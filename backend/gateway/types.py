from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


# The longest and shortest talking clips a handoff asks for.
MOST_CLIP_SECONDS = 20
LEAST_CLIP_SECONDS = 1


class ClipHandoff(Handoff):
    """A talking clip to make: the starting picture, by its key in the file store, how it
    should move, and how long it is. It says `audio`, also by its key, and is as long as
    it."""

    picture: str = Field(min_length=1)
    audio: str | None = Field(default=None, min_length=1)
    seconds: float = Field(ge=LEAST_CLIP_SECONDS, le=MOST_CLIP_SECONDS)
    motion_prompt: str = Field(min_length=1)


# Boreal-H3 makes B-roll clips of 5 to 15 whole seconds, and takes up to 5 example pictures
# for free (docs/broll-picture-logic.md, "What Boreal-H3 takes").
LEAST_BROLL_SECONDS = 5
MOST_BROLL_SECONDS = 15
MOST_EXAMPLE_PICTURES = 5


class BrollClipHandoff(Handoff):
    """A B-roll clip to make, with no sound, each picture by its key in the file store: from
    a starting picture, its exact first frame, or from example pictures that show how things
    look, never both. How long it is, in whole seconds, and the prompt saying what happens."""

    starting_picture: str | None = Field(default=None, min_length=1)
    example_pictures: list[Annotated[str, Field(min_length=1)]] = Field(
        default_factory=list, max_length=MOST_EXAMPLE_PICTURES
    )
    seconds: int = Field(ge=LEAST_BROLL_SECONDS, le=MOST_BROLL_SECONDS)
    prompt: str = Field(min_length=1)

    @model_validator(mode="after")
    def _made_one_way(self) -> Self:
        # The video service refuses both at once (docs/runs/second-run-review/check/).
        if self.starting_picture is not None and self.example_pictures:
            raise ValueError(
                "a B-roll clip is made from a starting picture or from example pictures, never both"
            )
        if self.starting_picture is None and not self.example_pictures:
            raise ValueError("a B-roll clip needs a starting picture or example pictures")
        return self


class ClipCollectHandoff(Handoff):
    """A clip asked for earlier, to wait for and fetch, by the id the video service gave it."""

    video_id: str = Field(min_length=1)


@dataclass(frozen=True)
class ClipStatus:
    """How a clip asked for is getting on. `video_url` is where a made clip can be fetched
    from, for a short while; `error` is why one couldn't be made."""

    state: Literal["working", "completed", "failed"]
    video_url: str | None = None
    error: str | None = None


class ClipFailed(Exception):
    """The video service couldn't make a clip. Asking again would pay for it again."""


class ClipCollector(Protocol):
    """Waits for clips a video service was asked for, then fetches them. Making one takes a
    while, so it is asked for, then waited for, then fetched. Raises OutsideServiceDown for
    errors worth retrying."""

    name: str

    def status(self, *, video_id: str) -> ClipStatus: ...

    def download(self, *, url: str) -> bytes: ...


class ClipProvider(ClipCollector, Protocol):
    """Makes talking clips from a picture, saying given audio."""

    def submit(
        self, *, picture: bytes, audio: bytes | None, seconds: float, motion_prompt: str
    ) -> str:
        """Ask for a `seconds`-long clip of the picture moving as `motion_prompt` says,
        speaking `audio`, a WAV file. Returns its id."""
        ...


class BrollClipProvider(ClipCollector, Protocol):
    """Makes B-roll clips, with no sound."""

    def submit_broll(
        self,
        *,
        starting_picture: bytes | None,
        example_pictures: Sequence[bytes],
        seconds: int,
        prompt: str,
    ) -> str:
        """Ask for a `seconds`-long clip as `prompt` says, starting on `starting_picture`, or
        with things looking as in `example_pictures`. Returns its id."""
        ...


class MusicHandoff(Handoff):
    """Background music to make: what it should sound like, and how many seconds of it."""

    prompt: str = Field(min_length=1)
    seconds: int = Field(ge=1, le=600)


class MusicProvider(Protocol):
    """Makes background music from a description. Raises OutsideServiceDown for errors worth
    retrying."""

    name: str

    def compose(self, *, model: str, prompt: str, seconds: int) -> bytes:
        """Make `seconds` of music as `prompt` describes. Returns the audio file."""
        ...
