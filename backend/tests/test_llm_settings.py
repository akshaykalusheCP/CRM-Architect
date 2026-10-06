from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.core.security import hash_password
from app.db.models import LLMCredential, User, UserRole
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.factory import get_provider_for_org
from app.llm.mock_provider import MockProvider
from app.services.jobs import wait_for_inline_jobs

BASE = "/api/settings/llm"


async def test_default_state(auth_client):
    r = await auth_client.get(BASE)
    assert r.status_code == 200
    body = r.json()
    assert body["active_provider"] is None and body["can_edit"] is True
    assert body["server_default"]["provider"] == "mock"
    assert {p["key"] for p in body["providers"]} == {"anthropic", "openai", "grok", "groq", "mock"}


async def test_save_key_is_encrypted_and_never_returned(auth_client):
    r = await auth_client.put(f"{BASE}/providers/anthropic", json={"api_key": "sk-ant-secret-1234", "effort": "xhigh"})
    assert r.status_code == 200
    anthropic = next(p for p in r.json()["providers"] if p["key"] == "anthropic")
    assert anthropic["configured"] and anthropic["key_hint"] == "…1234" and anthropic["effort"] == "xhigh"
    assert "sk-ant-secret-1234" not in r.text

    async with get_sessionmaker()() as s:
        cred = await s.scalar(select(LLMCredential).where(LLMCredential.provider == "anthropic"))
        assert cred.api_key_encrypted and "sk-ant-secret" not in cred.api_key_encrypted
        org_id = cred.org_id

    # first saved key becomes the active provider; a model change keeps the existing key
    assert r.json()["active_provider"] == "anthropic"
    await auth_client.put(f"{BASE}/providers/anthropic", json={"model": "claude-sonnet-5"})

    async with get_sessionmaker()() as s:
        provider = await get_provider_for_org(s, org_id)
    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-sonnet-5" and provider.effort == "xhigh"
    assert provider.client.api_key == "sk-ant-secret-1234"


async def test_cannot_activate_without_key_and_delete_resets(auth_client):
    r = await auth_client.put(f"{BASE}/active", json={"provider": "grok"})
    assert r.status_code == 422
    r = await auth_client.put(f"{BASE}/providers/grok", json={"api_key": "xai-abcdefgh9999"})
    assert r.json()["active_provider"] == "grok"  # auto-activated
    r = await auth_client.delete(f"{BASE}/providers/grok/key")
    body = r.json()
    assert body["active_provider"] is None
    assert not next(p for p in body["providers"] if p["key"] == "grok")["configured"]


async def test_test_endpoint(auth_client):
    assert (await auth_client.post(f"{BASE}/test", json={"provider": "mock"})).json()["ok"] is True
    r = await auth_client.post(f"{BASE}/test", json={"provider": "openai"})
    assert r.json() == {"ok": False, "message": "Enter an API key to test"}


async def test_consultants_cannot_edit(auth_client):
    me = (await auth_client.get("/api/auth/me")).json()
    async with get_sessionmaker()() as s:
        s.add(
            User(
                org_id=me["org_id"],
                email="c@example.com",
                full_name="C",
                password_hash=hash_password("consultant-pw-123"),
                role=UserRole.consultant,
            )
        )
        await s.commit()
    auth_client.cookies.clear()
    await auth_client.post("/api/auth/login", json={"email": "c@example.com", "password": "consultant-pw-123"})
    auth_client.headers["X-CSRF-Token"] = auth_client.cookies["csrf_token"]
    assert (await auth_client.get(BASE)).json()["can_edit"] is False
    r = await auth_client.put(f"{BASE}/providers/openai", json={"api_key": "sk-x"})
    assert r.status_code == 403


async def test_analysis_uses_org_provider(auth_client):
    await auth_client.put(f"{BASE}/active", json={"provider": "mock"})
    pid = (
        await auth_client.post("/api/projects", json={"name": "P", "client_name": "C", "description": "We sell solar."})
    ).json()["id"]
    job = (await auth_client.post(f"/api/projects/{pid}/analyze")).json()
    await wait_for_inline_jobs()
    job = (await auth_client.get(f"/api/projects/{pid}/jobs/{job['id']}")).json()
    assert job["status"] == "succeeded"
    version = (await auth_client.get(f"/api/projects/{pid}/model")).json()
    assert version["llm_usage"]["provider"] == "mock"


async def test_missing_key_gives_actionable_job_error(auth_client):
    me = (await auth_client.get("/api/auth/me")).json()
    async with get_sessionmaker()() as s:
        from app.db.models import Organization

        org = await s.get(Organization, me["org_id"])
        org.llm_provider = "openai"  # active without a saved key (e.g. key removed out-of-band)
        await s.commit()
        assert not isinstance(await _safe(s, me["org_id"]), MockProvider)
    pid = (await auth_client.post("/api/projects", json={"name": "P", "client_name": "C", "description": "x"})).json()[
        "id"
    ]
    job = (await auth_client.post(f"/api/projects/{pid}/analyze")).json()
    await wait_for_inline_jobs()
    job = (await auth_client.get(f"/api/projects/{pid}/jobs/{job['id']}")).json()
    assert job["status"] == "failed" and "Settings" in job["error"]


async def _safe(session, org_id):
    try:
        return await get_provider_for_org(session, org_id)
    except Exception as exc:  # noqa: BLE001
        return exc
