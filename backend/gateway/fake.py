"""A scripted stand-in for a model provider, for tests. Each call pops the next scripted
outcome for its purpose: output data to return, a turn an agent takes, an error to raise,
or a turn during which something else happens (see `meanwhile`).

Pictures, voices and transcripts need no script: the fake draws a plain portrait, makes a
plain picture of its own from other pictures, designs a numbered voice, speaks at
`words_per_second` with `pause_seconds` of silence before and after, and hears exactly the
words it spoke. Script an error for their purpose
to make one fail, or script a transcript for "transcribe_line" to have something else heard.

Music needs no script either: each piece asked for is a low hum as long as was asked, with
its number written into it so each is its own. Script an error for "make_music" to make one
fail.

Clips need no script either: each one asked for is made at once, a real tiny clip that
speaks the audio it was asked for, so ffmpeg can cut and join it. Script {"state": "working"}
for "collect_clip" to have it still being made when asked, {"state": "failed", "error": ...}
to have it fail, or an error for "make_clip" or "collect_clip"."""

import io
import itertools
import math
import subprocess
import tempfile
import wave
from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from typing import Any

import PIL.Image
from django.conf import settings
from pydantic import BaseModel, ValidationError

from .types import (
    ClipStatus,
    ModelReply,
    ModelRequest,
    Picture,
    ToolRequest,
    Transcription,
    Turn,
    TurnReply,
    TurnRequest,
    UnusableReply,
    Word,
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
    # The voice speaks in a steady tone at this pitch, and the music hums at this one, far
    # enough apart for a test to measure each on its own.
    VOICE_HERTZ = 440
    MUSIC_HERTZ = 110

    def __init__(self) -> None:
        self._scripts: defaultdict[str, deque[Outcome]] = defaultdict(deque)
        self.words_per_second = 2.0
        # Silence before and after the words in every audio spoken, as a real voice leaves.
        self.pause_seconds = 0.0
        self.voices = 0
        self.edits = 0
        # Every script the fake has been asked to speak, so a test can show that work paid
        # for once was not paid for again.
        self.spoken: list[str] = []
        # What each audio the fake spoke says, so hearing it gives back those words.
        self.heard: dict[bytes, str] = {}
        # Every audio the fake was asked to transcribe.
        self.transcribed: list[bytes] = []
        # The id of every clip the fake was asked to make, and of every one it handed over.
        self.clips_submitted: list[str] = []
        self.clips_downloaded: list[str] = []
        # The audio each clip asked for speaks, and each clip made, by its id.
        self._clip_audio: dict[str, bytes] = {}
        self.clips: dict[str, bytes] = {}
        # Every piece of music made, in turn.
        self.music: list[bytes] = []

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
        # The fake can't tell which purpose it speaks for, so an error scripted for either
        # fails the next speech.
        self._fail_if_scripted("measure_voice")
        self._fail_if_scripted("speak_line")
        self.spoken.append(text)
        seconds = len(text.split()) / self.words_per_second + 2 * self.pause_seconds
        audio = io.BytesIO()
        with wave.open(audio, "wb") as file:
            file.setnchannels(1)
            file.setsampwidth(2)
            file.setframerate(self.SAMPLE_RATE)
            # Silent for the pauses and a steady tone while the words are said, so a test can
            # hear where they are. Each audio spoken is its own, as a real voice's would be.
            pause = _silence_frames(self.pause_seconds, self.SAMPLE_RATE)
            words = _tone_frames(
                seconds - 2 * self.pause_seconds, self.SAMPLE_RATE, hertz=self.VOICE_HERTZ
            )
            frames = pause + words + pause
            first = len(self.spoken).to_bytes(2, "little")
            file.writeframes(first + frames[2:])
        self.heard[audio.getvalue()] = text
        return audio.getvalue()

    def transcribe(self, *, model: str, audio: bytes) -> Transcription:
        script = self._scripts["transcribe_line"]
        scripted = self._next("transcribe_line") if script else None
        assert not isinstance(scripted, Turn), "'transcribe_line' was scripted a turn"
        self.transcribed.append(audio)
        text = scripted["text"] if scripted else self.heard[audio]
        # Heard evenly spaced, at the pace the fake speaks.
        pace = 1 / self.words_per_second
        start = self.pause_seconds
        words = tuple(
            Word(
                text=word,
                start=round(start + i * pace, 3),
                end=round(start + (i + 1) * pace, 3),
            )
            for i, word in enumerate(text.split())
        )
        with wave.open(io.BytesIO(audio)) as file:
            seconds = file.getnframes() / file.getframerate()
        return Transcription(text=text, words=words, audio_seconds=seconds)

    def submit(self, *, picture: bytes, audio: bytes, motion_prompt: str) -> str:
        self._fail_if_scripted("make_clip")
        self.clips_submitted.append(f"video-{len(self.clips_submitted) + 1}")
        self._clip_audio[self.clips_submitted[-1]] = audio
        return self.clips_submitted[-1]

    def status(self, *, video_id: str) -> ClipStatus:
        scripted = self._next("collect_clip") if self._scripts["collect_clip"] else None
        assert not isinstance(scripted, Turn), "'collect_clip' was scripted a turn"
        if scripted is None:
            return ClipStatus(state="completed", video_url=f"https://fake.heygen/{video_id}.mp4")
        return ClipStatus(state=scripted["state"], error=scripted.get("error"))

    def download(self, *, url: str) -> bytes:
        video_id = url.removeprefix("https://fake.heygen/").removesuffix(".mp4")
        self.clips_downloaded.append(video_id)
        if video_id not in self.clips:
            self.clips[video_id] = _clip(video_id, self._clip_audio.get(video_id))
        return self.clips[video_id]

    def compose(self, *, model: str, prompt: str, seconds: int) -> bytes:
        self._fail_if_scripted("make_music")
        # A low hum, so a test can hear it under the voice's higher tone, but for its first
        # sound, which numbers it: each piece made is its own, as real music would be.
        music = io.BytesIO()
        with wave.open(music, "wb") as file:
            file.setnchannels(1)
            file.setsampwidth(2)
            file.setframerate(self.SAMPLE_RATE)
            frames = _tone_frames(seconds, self.SAMPLE_RATE, hertz=self.MUSIC_HERTZ)
            file.writeframes((len(self.music) + 1).to_bytes(2, "little") + frames[2:])
        self.music.append(music.getvalue())
        return self.music[-1]

    def _fail_if_scripted(self, purpose: str) -> None:
        script = self._scripts[purpose]
        if script:
            outcome = script.popleft()
            if isinstance(outcome, BaseException):
                raise outcome


# The colour of each clip made, in turn, so a test can see which clip plays where.
COLOURS = ("red", "lime", "blue", "yellow")


def _clip(video_id: str, audio: bytes | None) -> bytes:
    """A tiny real clip that speaks `audio` for as long as it lasts, over a plain picture:
    a second of silence for a clip not asked for through the fake. Each clip made is its
    own, as a real one would be: its id is written into it, and video-1 is red, video-2
    lime, and so on through COLOURS."""
    colour = COLOURS[(int(video_id.rsplit("-", 1)[-1]) - 1) % len(COLOURS)]
    with tempfile.TemporaryDirectory() as folder:
        speaks = f"{folder}/audio.wav"
        made = f"{folder}/clip.mp4"
        with open(speaks, "wb") as file:
            file.write(audio or _silence(seconds=1))
        subprocess.run(
            [
                settings.FFMPEG,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c={colour}:s=72x128:r=25",
                "-i",
                speaks,
                "-shortest",
                "-pix_fmt",
                "yuv420p",
                "-metadata",
                f"title={video_id}",
                made,
            ],
            check=True,
            capture_output=True,
            # No timeout: waiting with one sleeps in a loop, and a test's fake clock would
            # count those sleeps as time passing while the clip is collected.
        )
        with open(made, "rb") as file:
            return file.read()


def _silence(*, seconds: float) -> bytes:
    audio = io.BytesIO()
    with wave.open(audio, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(FakeModel.SAMPLE_RATE)
        file.writeframes(_silence_frames(seconds, FakeModel.SAMPLE_RATE))
    return audio.getvalue()


def _silence_frames(seconds: float, rate: int) -> bytes:
    return b"\0\0" * round(seconds * rate)


def _tone_frames(seconds: float, rate: int, *, hertz: int) -> bytes:
    """A steady tone at `hertz`, loud enough to be heard over silence."""
    return b"".join(
        round(8000 * math.sin(2 * math.pi * hertz * i / rate)).to_bytes(2, "little", signed=True)
        for i in range(round(seconds * rate))
    )
