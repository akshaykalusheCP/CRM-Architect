from types import SimpleNamespace

import anthropic
import httpx2 as httpx
import pytest

from app.domain.business_model import DiscoveryResult
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMMessage, LLMRefusalError
from tests.factories import solar_model


def _message(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(
        model="claude-opus-5",
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)],
    )


def _bad_request(msg: str) -> anthropic.BadRequestError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError(msg, response=httpx.Response(400, request=request), body=None)


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    return AnthropicProvider(model="claude-opus-5", effort="high", max_output_tokens=1000)


async def test_schema_rejection_falls_back_to_prompted_json(provider, monkeypatch):
    payload = DiscoveryResult(summary="ok", model=solar_model()).model_dump_json()
    calls = []

    async def fake_stream(system, messages, max_tokens, fmt):
        calls.append(fmt)
        if fmt is not None:
            raise _bad_request("output_config.format.schema: schema is too complex")
        return _message(f"```json\n{payload}\n```")

    monkeypatch.setattr(provider, "_stream", fake_stream)
    result = await provider.generate_structured(
        system="s", messages=[LLMMessage("user", "hi")], output_type=DiscoveryResult
    )
    assert result.output.summary == "ok"
    assert calls[0]["type"] == "json_schema" and calls[1] is None


async def test_refusal_is_surfaced(provider, monkeypatch):
    async def fake_stream(*args):
        return _message("", stop_reason="refusal")

    monkeypatch.setattr(provider, "_stream", fake_stream)
    with pytest.raises(LLMRefusalError):
        await provider.generate_structured(system="s", messages=[LLMMessage("user", "hi")], output_type=DiscoveryResult)
