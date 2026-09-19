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
class Image:
    """A picture to show the model: the label it knows the picture by, such as "Photo 1",
    and the picture's key in the file store."""

    label: str
    key: str


@dataclass(frozen=True)
class LoadedImage:
    """An Image read from the file store, ready to send."""

    label: str
    key: str
    media_type: str
    data: bytes


@dataclass(frozen=True)
class ModelRequest[Out: BaseModel]:
    purpose: str
    model: str
    instructions: str
    handoff: Handoff
    output: type[Out]
    images: tuple[LoadedImage, ...] = ()


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


class PortraitHandoff(Handoff):
    prompt: str = Field(min_length=1)


class VoiceDesignHandoff(Handoff):
    description: str = Field(min_length=1)
    sample: str = Field(min_length=1)


class SpeechHandoff(Handoff):
    voice_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


@dataclass(frozen=True)
class Picture:
    """A picture a model drew, and the tokens it was billed for."""

    data: bytes
    input_tokens: int
    output_tokens: int


class PictureProvider(Protocol):
    """Draws pictures. Raises OutsideServiceDown for errors worth retrying."""

    name: str

    def draw(self, *, model: str, prompt: str) -> Picture: ...


class VoiceProvider(Protocol):
    """Designs voices and speaks with them. Raises OutsideServiceDown for errors worth
    retrying."""

    name: str

    def design_voice(self, *, model: str, description: str, sample: str) -> str:
        """Design a voice from a description, hearing it say `sample`. Returns its id."""
        ...

    def speak(self, *, model: str, voice_id: str, text: str) -> bytes:
        """Say `text` in the voice. Returns the audio as a WAV file."""
        ...
