import json
import logging

import anthropic
from pydantic import ValidationError

from app.llm.base import (
    LLMError,
    LLMMessage,
    LLMOutputError,
    LLMProvider,
    LLMRateLimitError,
    LLMRefusalError,
    LLMResult,
    LLMTruncatedError,
    LLMUsage,
    T,
)
from app.llm.schema import strict_json_schema

logger = logging.getLogger(__name__)

SERVER_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        model: str,
        effort: str,
        max_output_tokens: int,
        server_fallbacks: bool = True,
        api_key: str | None = None,
    ):
        # api_key=None resolves credentials from the environment (ANTHROPIC_API_KEY or an `ant auth login` profile).
        self.client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=3)
        self.model = model
        self.effort = effort
        self.max_output_tokens = max_output_tokens
        self.server_fallbacks = server_fallbacks

    async def generate_structured(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        output_type: type[T],
        max_tokens: int | None = None,
    ) -> LLMResult[T]:
        schema = strict_json_schema(output_type)
        try:
            message = await self._stream(system, messages, max_tokens, {"type": "json_schema", "schema": schema})
        except anthropic.BadRequestError as e:
            if "schema" not in str(e.message).lower():
                raise LLMError(f"Anthropic rejected the request: {e.message}") from e
            # Constrained decoding rejected the schema (e.g. too complex): fall back to prompted JSON,
            # which is still validated against the same Pydantic model below.
            logger.warning("structured output schema rejected, using prompted JSON: %s", e.message)
            prompted = (
                f"{system}\n\nRespond with a single JSON object and nothing else. It must validate against "
                f"this JSON schema:\n{json.dumps(schema)}"
            )
            try:
                message = await self._stream(prompted, messages, max_tokens, None)
            except anthropic.BadRequestError as e2:
                raise LLMError(f"Anthropic rejected the request: {e2.message}") from e2

        logger.info(
            "anthropic call model=%s stop=%s in=%s out=%s request_id=%s",
            message.model,
            message.stop_reason,
            message.usage.input_tokens,
            message.usage.output_tokens,
            getattr(message, "_request_id", None),
        )
        if message.stop_reason == "refusal":
            raise LLMRefusalError("The model declined to process this request")
        if message.stop_reason == "max_tokens":
            raise LLMTruncatedError("The model ran out of output tokens; reduce input or raise the limit")

        text = "".join(b.text for b in message.content if b.type == "text").strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        try:
            output = output_type.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError) as e:
            raise LLMOutputError(f"Model output did not match the schema: {e}") from e
        usage = LLMUsage(message.usage.input_tokens, message.usage.output_tokens)
        return LLMResult(output=output, model=message.model, usage=usage)

    async def _stream(self, system: str, messages: list[LLMMessage], max_tokens: int | None, fmt: dict | None):
        output_config: dict = {"effort": self.effort}
        if fmt is not None:
            output_config["format"] = fmt
        kwargs: dict = {}
        if self.server_fallbacks:
            # On a safety-classifier decline the API re-runs the request on Anthropic's recommended model.
            kwargs = {"betas": [SERVER_FALLBACK_BETA], "fallbacks": "default"}
        try:
            async with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=max_tokens or self.max_output_tokens,
                system=system,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                thinking={"type": "adaptive"},
                output_config=output_config,
                **kwargs,
            ) as stream:
                return await stream.get_final_message()
        except anthropic.BadRequestError:
            raise
        except anthropic.RateLimitError as e:
            try:
                retry_after = float(e.response.headers.get("retry-after", ""))
            except (TypeError, ValueError):
                retry_after = None
            raise LLMRateLimitError("Anthropic rate limit reached", retry_after) from e
        except anthropic.AuthenticationError as e:
            raise LLMError("Anthropic credentials are missing or invalid") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Anthropic API error ({e.status_code})") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Could not reach the Anthropic API") from e
