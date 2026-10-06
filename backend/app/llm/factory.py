"""Builds LLM providers from in-app (per-organisation) settings, falling back to server env defaults."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import decrypt_secret
from app.db.models import LLMCredential, Organization
from app.llm.base import LLMError, LLMProvider

EFFORTS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class ProviderInfo:
    key: str
    label: str
    needs_key: bool
    default_model: str
    suggested_models: tuple[str, ...]
    supports_effort: bool = False
    key_url: str | None = None


def catalog(settings: Settings | None = None) -> dict[str, ProviderInfo]:
    s = settings or get_settings()
    return {
        "anthropic": ProviderInfo(
            "anthropic",
            "Anthropic Claude",
            True,
            s.anthropic_model,
            ("claude-opus-5", "claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5"),
            supports_effort=True,
            key_url="https://platform.claude.com/settings/keys",
        ),
        "openai": ProviderInfo(
            "openai", "OpenAI", True, s.openai_model, ("gpt-5",), key_url="https://platform.openai.com/api-keys"
        ),
        "grok": ProviderInfo("grok", "xAI Grok", True, s.grok_model, ("grok-4.7",), key_url="https://console.x.ai"),
        "groq": ProviderInfo(
            "groq",
            "Groq",
            True,
            s.groq_model,
            # Models Groq documents as supporting strict json_schema output.
            ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"),
            key_url="https://console.groq.com/keys",
        ),
        "mock": ProviderInfo("mock", "Offline demo (no AI)", False, "mock", ("mock",)),
    }


def build_provider(
    provider: str,
    *,
    api_key: str | None,
    model: str | None = None,
    effort: str | None = None,
    settings: Settings | None = None,
) -> LLMProvider:
    s = settings or get_settings()
    info = catalog(s).get(provider)
    if info is None:
        raise LLMError(f"Unknown AI provider '{provider}'")
    model = model or info.default_model
    if provider == "anthropic":
        from app.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            model=model,
            effort=effort or s.anthropic_effort,
            max_output_tokens=s.llm_max_output_tokens,
            server_fallbacks=s.anthropic_server_fallbacks,
            api_key=api_key,
        )
    if provider == "openai":
        from app.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(model=model, max_output_tokens=s.llm_max_output_tokens, api_key=api_key)
    if provider == "grok":
        from app.llm.openai_provider import OpenAIProvider

        if not api_key:
            raise LLMError("No xAI API key configured. Add one in Settings > AI provider.")
        return OpenAIProvider(
            model=model,
            max_output_tokens=s.llm_max_output_tokens,
            name="grok",
            api_key=api_key,
            base_url=s.xai_base_url,
            token_param="max_tokens",
        )
    if provider == "groq":
        from app.llm.openai_provider import OpenAIProvider

        if not api_key:
            raise LLMError("No Groq API key configured. Add one in Settings > AI provider.")
        return OpenAIProvider(
            model=model,
            max_output_tokens=min(s.llm_max_output_tokens, s.groq_max_output_tokens),
            name="groq",
            api_key=api_key,
            base_url=s.groq_base_url,
            # gpt-oss reasoning tokens count against the output allowance; keep them lean.
            reasoning_effort="low" if model.startswith("openai/gpt-oss") else None,
        )
    from app.llm.mock_provider import MockProvider

    return MockProvider()


def get_provider(settings: Settings | None = None) -> LLMProvider:
    """Server default provider from environment variables."""
    s = settings or get_settings()
    # Anthropic/OpenAI SDKs read their own env vars when api_key is None.
    api_key = {"grok": s.xai_api_key, "groq": s.groq_api_key}.get(s.llm_provider)
    return build_provider(s.llm_provider, api_key=api_key, settings=s)


async def get_provider_for_org(session: AsyncSession, org_id: str) -> LLMProvider:
    """Provider chosen in the app for this organisation, or the server default if none is chosen."""
    org = await session.get(Organization, org_id)
    if org is None or not org.llm_provider:
        return get_provider()
    info = catalog().get(org.llm_provider)
    if info is None:
        raise LLMError(f"Unknown AI provider '{org.llm_provider}'")
    cred = await session.scalar(
        select(LLMCredential).where(LLMCredential.org_id == org_id, LLMCredential.provider == org.llm_provider)
    )
    if info.needs_key and not (cred and cred.api_key_encrypted):
        raise LLMError(f"No API key saved for {info.label}. Add one in Settings > AI provider.")
    api_key = decrypt_secret(cred.api_key_encrypted) if cred and cred.api_key_encrypted else None
    return build_provider(
        org.llm_provider,
        api_key=api_key,
        model=cred.model if cred else None,
        effort=cred.effort if cred else None,
    )
