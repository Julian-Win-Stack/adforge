"""The model provider for the owner-notes run: every call the app makes goes through it.

Two phases. While the FIRST version of an ad is made again, the saved round 4 ad is
replayed: the planner's real plan gets round 4's scenes, each B-roll scene's prompt writer
round 4's prompts, and each picture and B-roll clip is round 4's own file, so the shop owner's
notes land on the ad that was made and graded. Then, while the shop owner's NOTES are worked
on, every text model is real (the producer, the prompt writers, the picture judge and the fix
check of a picture), and so is every picture, at most one at a time and PICTURE_GAP_SECONDS
apart, under the picture model's limit.

Video is never paid for: every clip is made by the test fake, a plain clip as long as asked,
and the clip judge, which can't judge a fake clip, passes it. Voices, transcripts and music
are the fake's too: they cost money and the notes don't test them."""

import fcntl
import json
import os
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from gateway import gateway
from gateway.fake import FakeModel
from gateway.openai_adapter import OpenAIProvider
from gateway.types import (
    ClipStatus,
    LoadedPiece,
    ModelReply,
    ModelRequest,
    Picture,
    Transcription,
    TurnReply,
    TurnRequest,
)

from .saved_ads import SavedAd, as_planned

type Phase = Literal["first", "notes"]

# The picture model allows 20 pictures a minute on OpenAI (reference/rate-limits.md), for
# every run on the machine at once.
PICTURE_GAP_SECONDS = 3.1
PICTURE_LOCK = Path(os.environ.get("OWNER_NOTES_PICTURE_LOCK", "/tmp/owner-notes-pictures.lock"))

# What the clip judge answers: nothing wrong, and, asked about a fix's clip, the change made.
CLIP_SIDES_OK: dict[str, Any] = {
    "frames": [
        {
            "time": 0.0,
            "seen": "broad_face",
            "face": "front",
            "parts": "(video faked: not judged)",
            "thin_edge_side": "none",
        }
    ],
    "turns": [],
    "across_frames": "(video faked: not judged)",
    "verdict": "pass",
    "problem": None,
    "back_to_normal_at": None,
}
CLIP_MOTION_OK: dict[str, Any] = {
    "look": {
        "in_the_clip": "(video faked: not judged)",
        "start": "(video faked)",
        "moments": [{"time": 1.0, "what_happens": "(video faked)"}],
        "end": "(video faked)",
    },
    "ordered_action": "(video faked)",
    "seen_action": "(video faked)",
    **{
        question: {"verdict": "pass", "problem": None, "back_to_normal_at": None}
        for question in ("real_motion", "sudden_change", "does_what_was_ordered")
    },
}
CLIP_FIXED: dict[str, Any] = {
    # Asked of a fix with the shop owner's photos (owner photos build); ignored otherwise.
    "owner_photo_shows": "(video faked)",
    "old_shows": "(video faked)",
    "new_shows": "(video faked)",
    "verdict": "fixed",
    "still_wrong": None,
}
# What the starting picture judge answers for round 4's own pictures, already judged then.
PICTURE_OK: dict[str, Any] = {
    "objects": [
        {
            "name": "the product",
            "side_facing_camera": "the front",
            "parts_seen": "(round 4's picture, replayed)",
            "a_real_one_could_look_like_this": True,
            "why": "(round 4's picture, replayed)",
        }
    ],
    "verdict": "pass",
    "problem": None,
}
# What the cut-out check answers in the replay: round 4 had no cut-outs to judge.
CUT_OUT_OK: dict[str, Any] = {
    "photo_shows": "(round 4's photo, replayed)",
    "cut_out_shows": "(round 4's photo, replayed)",
    "verdict": "pass",
    "problem": None,
}


class OwnerNotesProvider:
    name = "owner-notes"

    def __init__(
        self,
        ad: SavedAd,
        *,
        real: Any = None,
        fake: FakeModel | None = None,
        real_clip: bool = False,
    ) -> None:
        self.ad = ad
        # The notes' first B-roll clip is made by
        # Creatify for real and judged for real; a second one is refused, so a case pays for
        # one clip at most.
        self.real_clip = real_clip
        self._real_videos: set[str] = set()
        self._creatify: Any = None
        self.phase: Phase = "first"
        # The real models; a test passes its fake.
        self.real = real if real is not None else OpenAIProvider()
        self.fake = fake or FakeModel()
        # While the first version is made: faster than the app's real voices (3.6 since #9's
        # lengthened scene 4 line ran 0.1 s over its clip at 3.2), so a
        # line's audio fits the round 4 clip made for its real audio (one longer than its clip
        # can't be made into a scene). The notes get round 4's real speed (start_notes).
        self.fake.words_per_second = 3.6
        self._saved_clips: dict[str, bytes] = {}

    def start_notes(self, words_per_second: float) -> None:
        """Work on the shop owner's notes from now on, with the voice speaking at
        `words_per_second`, as round 4's real voice did."""
        self.phase = "notes"
        self.fake.words_per_second = words_per_second

    # Text.

    def complete[Out: BaseModel](self, request: ModelRequest[Out]) -> ModelReply[Out]:
        faked = self._faked(request)
        if faked is not None:
            return ModelReply(
                output=request.output.model_validate(faked), input_tokens=0, output_tokens=0
            )
        reply = self.real.complete(request)
        if self.phase == "first" and request.purpose == "plan_ad":
            planned = self._round_4_plan(
                reply.output.model_dump(), request.handoff.model_dump()["library_pictures"]
            )
            return _with(reply, request, planned)
        if self.phase == "first" and request.purpose == "choose_broll_picture":
            return _with(reply, request, self._round_4_prompts(request, reply.output.model_dump()))
        return reply

    def _faked(self, request: ModelRequest[Any]) -> dict[str, Any] | None:
        if self.real_clip and self._real_videos and request.purpose.startswith("check_clip"):
            return None
        if request.purpose == "check_clip_sides":
            return CLIP_SIDES_OK
        if request.purpose == "check_clip_motion":
            if "owner_change" in request.output.model_fields:
                return {**CLIP_MOTION_OK, "owner_change": CLIP_FIXED}
            return CLIP_MOTION_OK
        if self.phase == "first" and request.purpose == "check_starting_picture":
            return PICTURE_OK
        if self.phase == "first" and request.purpose == "check_cut_out":
            return CUT_OUT_OK
        if self.phase == "first" and request.purpose == "fact_check":
            # Round 4's lines passed the fact check then; today's would change the ad the
            # notes are about, as when it made a B-roll scene a talking one.
            lines = request.handoff.model_dump()["lines"]
            return {
                "decision": "checked",
                "reason": "(round 4's lines, checked when round 4 was made)",
                "question": None,
                "lines": [
                    {
                        "scene": line["scene"],
                        "verdict": "ok",
                        "wrong": None,
                        "problem": None,
                        "page_says": None,
                    }
                    for line in lines
                ],
            }
        return None

    def _round_4_plan(self, decision: dict[str, Any], photos: int) -> dict[str, Any]:
        """The planner's decision, with round 4's scenes in its plan."""
        plan = decision.get("plan")
        if plan is None:
            return decision
        planned = plan["scenes"]
        scenes = []
        for index, scene in enumerate(self.ad.scenes):
            # The part and overlay the planner gave the scene in its place, if it planned one.
            same_place = planned[index] if index < len(planned) else {}
            if index == len(self.ad.scenes) - 1 and planned:
                same_place = planned[-1]
            scenes.append(
                {
                    **as_planned(scene, photos),
                    "part": same_place.get("part"),
                    "overlay": same_place.get("overlay"),
                }
            )
        return {**decision, "plan": {**plan, "scenes": scenes}}

    def _round_4_prompts(
        self, request: ModelRequest[Any], choice: dict[str, Any]
    ) -> dict[str, Any]:
        """The prompt writer's choice, with round 4's prompts for the scene."""
        saved = self.ad.scene(request.handoff.model_dump()["scene"])
        return {
            **choice,
            "prompt": saved.picture_prompt or choice["prompt"],
            "prompt_reason": saved.prompt_reason or choice["prompt_reason"],
            "motion_prompt": saved.motion_prompt or choice["motion_prompt"],
        }

    def take_turn(self, request: TurnRequest) -> TurnReply:
        reply: TurnReply = self.real.take_turn(request)
        return reply

    # Pictures.

    def draw(self, *, model: str, prompt: str, on_azure: bool = False) -> Picture:
        if self.phase == "first":
            # The portrait: round 4's first talking scene's picture shows the person. The
            # before-picture: its first B-roll scene's shows the place.
            portrait = _running_tool() == "create_person"
            scenes = [scene for scene in self.ad.scenes if scene.talking == portrait]
            return Picture(
                data=(scenes or self.ad.scenes)[0].picture, input_tokens=0, output_tokens=0
            )
        with _one_picture_at_a_time():
            picture: Picture = self.real.draw(model=model, prompt=prompt, on_azure=on_azure)
            return picture

    def edit(
        self, *, model: str, prompt: str, pictures: Sequence[bytes], on_azure: bool = False
    ) -> Picture:
        if self.phase == "first":
            call = gateway.running_tool()
            if call is None or "scene" not in call.arguments:
                # A cut-out made while the page is read (a library photo's product-only
                # version): the photo itself, as round 4 had none.
                return Picture(data=pictures[0], input_tokens=0, output_tokens=0)
            number = _running_scene()
            return Picture(data=self.ad.scene(number).picture, input_tokens=0, output_tokens=0)
        with _one_picture_at_a_time():
            picture: Picture = self.real.edit(
                model=model, prompt=prompt, pictures=pictures, on_azure=on_azure
            )
            return picture

    # Voices, transcripts and music: the fake's.

    def design_voice(self, *, model: str, description: str, sample: str) -> str:
        return self.fake.design_voice(model=model, description=description, sample=sample)

    def speak(self, *, model: str, voice_id: str, text: str) -> bytes:
        return self.fake.speak(model=model, voice_id=voice_id, text=text)

    def transcribe(self, *, model: str, audio: bytes) -> Transcription:
        return self.fake.transcribe(model=model, audio=audio)

    def compose(self, *, model: str, prompt: str, seconds: int) -> bytes:
        return self.fake.compose(model=model, prompt=prompt, seconds=seconds)

    # Clips: the fake's, but a first version's B-roll clip, which is round 4's.

    def submit(
        self, *, picture: bytes, audio: bytes | None, seconds: float, motion_prompt: str
    ) -> str:
        return self.fake.submit(
            picture=picture, audio=audio, seconds=seconds, motion_prompt=motion_prompt
        )

    def submit_broll(
        self,
        *,
        starting_picture: bytes | None,
        example_pictures: Sequence[bytes],
        seconds: int,
        prompt: str,
        reference_videos: Sequence[LoadedPiece] = (),
    ) -> str:
        if self.phase == "first":
            video_id = f"round4-{_running_scene()}"
            self._saved_clips[video_id] = self.ad.scene(_running_scene()).clip
            return video_id
        if self.real_clip:
            if self._real_videos:
                raise RuntimeError("test: this case pays for one real clip only")
            from gateway.creatify_adapter import CreatifyProvider

            self._creatify = CreatifyProvider()
            made_id: str = self._creatify.submit_broll(
                starting_picture=starting_picture,
                example_pictures=example_pictures,
                seconds=seconds,
                prompt=prompt,
                reference_videos=reference_videos,
            )
            self._real_videos.add(made_id)
            return made_id
        return self.fake.submit_broll(
            starting_picture=starting_picture,
            example_pictures=example_pictures,
            seconds=seconds,
            prompt=prompt,
            reference_videos=reference_videos,
        )

    def status(self, *, video_id: str) -> ClipStatus:
        if video_id in self._real_videos:
            status: ClipStatus = self._creatify.status(video_id=video_id)
            return status
        if video_id in self._saved_clips:
            return ClipStatus(state="completed", video_url=f"https://fake.fal/{video_id}.mp4")
        return self.fake.status(video_id=video_id)

    def download(self, *, url: str) -> bytes:
        if self._real_videos and not url.startswith("https://fake."):
            data: bytes = self._creatify.download(url=url)
            return data
        video_id = url.removeprefix("https://fake.fal/").removesuffix(".mp4")
        if video_id in self._saved_clips:
            return self._saved_clips[video_id]
        return self.fake.download(url=url)


def _with[Out: BaseModel](
    reply: ModelReply[Out], request: ModelRequest[Out], output: dict[str, Any]
) -> ModelReply[Out]:
    return ModelReply(
        output=request.output.model_validate(output),
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
    )


def _running_tool() -> str:
    call = gateway.running_tool()
    return call.tool if call is not None else ""


def _running_scene() -> int:
    call = gateway.running_tool()
    assert call is not None and "scene" in call.arguments, "a scene's step is running"
    return int(call.arguments["scene"])


class _one_picture_at_a_time:
    """Holds the machine's picture lock, and waits until PICTURE_GAP_SECONDS have passed since
    the last picture asked for under it."""

    def __enter__(self) -> None:
        PICTURE_LOCK.touch()
        self.file = PICTURE_LOCK.open("r+")
        fcntl.flock(self.file, fcntl.LOCK_EX)
        text = self.file.read()
        last = json.loads(text)["at"] if text else 0.0
        wait = last + PICTURE_GAP_SECONDS - time.time()
        if wait > 0:
            time.sleep(wait)

    def __exit__(self, *_: object) -> None:
        self.file.seek(0)
        self.file.truncate()
        self.file.write(json.dumps({"at": time.time()}))
        self.file.flush()
        fcntl.flock(self.file, fcntl.LOCK_UN)
        self.file.close()
