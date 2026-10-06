from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Base error for provider failures that a job should surface to the user."""


class LLMRefusalError(LLMError):
    pass


class LLMTimeoutError(LLMError):
    """A request took longer than the provider timeout; usually worth one more try."""


class LLMRateLimitError(LLMError):
    """The provider's rate limit was hit even after the SDK's own retries; wait and try again."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class LLMTruncatedError(LLMError):
    pass


class LLMOutputError(LLMError):
    """The provider returned output that does not match the requested schema."""


@dataclass
class LLMMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "LLMUsage") -> "LLMUsage":
        return LLMUsage(self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens)


@dataclass
class LLMResult(Generic[T]):
    output: T
    model: str
    usage: LLMUsage = field(default_factory=LLMUsage)


class LLMProvider(ABC):
    name: str

    @abstractmethod
    async def generate_structured(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        output_type: type[T],
        max_tokens: int | None = None,
    ) -> LLMResult[T]:
        """Return an instance of ``output_type`` produced by the model."""

    async def token_limit(self) -> int | None:
        """Tokens-per-minute limit a single request must fit in, when the account enforces one.

        Requests are charged input + max output tokens up front, so small limits (free tiers) need
        the staged pipeline. None means no limit worth planning around.
        """
        return None
