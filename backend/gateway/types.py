from collections.abc import Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar, Literal, Protocol, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    ValidationError,
    model_serializer,
    model_validator,
)


class Handoff(BaseModel):
    """What one part of the system hands a model. Strict, so a decimal where a whole
    number belongs, or a missing field, fails before any money is spent."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    # The fields a model reads after the pictures it is shown rather than before them, such
    # as the planner's page text after the library's sheets. A call with no pictures is handed
    # every field at once.
    AFTER_IMAGES: ClassVar[frozenset[str]] = frozenset()

    def around_images(self) -> tuple[str, str | None]:
        """The handoff as JSON in two parts: the fields that go before the pictures, and
        those in AFTER_IMAGES that go after them (None when there are none), each in the
        handoff's own field order."""
        if not self.AFTER_IMAGES:
            return self.model_dump_json(), None
        return (
            self.model_dump_json(exclude=set(self.AFTER_IMAGES)),
            self.model_dump_json(include=set(self.AFTER_IMAGES)),
        )


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


@dataclass(frozen=True)
class LoadedVideo:
    """A video read from the file store, ready to send: by its key there, its media type and
    its bytes."""

    key: str
    media_type: str
    data: bytes


@dataclass(frozen=True)
class VideoRequest[Out: BaseModel]:
    """One video to watch: what to answer, in `output`'s shape, from the handoff and from
    what is seen and heard in `video`, looked at `frames_per_second` times a second."""

    purpose: str
    model: str
    instructions: str
    handoff: Handoff
    output: type[Out]
    video: LoadedVideo
    frames_per_second: float


class UnusableReply(Exception):
    """The provider answered, and billed for it, but the answer can't be used: a refusal,
    or output cut off before it was complete."""

    def __init__(self, message: str, *, input_tokens: int, output_tokens: int) -> None:
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class AnswerBrokeItsRules(UnusableReply):
    """An answer, billed and whole, that broke a rule of its output's shape, such as a field's
    check: inside a producer's tool call the model is told why and asked once more (spec #247
    point 8, `gateway.asking_again_once`)."""


def broke_its_rules(error: ValueError) -> bool:
    """Whether reading an answer failed on a rule of its output, the answer itself whole: not
    JSON cut off or unreadable."""
    return isinstance(error, ValidationError) and all(
        each["type"] != "json_invalid" for each in error.errors()
    )


class AnswerTookTooLong(Exception):
    """A model took longer to answer than its purpose allows (catalog.SLOW_ANSWER_SECONDS).
    Not asked again: a stalled ask would hold up whatever waits on it for every try."""


class BlockedBySafetyFilter(Exception):
    """The provider's safety filter refused what it was sent, such as a product photo it
    took for something it isn't. Asking again sends the same thing, so it isn't retried."""


class RefusedForMoney(Exception):
    """The provider refused the call because the account is out of money: its spend limit or
    its quota is reached. Asking again soon gets the same answer, so it isn't retried."""


class NotSetUp(Exception):
    """The app has no key for a service it needs, such as OpenAI's own API for pictures.
    Asking again sends the same thing, so it isn't retried."""


class ModelProvider(Protocol):
    """Talks to one model provider. Raises OutsideServiceDown for errors worth retrying,
    and UnusableReply for an answer that was billed but can't be used."""

    name: str

    def complete[Out: BaseModel](self, request: ModelRequest[Out]) -> ModelReply[Out]: ...


class VideoProvider(Protocol):
    """Watches videos, sound and all. Raises OutsideServiceDown for errors worth retrying,
    BlockedBySafetyFilter for a video or handoff its safety filter refuses, and UnusableReply
    for an answer that was billed but can't be used."""

    name: str

    def watch[Out: BaseModel](self, request: VideoRequest[Out]) -> ModelReply[Out]: ...


class Said(Handoff):
    """Something said in an agent's conversation: by the user, or by the agent itself."""

    kind: Literal["said"] = "said"
    by: Literal["user", "agent"]
    text: str


class Shown(Handoff):
    """A picture a tool handed back, by the label code gave it and its key in the file store."""

    label: str = Field(min_length=1)
    key: str = Field(min_length=1)


class ToolUse(Handoff):
    """A tool the agent called earlier in its conversation, and what the tool handed back:
    its words, and any pictures, which the agent is shown with their labels in this order."""

    kind: Literal["tool_use"] = "tool_use"
    call_id: str
    tool: str
    arguments: dict[str, Any]
    result: str
    pictures: list[Shown] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _no_pictures_left_out(self, dump: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # A text-only tool's use is recorded exactly as before pictures could be handed back.
        dumped: dict[str, Any] = dump(self)
        if not self.pictures:
            dumped.pop("pictures", None)
        return dumped


class StepFinished(Handoff):
    """Work a tool started in the background finished or failed: what the agent is told,
    by the system rather than the user."""

    kind: Literal["step_finished"] = "step_finished"
    text: str


# One thing in an agent's conversation.
type Happened = Said | ToolUse | StepFinished


class TurnHandoff(Handoff):
    """What an agent is given for one turn: its conversation so far, the names of the tools
    it may call, and, for an agent given one, the snapshot of the ad as it stands, given after
    the conversation (spec #247 point 15)."""

    conversation: list[Happened]
    tools: list[str]
    snapshot: str = ""

    @model_serializer(mode="wrap")
    def _no_snapshot_left_out(self, dump: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # A turn with no snapshot is recorded exactly as before snapshots were given.
        dumped: dict[str, Any] = dump(self)
        if not self.snapshot:
            dumped.pop("snapshot", None)
        return dumped


@dataclass(frozen=True)
class ToolSpec:
    """A tool offered to an agent: its name, what it does in words the model reads, and the
    shape of the arguments the model fills in."""

    name: str
    description: str
    arguments: type[BaseModel]


@dataclass(frozen=True)
class TurnRequest:
    """One turn to take. `images` are the pictures the conversation's tools handed back, read
    from the file store, one for each picture its tool uses name, in the same order."""

    purpose: str
    model: str
    instructions: str
    handoff: TurnHandoff
    tools: tuple[ToolSpec, ...]
    images: tuple[LoadedImage, ...] = ()


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
    """A turn taken, what it was billed, and the summary of the model's reasoning for it:
    blank when it gave none."""

    turn: Turn
    input_tokens: int
    output_tokens: int
    reasoning: str = ""


class AgentProvider(Protocol):
    """Runs agents' turns. Raises OutsideServiceDown for errors worth retrying, and
    UnusableReply for an answer that was billed but can't be used."""

    name: str

    def take_turn(self, request: TurnRequest) -> TurnReply: ...


class PortraitHandoff(Handoff):
    prompt: str = Field(min_length=1)


# The purpose a picture is being made from other pictures for, while the provider makes it:
# what a stand-in provider for tests knows the picture by.
EDITING_FOR: ContextVar[str | None] = ContextVar("editing_for", default=None)


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
    """Draws pictures, on OpenAI's own servers or, `on_azure`, on our Azure copy of the same
    picture model. Raises OutsideServiceDown for errors worth retrying, and RefusedForMoney
    when the account is out of money."""

    name: str

    def draw(self, *, model: str, prompt: str, on_azure: bool = False) -> Picture: ...

    def edit(
        self, *, model: str, prompt: str, pictures: Sequence[bytes], on_azure: bool = False
    ) -> Picture:
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


# Boreal-H3 makes B-roll clips of 5 to 15 whole seconds, and takes up to 9 example pictures,
# the first 5 for free (docs/broll-picture-logic.md, "What Boreal-H3 takes"; spec #210 point
# 13 and Further Notes).
LEAST_BROLL_SECONDS = 5
MOST_BROLL_SECONDS = 15
MOST_EXAMPLE_PICTURES = 9
FREE_EXAMPLE_PICTURES = 5


# Boreal-H3 takes reference videos of 2 to 15 seconds, and no more than 15 seconds of them
# in all (spec #210, point 14).
LEAST_PIECE_SECONDS = 2
MOST_PIECE_SECONDS = 15
# Why a piece holds fewer seconds than its moment: the moment ran past MOST_PIECE_SECONDS and
# was cut to its first seconds, the pieces ran past it together and the last was cut to fit,
# or the moment ran past its video's end and was cut there.
PieceTrim = Literal["moment_over_limit", "pieces_over_limit", "video_ended"]


class ReferenceVideo(Handoff):
    """A piece of a page video, sent beside the example pictures to show how the product
    moves: the cut piece by its key in the file store, the page video it was cut from, also
    by its key, and the seconds of that video it holds. `trim` says why it was cut shorter
    than the seconds asked for, so the record can say so; None when it holds all of them."""

    file: str = Field(min_length=1)
    video: str = Field(min_length=1)
    start: float = Field(ge=0)
    end: float
    trim: PieceTrim | None = None

    @property
    def seconds(self) -> float:
        return self.end - self.start

    @model_validator(mode="after")
    def _within_the_window(self) -> Self:
        # A hair either way, as seconds counted in floats can be, isn't out of it.
        if not LEAST_PIECE_SECONDS - 1e-6 <= self.seconds <= MOST_PIECE_SECONDS + 1e-6:
            raise ValueError(
                f"a reference video is {LEAST_PIECE_SECONDS} to {MOST_PIECE_SECONDS} seconds "
                f"long, and this one is {self.seconds:g}"
            )
        return self


class BrollClipHandoff(Handoff):
    """A B-roll clip to make, with no sound, each picture by its key in the file store: from
    a starting picture, its exact first frame, or from example pictures that show how things
    look, never both; with example pictures, any pieces of page videos that show how the
    product moves. How long it is, in whole seconds, and the prompt saying what happens."""

    starting_picture: str | None = Field(default=None, min_length=1)
    example_pictures: list[Annotated[str, Field(min_length=1)]] = Field(
        default_factory=list, max_length=MOST_EXAMPLE_PICTURES
    )
    reference_videos: list[ReferenceVideo] = Field(default_factory=list)
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
        if self.starting_picture is not None and self.reference_videos:
            raise ValueError(
                "a B-roll clip made from a starting picture takes no reference videos: they go "
                "beside example pictures"
            )
        sent = sum(piece.seconds for piece in self.reference_videos)
        if sent > MOST_PIECE_SECONDS + 1e-6:
            raise ValueError(
                f"a B-roll clip takes at most {MOST_PIECE_SECONDS} seconds of reference video "
                f"in all, and this one is sent {sent:g}"
            )
        return self

    @model_serializer(mode="wrap")
    def _no_videos_left_out(self, dump: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # A clip sent no piece is recorded exactly as before pieces could be sent, so a clip
        # asked for then is still found by its handoff, and not paid for again.
        dumped: dict[str, Any] = dump(self)
        if not self.reference_videos:
            dumped.pop("reference_videos", None)
        return dumped


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


@dataclass(frozen=True)
class LoadedPiece:
    """A ReferenceVideo read from the file store, ready to send: the piece's file, what kind
    of file it is, and the piece as it was handed over."""

    data: bytes
    media_type: str
    piece: ReferenceVideo


class BrollClipProvider(ClipCollector, Protocol):
    """Makes B-roll clips, with no sound."""

    def submit_broll(
        self,
        *,
        starting_picture: bytes | None,
        example_pictures: Sequence[bytes],
        seconds: int,
        prompt: str,
        reference_videos: Sequence[LoadedPiece] = (),
    ) -> str:
        """Ask for a `seconds`-long clip as `prompt` says, starting on `starting_picture`, or
        with things looking as in `example_pictures` and moving as in `reference_videos`.
        Returns its id."""
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
