"""The one interface every model provider implements, so Claude and Gemini are interchangeable.

A provider's only job is transport: send a prompt, get back an object that fits the schema
(shape). Checking meaning (IDs exist, quotes are verbatim) happens later in verify.py.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class InvalidOutputError(RuntimeError):
    """The model gave no usable answer: failed validation, refused, or was cut off.

    Callers count these toward the "fails twice -> review manually" rule (P1).
    `log` is set when the call still returned token counts, so a billed failure is priced.
    """

    def __init__(self, message: str, log: "CallLog | None" = None):
        super().__init__(message)
        self.log = log


@dataclass(frozen=True)
class CallLog:
    """What the call that produced this answer cost. Feeds the run log (pipeline step 6).

    On a cache hit, tokens and latency are the ORIGINAL call's (so evals report real cost per
    posting), and `cached` is True: nothing was spent this time.
    """

    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    cached: bool = False  # True when the answer came from the result cache (no API call, $0 now)


@dataclass(frozen=True)
class ProviderResult(Generic[T]):
    output: T
    log: CallLog


class Provider(ABC):
    name: str

    @abstractmethod
    def generate(self, *, system: str, user: str, schema: type[T]) -> ProviderResult[T]:
        """Return an instance of `schema`, or raise InvalidOutputError."""
