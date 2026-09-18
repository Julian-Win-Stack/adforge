from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class Handoff(BaseModel):
    """What one part of the system hands a model. Strict, so a decimal where a whole
    number belongs, or a missing field, fails before any money is spent."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class Judgement(BaseModel):
    """Every judgement is a decision plus one sentence saying why, written for a person.
    Subclasses narrow `decision` to the allowed choices."""

    decision: str
    reason: str = Field(description="One sentence saying why, written for a person to read.")


@dataclass(frozen=True)
class ModelRequest[Out: BaseModel]:
    purpose: str
    model: str
    instructions: str
    handoff: Handoff
    output: type[Out]


@dataclass(frozen=True)
class ModelReply[Out: BaseModel]:
    output: Out
    input_tokens: int
    output_tokens: int


class UnusableReply(Exception):
    """The provider answered, and billed for it, but the answer can't be used: a refusal,
    or output cut off before it was complete."""

    def __init__(self, message: str, *, input_tokens: int, output_tokens: int) -> None:
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class ModelProvider(Protocol):
    """Talks to one model provider. Raises OutsideServiceDown for errors worth retrying,
    and UnusableReply for an answer that was billed but can't be used."""

    name: str

    def complete[Out: BaseModel](self, request: ModelRequest[Out]) -> ModelReply[Out]: ...
