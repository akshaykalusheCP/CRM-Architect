from types import SimpleNamespace

import httpx
import openai
import pytest

from app.core.config import Settings
from app.domain.business_model import DiscoveryResult
from app.llm.base import LLMError, LLMMessage
from app.llm.factory import get_provider
from tests.factories import solar_model


def _response(text: str, finish_reason: str = "stop"):
    return SimpleNamespace(
        model="grok-4.7",
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        choices=[SimpleNamespace(finish_reason=finish_reason, message=SimpleNamespace(content=text, refusal=None))],
    )


def test_factory_builds_grok_provider():
    provider = get_provider(Settings(llm_provider="grok", xai_api_key="xai-test"))
    assert provider.name == "grok"
    assert str(provider.client.base_url).startswith("https://api.x.ai/v1")
    assert provider.token_param == "max_tokens"


def test_factory_requires_xai_key():
    with pytest.raises(LLMError, match="xAI API key"):
        get_provider(Settings(llm_provider="grok", xai_api_key=None))


async def test_schema_rejection_falls_back_to_json_mode(monkeypatch):
    provider = get_provider(Settings(llm_provider="grok", xai_api_key="xai-test"))
    payload = DiscoveryResult(summary="ok", model=solar_model()).model_dump_json()
    formats = []

    async def fake_create(system, chat, response_format, max_tokens):
        formats.append(response_format["type"])
        if response_format["type"] == "json_schema":
            request = httpx.Request("POST", "https://api.x.ai/v1/chat/completions")
            raise openai.BadRequestError(
                "Invalid response_format schema", response=httpx.Response(400, request=request), body=None
            )
        assert "JSON schema" in system
        return _response(payload)

    monkeypatch.setattr(provider, "_create", fake_create)
    result = await provider.generate_structured(
        system="s", messages=[LLMMessage("user", "hi")], output_type=DiscoveryResult
    )
    assert result.output.summary == "ok" and formats == ["json_schema", "json_object"]


def test_factory_builds_groq_provider():
    provider = get_provider(Settings(llm_provider="groq", groq_api_key="gsk-test"))
    assert provider.name == "groq"
    assert str(provider.client.base_url).startswith("https://api.groq.com/openai/v1")
    assert provider.model == "openai/gpt-oss-120b"
    assert provider.token_param == "max_completion_tokens"
    assert provider.max_output_tokens == 32_000


async def test_provider_error_message_is_passed_through(monkeypatch):
    provider = get_provider(Settings(llm_provider="groq", groq_api_key="gsk-test"))
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    body = {
        "error": {"message": "Request too large: tokens per minute (TPM): Limit 8000", "code": "rate_limit_exceeded"}
    }

    async def fail(**kwargs):
        raise openai.APIStatusError("413", response=httpx.Response(413, request=request), body=body)

    monkeypatch.setattr(provider.client.chat.completions, "create", fail)
    with pytest.raises(LLMError, match=r"\(413\): Request too large: tokens per minute"):
        await provider.generate_structured(system="s", messages=[LLMMessage("user", "hi")], output_type=DiscoveryResult)


async def test_groq_truncation_is_reported_as_truncation(monkeypatch):
    from app.llm.base import LLMTruncatedError

    provider = get_provider(Settings(llm_provider="groq", groq_api_key="gsk-test"))
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    msg = (
        "Failed to validate JSON: max completion tokens reached before generating a valid document: "
        "the output was truncated to fit max_completion_tokens"
    )

    async def fail(**kwargs):
        raise openai.BadRequestError(msg, response=httpx.Response(400, request=request), body=None)

    monkeypatch.setattr(provider.client.chat.completions, "create", fail)
    with pytest.raises(LLMTruncatedError):
        await provider.generate_structured(system="s", messages=[LLMMessage("user", "hi")], output_type=DiscoveryResult)


def test_coerce_to_schema_repairs_small_deviations():
    from app.llm.schema import coerce_to_schema, strict_json_schema
    from app.pipeline.staged import ExtrasStage, OutlineStage

    extras = {
        "integrations": [
            {
                "key": "erp",
                "system": "SAP",
                "direction": "bidirectional",
                "entities": ["order"],
                "frequency": "hourly",
                "description": "d",
                "provenance": {"source": "assumption", "reference": "x", "confidence": 0.8},
            },
        ],
        "reports": [
            {
                "key": "r",
                "name": "R",
                "entity": "order",
                "kind": "chart",
                "group_by": [],
                "metrics": [],
                "filters": None,
                "audience": [],
                "surprise": "extra key",
            }
        ],
        "assumptions": [{"key": "not-in-schema", "statement": "s", "rationale": None, "confidence": 0.7}],
    }
    fixed = ExtrasStage.model_validate(coerce_to_schema(extras, strict_json_schema(ExtrasStage)))
    assert fixed.integrations[0].provenance.source == "inferred"
    assert fixed.assumptions[0].related == []

    outline = {
        "summary": "s",
        "profile": {"name": "n", "industry": "i", "description": "d"},
        "changes": [],
        "entities": [
            {
                "key": "e",
                "label": "E",
                "plural_label": "Es",
                "kind": "custom_object",
                "description": "d",
                "provenance": {"source": "narrative", "confidence": 1},
            }
        ],
        "questions": [
            {
                "key": "q",
                "question": "?",
                "why": "w",
                "category": "process",
                "priority": "urgent",
                "provenance": {"source": "x"},
            }
        ],
    }
    fixed = OutlineStage.model_validate(coerce_to_schema(outline, strict_json_schema(OutlineStage)))
    assert fixed.entities[0].kind == "custom" and fixed.questions[0].priority == "medium"


async def test_groq_rejected_output_is_salvaged(monkeypatch):
    import json

    from app.pipeline.staged import ExtrasStage

    provider = get_provider(Settings(llm_provider="groq", groq_api_key="gsk-test"))
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    generated = {"integrations": [], "reports": [], "assumptions": [{"statement": "s", "confidence": 0.5, "junk": 1}]}
    body = {
        "error": {
            "message": "Generated JSON does not match the expected schema. See 'failed_generation'",
            "code": "json_validate_failed",
            "failed_generation": json.dumps(generated),
        }
    }
    calls = []

    async def fail(**kwargs):
        calls.append(1)
        raise openai.BadRequestError("bad", response=httpx.Response(400, request=request), body=body)

    monkeypatch.setattr(provider.client.chat.completions, "create", fail)
    result = await provider.generate_structured(
        system="s", messages=[LLMMessage("user", "hi")], output_type=ExtrasStage
    )
    assert result.output.assumptions[0].statement == "s" and len(calls) == 1  # no retry needed
