from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

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


class Said(Handoff):
    """Something said in an agent's conversation: by the user, or by the agent itself."""

    kind: Literal["said"] = "said"
    by: Literal["user", "agent"]
    text: str


class ToolUse(Handoff):
    """A tool the agent called earlier in its conversation, and what the tool handed back."""

    kind: Literal["tool_use"] = "tool_use"
    call_id: str
    tool: str
    arguments: dict[str, Any]
    result: str


class StepFinished(Handoff):
    """Work a tool started in the background finished or failed: what the agent is told,
    by the system rather than the user."""

    kind: Literal["step_finished"] = "step_finished"
    text: str


# One thing in an agent's conversation.
type Happened = Said | ToolUse | StepFinished


class TurnHandoff(Handoff):
    """What an agent is given for one turn: its conversation so far, and the names of the
    tools it may call."""

    conversation: list[Happened]
    tools: list[str]


@dataclass(frozen=True)
class ToolSpec:
    """A tool offered to an agent: its name, what it does in words the model reads, and the
    shape of the arguments the model fills in."""

    name: str
    description: str
    arguments: type[BaseModel]


@dataclass(frozen=True)
class TurnRequest:
    purpose: str
    model: str
    instructions: str
    handoff: TurnHandoff
    tools: tuple[ToolSpec, ...]


@dataclass(frozen=True)
class ToolRequest:
    """A tool the agent asked for in its turn. `call_id` pairs the result with the request."""

    call_id: str
    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Turn:
    """What an agent did with one turn: what it said, and the tools it asked for. A turn
    that asks for no tool is its reply."""

    says: str
    calls: tuple[ToolRequest, ...]


@dataclass(frozen=True)
class TurnReply:
    turn: Turn
    input_tokens: int
    output_tokens: int


class AgentProvider(Protocol):
    """Runs agents' turns. Raises OutsideServiceDown for errors worth retrying, and
    UnusableReply for an answer that was billed but can't be used."""

    name: str

    def take_turn(self, request: TurnRequest) -> TurnReply: ...


class PortraitHandoff(Handoff):
    prompt: str = Field(min_length=1)


class PictureEditHandoff(Handoff):
    """A picture to make from other pictures: what to make, and each picture's key in the
    file store, in the order the prompt refers to them."""

    prompt: str = Field(min_length=1)
    pictures: list[str] = Field(min_length=1)


class VoiceDesignHandoff(Handoff):
    description: str = Field(min_length=1)
    sample: str = Field(min_length=1)


class SpeechHandoff(Handoff):
    voice_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class TranscriptionHandoff(Handoff):
    """Audio to transcribe, by its key in the file store."""

    audio: str = Field(min_length=1)


@dataclass(frozen=True)
class Picture:
    """A picture a model drew, and the tokens it was billed for. Of the input tokens,
    `picture_input_tokens` were pictures it was given, which cost more than words."""

    data: bytes
    input_tokens: int
    output_tokens: int
    picture_input_tokens: int = 0


class PictureProvider(Protocol):
    """Draws pictures. Raises OutsideServiceDown for errors worth retrying."""

    name: str

    def draw(self, *, model: str, prompt: str) -> Picture: ...

    def edit(self, *, model: str, prompt: str, pictures: Sequence[bytes]) -> Picture:
        """Make a picture from `pictures`, as `prompt` says."""
        ...


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


@dataclass(frozen=True)
class Word:
    """One word heard, and when it was said, in seconds from the start of the audio."""

    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Transcription:
    """What was heard in some audio, word by word, exactly as it was said. `audio_seconds`
    is how long the audio was, which is what transcription is billed by."""

    text: str
    words: tuple[Word, ...]
    audio_seconds: float


class TranscriptionProvider(Protocol):
    """Hears audio and writes down what was said. Raises OutsideServiceDown for errors worth
    retrying."""

    name: str

    def transcribe(self, *, model: str, audio: bytes) -> Transcription:
        """Write down every word said in `audio`, a WAV file, with when it was said."""
        ...
