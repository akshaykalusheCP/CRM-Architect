"""In-app AI provider configuration (per organisation). Owners/admins can change it; everyone can view."""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import SessionDep, UserDep
from app.core.config import get_settings
from app.core.security import decrypt_secret, encrypt_secret
from app.db.models import LLMCredential, Organization, User, UserRole
from app.llm.base import LLMError, LLMMessage
from app.llm.factory import EFFORTS, build_provider, catalog

router = APIRouter(prefix="/settings/llm", tags=["settings"])
logger = logging.getLogger(__name__)


def require_admin(user: UserDep) -> User:
    if user.role not in (UserRole.owner, UserRole.admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only workspace owners and admins can change AI settings")
    return user


AdminDep = Annotated[User, Depends(require_admin)]


class ProviderUpdate(BaseModel):
    api_key: str | None = Field(default=None, max_length=500, description="Omit to keep the saved key.")
    model: str | None = Field(default=None, max_length=120)
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None


class ActiveUpdate(BaseModel):
    provider: str | None = Field(description="Provider key, or null to use the server default.")


class TestRequest(BaseModel):
    provider: str
    api_key: str | None = Field(default=None, max_length=500, description="Test an unsaved key.")
    model: str | None = Field(default=None, max_length=120)


class _Ping(BaseModel):
    ok: bool
    reply: str


def _check_provider(key: str) -> None:
    if key not in catalog():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown provider '{key}'")


def _hint(key: str) -> str:
    return f"…{key[-4:]}" if len(key) > 8 else "…"


async def _creds(session, org_id: str) -> dict[str, LLMCredential]:
    rows = (await session.scalars(select(LLMCredential).where(LLMCredential.org_id == org_id))).all()
    return {c.provider: c for c in rows}


async def _state(session, user: User) -> dict:
    settings = get_settings()
    org = await session.get(Organization, user.org_id)
    creds = await _creds(session, user.org_id)
    providers = []
    for info in catalog().values():
        c = creds.get(info.key)
        providers.append(
            {
                "key": info.key,
                "label": info.label,
                "needs_key": info.needs_key,
                "key_url": info.key_url,
                "configured": bool(c and c.api_key_encrypted) or not info.needs_key,
                "key_hint": c.key_hint if c else None,
                "model": (c.model if c else None) or info.default_model,
                "default_model": info.default_model,
                "suggested_models": list(info.suggested_models),
                "supports_effort": info.supports_effort,
                "effort": (c.effort if c else None) or (settings.anthropic_effort if info.supports_effort else None),
                "updated_at": c.updated_at if c else None,
            }
        )
    return {
        "active_provider": org.llm_provider if org else None,
        "server_default": {"provider": settings.llm_provider, "label": catalog()[settings.llm_provider].label},
        "efforts": list(EFFORTS),
        "can_edit": user.role in (UserRole.owner, UserRole.admin),
        "providers": providers,
    }


@router.get("")
async def get_llm_settings(user: UserDep, session: SessionDep):
    return await _state(session, user)


@router.put("/providers/{provider}")
async def update_provider(provider: str, body: ProviderUpdate, user: AdminDep, session: SessionDep):
    _check_provider(provider)
    cred = (await _creds(session, user.org_id)).get(provider)
    if cred is None:
        cred = LLMCredential(org_id=user.org_id, provider=provider)
        session.add(cred)
    data = body.model_dump(exclude_unset=True)
    if data.get("api_key"):
        key = data["api_key"].strip()
        cred.api_key_encrypted, cred.key_hint = encrypt_secret(key), _hint(key)
        org = await session.get(Organization, user.org_id)
        if org is not None and org.llm_provider is None:
            # First key saved while on the server default: use it, rather than silently staying on the default.
            org.llm_provider = provider
    if "model" in data:
        cred.model = (data["model"] or "").strip() or None
    if "effort" in data:
        cred.effort = data["effort"] if catalog()[provider].supports_effort else None
    cred.updated_by = user.id
    await session.commit()
    logger.info("llm settings updated org=%s provider=%s by=%s", user.org_id, provider, user.id)
    return await _state(session, user)


@router.delete("/providers/{provider}/key")
async def delete_key(provider: str, user: AdminDep, session: SessionDep):
    _check_provider(provider)
    cred = (await _creds(session, user.org_id)).get(provider)
    if cred:
        cred.api_key_encrypted = cred.key_hint = None
        cred.updated_by = user.id
    org = await session.get(Organization, user.org_id)
    if org and org.llm_provider == provider and catalog()[provider].needs_key:
        org.llm_provider = None  # don't leave the workspace pointing at a provider with no key
    await session.commit()
    return await _state(session, user)


@router.put("/active")
async def set_active(body: ActiveUpdate, user: AdminDep, session: SessionDep):
    org = await session.get(Organization, user.org_id)
    assert org is not None
    if body.provider is not None:
        _check_provider(body.provider)
        info = catalog()[body.provider]
        cred = (await _creds(session, user.org_id)).get(body.provider)
        if info.needs_key and not (cred and cred.api_key_encrypted):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Save an API key for {info.label} first")
    org.llm_provider = body.provider
    await session.commit()
    return await _state(session, user)


@router.post("/test")
async def test_provider(body: TestRequest, user: AdminDep, session: SessionDep):
    _check_provider(body.provider)
    cred = (await _creds(session, user.org_id)).get(body.provider)
    api_key = body.api_key.strip() if body.api_key else None
    try:
        if api_key is None and cred and cred.api_key_encrypted:
            api_key = decrypt_secret(cred.api_key_encrypted)
        if catalog()[body.provider].needs_key and not api_key:
            return {"ok": False, "message": "Enter an API key to test"}
        if body.provider == "mock":
            return {"ok": True, "message": "Offline demo mode needs no key", "model": "mock"}
        provider = build_provider(
            body.provider, api_key=api_key, model=body.model or (cred.model if cred else None), effort="low"
        )
        result = await provider.generate_structured(
            system="You are a connectivity check.",
            messages=[LLMMessage("user", 'Reply with ok=true and reply="pong".')],
            output_type=_Ping,
            max_tokens=4000,
        )
    except (LLMError, ValueError) as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": result.output.ok, "message": f"Connected to {result.model}", "model": result.model}
