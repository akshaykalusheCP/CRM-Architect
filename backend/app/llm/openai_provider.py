"""OpenAI Chat Completions provider. Also serves OpenAI-compatible APIs such as xAI Grok."""

import json
import logging

import openai
from pydantic import ValidationError

from app.llm.base import (
    LLMError,
    LLMMessage,
    LLMOutputError,
    LLMProvider,
    LLMRateLimitError,
    LLMRefusalError,
    LLMResult,
    LLMTimeoutError,
    LLMTruncatedError,
    LLMUsage,
    T,
)
from app.llm.schema import coerce_to_schema, strict_json_schema

logger = logging.getLogger(__name__)


def _detail(e: openai.APIStatusError) -> str:
    """The provider's own error message, which usually says exactly what to fix."""
    body = e.body if isinstance(e.body, dict) else {}
    err = body.get("error", body)
    message = err.get("message") if isinstance(err, dict) else None
    return str(message or e.message)[:500]


def _salvage(e: openai.APIStatusError, output_type: type[T], schema: dict) -> T | None:
    """Groq returns the rejected output in `failed_generation`; repair small schema deviations in it."""
    body = e.body if isinstance(e.body, dict) else {}
    err = body.get("error", body) if isinstance(body.get("error", body), dict) else {}
    raw = err.get("failed_generation")
    if not raw:
        return None
    try:
        return output_type.model_validate(coerce_to_schema(json.loads(raw), schema))
    except (json.JSONDecodeError, ValidationError, TypeError, KeyError):
        return None  # truncated or badly broken: let the caller retry or split


def _retry_after(e: openai.APIStatusError) -> float | None:
    try:
        return float(e.response.headers.get("retry-after", ""))
    except (TypeError, ValueError):
        return None


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        model: str,
        max_output_tokens: int,
        *,
        name: str = "openai",
        api_key: str | None = None,
        base_url: str | None = None,
        token_param: str = "max_completion_tokens",
        reasoning_effort: str | None = None,
        timeout: float = 180.0,
    ):
        # api_key=None lets the SDK read OPENAI_API_KEY from the environment.
        # Structured steps normally answer in seconds; without a timeout a stalled request would sit for the
        # SDK default of 10 minutes (times retries) and hang the whole analysis.
        self.client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=2, timeout=timeout)
        self.name = name
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.token_param = token_param
        self.reasoning_effort = reasoning_effort
        self._token_limit: int | None | bool = False  # False = not detected yet

    async def generate_structured(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        output_type: type[T],
        max_tokens: int | None = None,
    ) -> LLMResult[T]:
        schema = strict_json_schema(output_type)
        chat = [{"role": m.role, "content": m.content} for m in messages]
        strict_format = {
            "type": "json_schema",
            "json_schema": {"name": output_type.__name__, "schema": schema, "strict": True},
        }
        try:
            response = await self._create(system, chat, strict_format, max_tokens)
        except openai.BadRequestError as e:
            message = str(e.message).lower()
            if "max completion tokens reached" in message or "output was truncated" in message:
                # Groq reports running out of output room as a JSON validation failure.
                raise LLMTruncatedError(f"{self.name} ran out of output tokens") from e
            salvaged = _salvage(e, output_type, schema)
            if salvaged is not None:
                logger.info("%s output repaired against the schema instead of retrying", self.name)
                return LLMResult(output=salvaged, model=self.model, usage=LLMUsage())
            if "failed to validate json" in message or "failed_generation" in message:
                # Constrained decoding produced output that did not validate (e.g. ran out of tokens).
                raise LLMOutputError(f"{self.name} could not produce valid JSON: {_detail(e)}") from e
            if "schema" not in message and "response_format" not in message:
                raise LLMError(f"{self.name} rejected the request: {e.message}") from e
            # Schema not accepted for constrained decoding: fall back to JSON mode + schema in the prompt.
            logger.warning("%s rejected the strict schema, using JSON mode: %s", self.name, e.message)
            prompted = (
                f"{system}\n\nRespond with a single JSON object and nothing else. It must validate against "
                f"this JSON schema:\n{json.dumps(schema)}"
            )
            try:
                response = await self._create(prompted, chat, {"type": "json_object"}, max_tokens)
            except openai.BadRequestError as e2:
                raise LLMError(f"{self.name} rejected the request: {e2.message}") from e2

        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise LLMRefusalError(choice.message.refusal)
        if choice.finish_reason == "length":
            raise LLMTruncatedError("The model ran out of output tokens")
        text = (choice.message.content or "").strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        try:
            output = output_type.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError) as e:
            raise LLMOutputError(f"Model output did not match the schema: {e}") from e
        usage = LLMUsage(
            response.usage.prompt_tokens if response.usage else 0,
            response.usage.completion_tokens if response.usage else 0,
        )
        logger.info("%s call model=%s in=%s out=%s", self.name, response.model, usage.input_tokens, usage.output_tokens)
        return LLMResult(output=output, model=response.model, usage=usage)

    async def token_limit(self) -> int | None:
        if self._token_limit is False:
            # A tiny request reveals the account's tokens-per-minute limit in the response headers.
            try:
                raw = await self.client.chat.completions.with_raw_response.create(
                    model=self.model,
                    messages=[{"role": "user", "content": "hi"}],
                    **{self.token_param: 5},
                )
                value = raw.headers.get("x-ratelimit-limit-tokens", "")
                self._token_limit = int(value) if value.isdigit() else None
            except openai.APIError:
                self._token_limit = None
            logger.info("%s tokens-per-minute limit: %s", self.name, self._token_limit)
        return self._token_limit  # type: ignore[return-value]

    async def _create(self, system: str, chat: list[dict], response_format: dict, max_tokens: int | None):
        extra = {"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}
        try:
            return await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, *chat],
                response_format=response_format,
                **{self.token_param: max_tokens or self.max_output_tokens},
                **extra,
            )
        except openai.BadRequestError:
            raise
        except openai.AuthenticationError as e:
            raise LLMError(f"{self.name} API key is missing or invalid") from e
        except openai.RateLimitError as e:
            raise LLMRateLimitError(f"{self.name} rate limit reached: {_detail(e)}", _retry_after(e)) from e
        except openai.APIStatusError as e:
            # e.g. Groq returns 413 when one request exceeds the account's tokens-per-minute limit.
            raise LLMError(f"{self.name} API error ({e.status_code}): {_detail(e)}") from e
        except openai.APITimeoutError as e:
            raise LLMTimeoutError(f"{self.name} did not answer within the time limit") from e
        except openai.APIConnectionError as e:
            raise LLMError(f"Could not reach the {self.name} API") from e
