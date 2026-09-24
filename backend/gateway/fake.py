"""A scripted stand-in for a model provider, for tests. Each call pops the next scripted
outcome for its purpose: output data to return, a turn an agent takes, an error to raise,
or a turn during which something else happens (see `meanwhile`).

Pictures and voices need no script: the fake draws a plain portrait, makes a plain picture
of its own from other pictures, designs a numbered voice, and speaks at `words_per_second`.
Script an error for their purpose to make one fail."""

import io
import itertools
import wave
from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from typing import Any

import PIL.Image
from pydantic import BaseModel, ValidationError

from .types import (
    ModelReply,
    ModelRequest,
    Picture,
    ToolRequest,
    Turn,
    TurnReply,
    TurnRequest,
    UnusableReply,
)

# Numbers the scripted tool calls, so each has its own id as a real model's would.
_call_ids = itertools.count(1)


def turn(says: str = "", *, calls: Sequence[tuple[str, dict[str, Any]]] = ()) -> Turn:
    """A turn for a fake agent to take: what it says, and each tool it calls with its
    arguments. A turn that calls no tool is the agent's reply."""
    return Turn(
        says=says,
        calls=tuple(
            ToolRequest(call_id=f"call_{next(_call_ids)}", tool=tool, arguments=arguments)
            for tool, arguments in calls
        ),
    )


def meanwhile(happens: Callable[[], object], then: Turn) -> Callable[[], Turn]:
    """A turn the model takes long enough over for `happens` to happen while it works, such
    as the user sending another message."""

    def taking() -> Turn:
        happens()
        return then

    return taking


type Outcome = dict[str, Any] | Turn | BaseException | Callable[[], Turn]


class FakeModel:
    name = "fake"
    INPUT_TOKENS = 1_000
    OUTPUT_TOKENS = 100
    SAMPLE_RATE = 8_000

    def __init__(self) -> None:
        self._scripts: defaultdict[str, deque[Outcome]] = defaultdict(deque)
        self.words_per_second = 2.0
        self.voices = 0
        self.edits = 0
        # Every script the fake has been asked to speak, so a test can show that work paid
        # for once was not paid for again.
        self.spoken: list[str] = []

    def respond(self, purpose: str, *outcomes: Outcome) -> None:
        self._scripts[purpose].extend(outcomes)

    def complete[Out: BaseModel](self, request: ModelRequest[Out]) -> ModelReply[Out]:
        outcome = self._next(request.purpose)
        assert isinstance(outcome, dict), f"{request.purpose!r} was scripted a turn, not output"
        try:
            output = request.output.model_validate(outcome)
        except ValidationError as error:
            # As the real adapter does with an answer that breaks the output's rules.
            raise UnusableReply(
                f"{request.model} gave an answer for {request.purpose} that could not be "
                f"read: {error}",
                input_tokens=self.INPUT_TOKENS,
                output_tokens=self.OUTPUT_TOKENS,
            ) from error
        return ModelReply(
            output=output, input_tokens=self.INPUT_TOKENS, output_tokens=self.OUTPUT_TOKENS
        )

    def take_turn(self, request: TurnRequest) -> TurnReply:
        outcome = self._next(request.purpose)
        assert isinstance(outcome, Turn), f"{request.purpose!r} was scripted output, not a turn"
        return TurnReply(
            turn=outcome, input_tokens=self.INPUT_TOKENS, output_tokens=self.OUTPUT_TOKENS
        )

    def _next(self, purpose: str) -> dict[str, Any] | Turn:
        script = self._scripts[purpose]
        if not script:
            raise AssertionError(f"No scripted model response left for {purpose!r}")
        outcome = script.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return outcome()
        return outcome

    def draw(self, *, model: str, prompt: str) -> Picture:
        self._fail_if_scripted("draw_person")
        portrait = io.BytesIO()
        PIL.Image.new("RGB", (72, 128), "tan").save(portrait, format="PNG")
        return Picture(
            data=portrait.getvalue(),
            input_tokens=self.INPUT_TOKENS,
            output_tokens=self.OUTPUT_TOKENS,
        )

    def edit(self, *, model: str, prompt: str, pictures: Sequence[bytes]) -> Picture:
        self._fail_if_scripted("make_starting_picture")
        self.edits += 1
        made = io.BytesIO()
        # Each picture made is its own, as a real model's would be.
        PIL.Image.new("RGB", (72, 128), (self.edits, 120, 90)).save(made, format="PNG")
        return Picture(
            data=made.getvalue(),
            input_tokens=self.INPUT_TOKENS,
            output_tokens=self.OUTPUT_TOKENS,
            picture_input_tokens=self.INPUT_TOKENS // 2,
        )

    def design_voice(self, *, model: str, description: str, sample: str) -> str:
        self._fail_if_scripted("design_voice")
        self.voices += 1
        return f"fake-voice-{self.voices}"

    def speak(self, *, model: str, voice_id: str, text: str) -> bytes:
        self._fail_if_scripted("measure_voice")
        self.spoken.append(text)
        seconds = len(text.split()) / self.words_per_second
        audio = io.BytesIO()
        with wave.open(audio, "wb") as file:
            file.setnchannels(1)
            file.setsampwidth(2)
            file.setframerate(self.SAMPLE_RATE)
            file.writeframes(b"\0\0" * round(seconds * self.SAMPLE_RATE))
        return audio.getvalue()

    def _fail_if_scripted(self, purpose: str) -> None:
        script = self._scripts[purpose]
        if script:
            outcome = script.popleft()
            if isinstance(outcome, BaseException):
                raise outcome
