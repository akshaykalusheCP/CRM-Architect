from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    app_name: str = "CRM Architect"
    api_prefix: str = "/api"

    database_url: str = "postgresql+asyncpg://crm:crm@localhost:5432/crm_architect"
    redis_url: str = "redis://localhost:6379/0"
    # "arq" runs jobs on the Redis-backed worker; "inline" runs them in the API process (dev/tests only).
    job_backend: Literal["arq", "inline"] = "arq"

    secret_key: str = Field(default="change-me-in-production", min_length=16)
    # Fernet key for API keys stored in the database. Falls back to a key derived from SECRET_KEY;
    # set it explicitly so rotating SECRET_KEY does not make stored keys unreadable.
    encryption_key: str | None = None
    access_token_ttl_minutes: int = 60 * 12
    cookie_secure: bool = False
    cors_origins: list[str] = ["http://localhost:3000"]

    data_dir: Path = Path("./data")
    max_upload_mb: int = 25
    max_profile_rows: int = 50_000
    max_document_chars: int = 60_000

    # LLM
    llm_provider: Literal["anthropic", "openai", "grok", "groq", "mock"] = "anthropic"
    anthropic_model: str = "claude-opus-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    anthropic_server_fallbacks: bool = True
    openai_model: str = "gpt-5"
    # xAI Grok (OpenAI-compatible API)
    xai_api_key: str | None = None
    grok_model: str = "grok-4.7"
    xai_base_url: str = "https://api.x.ai/v1"
    # Groq (OpenAI-compatible API; not the same company as xAI Grok)
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_max_output_tokens: int = 32_000
    llm_max_output_tokens: int = 64_000
    llm_repair_attempts: int = 1
    # Mask e-mail / phone sample values before they are sent to the LLM.
    llm_mask_pii_samples: bool = True

    salesforce_api_version: str = "62.0"

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.is_production and settings.secret_key == "change-me-in-production":
        raise RuntimeError("SECRET_KEY must be set in production")
    return settings
