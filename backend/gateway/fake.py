"""A scripted stand-in for a model provider, for tests. Each call pops the next scripted
outcome for its purpose: output data to return, or an error to raise.

Pictures and voices need no script: the fake draws a plain portrait, designs a numbered
voice, and speaks at `words_per_second`. Script an error for their purpose to make one fail."""

import io
import wave
from collections import defaultdict, deque
from typing import Any

import PIL.Image
from pydantic import BaseModel

from .types import ModelReply, ModelRequest, Picture


class FakeModel:
    name = "fake"
    INPUT_TOKENS = 1_000
    OUTPUT_TOKENS = 100
    SAMPLE_RATE = 8_000

    def __init__(self) -> None:
        self._scripts: defaultdict[str, deque[dict[str, Any] | BaseException]] = defaultdict(deque)
        self.words_per_second = 2.0
        self.voices = 0

    def respond(self, purpose: str, *outcomes: dict[str, Any] | BaseException) -> None:
        self._scripts[purpose].extend(outcomes)

    def complete[Out: BaseModel](self, request: ModelRequest[Out]) -> ModelReply[Out]:
        script = self._scripts[request.purpose]
        if not script:
            raise AssertionError(f"No scripted model response left for {request.purpose!r}")
        outcome = script.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        return ModelReply(
            output=request.output.model_validate(outcome),
            input_tokens=self.INPUT_TOKENS,
            output_tokens=self.OUTPUT_TOKENS,
        )

    def draw(self, *, model: str, prompt: str) -> Picture:
        self._fail_if_scripted("draw_person")
        portrait = io.BytesIO()
        PIL.Image.new("RGB", (72, 128), "tan").save(portrait, format="PNG")
        return Picture(
            data=portrait.getvalue(),
            input_tokens=self.INPUT_TOKENS,
            output_tokens=self.OUTPUT_TOKENS,
        )

    def design_voice(self, *, model: str, description: str, sample: str) -> str:
        self._fail_if_scripted("design_voice")
        self.voices += 1
        return f"fake-voice-{self.voices}"

    def speak(self, *, model: str, voice_id: str, text: str) -> bytes:
        self._fail_if_scripted("measure_voice")
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
