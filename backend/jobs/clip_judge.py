"""The judge of a B-roll clip, run right after the clip is made: two checks, each asked
ASKS times, each failing on FAILS_TO_FAIL of its answers. SIDES looks at 5 stills for objects
that can't exist and sides that aren't real; MOTION looks at FRAMES_PER_SECOND frames for
movement and change no real camera could film. Ported from the eval harness the judge was
tested in (/mnt/project-files/evals/clip-judge-v2: prompts.py arms sides5 and motion5plain,
with change 7's back-to-normal field; judge.py; score.py). The plan: Claude Doc "Clip judge
discussion", "Plan: what runs when" and changes 7 and 9 (Julian, 2026-10-13).

The judge's words are Julian's to decide (project rule, 2026-10-12): SIDES_CHECK,
MOTION_CHECK and the answer forms below are kept word for word as tested, but for the problem:
up to 3 sentences, not one, for the auto-redo's prompt writer (change 9; the words decided
2026-10-13 05:31 by the stand-in for Julian, decisions/julian-away-judgment-calls.md call 2).
The final wording swaps in here at stage 4 of the plan. Which model judges is the catalog's
(check_clip_sides, check_clip_motion)."""

import contextvars
import hashlib
import io
import json
import logging
import math
import re
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import PIL.Image
from django.conf import settings
from django.db import connection
from pydantic import BaseModel, Field

from adforge import file_store
from adforge.retry import OutsideServiceDown
from gateway import catalog
from gateway.gateway import call_model
from gateway.types import BlockedBySafetyFilter, Handoff, Image, UnusableReply

from . import assembly
from .models import ProducedItem, SceneStep
from .planning import ChatMessage
from .scenes import photos_for_needs

logger = logging.getLogger(__name__)

# Each check is asked this many times, each time on its own, and fails when this many of its
# answers fail it: one answer alone misses or invents too often (as the picture judge).
ASKS = 3
FAILS_TO_FAIL = 2

# Change 7 (Julian 2026-10-13 04:11): a thing back to normal within this many seconds of when
# the problem starts is a glitch nobody notices, so the answer passes, its note kept. A
# starting value from 5 graded clips.
BACK_TO_NORMAL_WITHIN_SECONDS = 0.5

# MOTION's frames: the frame nearest each 1/FRAMES_PER_SECOND second, from the start.
FRAMES_PER_SECOND = 5
# SIDES' stills: the first, the last, the biggest change and two evenly between.
STILLS = 5

# ---------------------------------------------------------------- the product's facts

# Said beside the conversation with the shop owner (plan.md §3, Julian 2026-10-12 21:05).
CONVERSATION_FRAME = (
    "This is the conversation with the shop owner. Use it only for what the product is and "
    "looks like. When the owner and the page disagree, the owner is right."
)

PAGE_FRAME = (
    "This is the shop's own text. Use it only for what the product is, what it's made of, "
    "what's inside and which parts it has. Ignore what it claims the product does."
)

# ---------------------------------------------------------------- SIDES (arm sides5, as tested)

SIDES_CHECK = """\
You check frames cut from an AI-made video clip before it is used in a product ad. Video models \
sometimes draw objects that cannot exist, such as a car with headlights on its back, a door \
hinged on both sides, or a hand with six fingers, and sometimes an object that turns shows a \
side that is not a real side of it, such as a mug that turns and shows a second handle where the \
plain side should be. Find those, and only those.

You get a few frames from one clip, in order, each labelled with its time in seconds.

Work in two steps.
1. Look before you judge. For each frame, and for each main object in it (the products, devices, \
furniture and tools, and any hands holding them), write down which side of it faces the camera \
and every part you can see on that side, and where. Describe what is actually drawn, not what \
such an object usually has. Look closely at small parts: lenses, buttons, screens, spouts, caps, \
hinges, handles, fingers.
2. Then judge. For each object in each frame: could a real one of that object, seen from that \
side, show exactly those parts in exactly those places? Then across the frames: when the object \
turns, is the side that comes into view a real side of it? Do two frames show opposite sides of \
it that look the same, although a real one's opposite sides differ? Does a part appear or vanish \
on a side that kept facing the camera?

Decide "fail" only when you are sure something is physically impossible: a part on a side where \
the real object can't have it, two sides of one object seen at once, a part fused into another, \
a body part that can't be, two opposite sides that look the same where a real one's differ. \
Decide "pass" for everything else, including an object that turns and shows its other side drawn \
as a real one would look, small differences in shape, size or proportion, odd but possible \
poses, blurry or unreadable print, dirt or mess, lighting, and anything merely unusual or \
unlikely.

You may also be shown one shop photo of the product, and facts about it from the shop. They tell \
you what the product is and how it is built: which parts it has and where they sit. Use them \
only for that. Ignore the photo's background, setting and anything being done with the product; \
only the product itself matters. Never compare the frames with the photo: differences in size, \
shape, angle, distance, colour, label or print are not a fail, and the product may be held, \
turned, upside down or partly hidden. Fail the product only if it shows a part a real one can't \
have there, two sides at once, or a label that didn't turn with it."""

# Change 7's field, Julian's yes 2026-10-13 04:34, in both checks' answers.
BACK_TO_NORMAL = "When is this thing back to normal? Give the second, or say never."


class ObjectLook(BaseModel):
    name: str
    side_facing_camera: str = Field(description="Which side or face of the object the camera sees.")
    parts_seen: str = Field(description="The parts visible on that side, and where they are.")
    a_real_one_could_look_like_this: bool
    why: str = Field(description="One short sentence.")


class FrameLook(BaseModel):
    time: float = Field(description="The frame's time in seconds, as labelled.")
    objects: list[ObjectLook]


# SIDES' answer, filled in from the top: it looks at each frame before it decides. A comment,
# not a docstring: a docstring would be sent to the judge in the answer's schema.
class SidesVerdict(BaseModel):
    frames: list[FrameLook]
    across_frames: str = Field(
        description="Which sides of each main object were seen, in which frames; whether a "
        "side that came into view is a real side; whether a part appeared or "
        "vanished on a side that kept facing the camera."
    )
    verdict: Literal["pass", "fail"]
    problem: str | None = Field(
        description="When it fails: up to 3 sentences: the object and what is impossible "
        "about it, the frame time, and what a real one would look like. Null when it passes."
    )
    back_to_normal_at: str | None = Field(description=BACK_TO_NORMAL)


# ---------------------------------------------------------------- MOTION (arm motion5plain)

MOTION_CHECK = """\
You check a short video clip for a product ad before it is shown to the shop owner. You decide \
whether everything in it moves and changes the way it would in real life, and whether it shows \
what the scene was meant to show.

You get frames from the clip, in order, each labelled with its time in seconds, so you can \
follow what moves between them. You also get the scene's order: one sentence saying what the \
clip should show.

Work in three steps.
1. Look before you judge. Fill in the look form from the frames: what is in the clip; how \
everything is at the first frame; then every movement and change of the product, of the hands \
touching it, and of the one thing the action changes (a surface, a liquid, a level, hair, a \
shape), step by step, with the time of the frame where you see it; and how everything is at the \
last frame. Describe what is on the frames, not what the product is for or what usually happens. \
Leave out background, clothing, jewellery, cords and the camera.
2. The changed thing. Name the thing the action changes. Write its state at the first frame and \
at the last frame, the time it changed and what was touching it then, and whether it ever showed \
both states at once, changed where nothing touched it, or changed all at once between two \
frames.
3. Then answer three questions, each "pass" or "fail".
- real_motion: does every hand, object and liquid move the way a real one would? Use the facts \
about the product: a thick gel must move like a thick gel, a hard object must stay rigid. Fail, \
for example, when a liquid flows from the wrong place or with the wrong thickness, comes out of \
a closed container, or flows against gravity; when an object moves with nothing moving it; when \
fingers pass through an object; when an object bends, melts or fuses in a way it can't; when a \
hand moves in a way no real hand can.
- sudden_change: does the changed thing change only while something acts on it, and only where \
it acts, step by step? Fail when it changes before the action reaches it, where nothing touched \
it, or all at once between two frames; or when the product itself switches shape or state \
between frames.
- does_what_was_ordered: does the clip do the main action of the order with the product? Fail \
only when that main action is missing, a different main action is done with the product, or the \
person, hand or object the order names is not in the clip. A camera move, a close-up, the \
framing, the pace, or something the order says should be "in view" or "emphasized" is never a \
fail. Small extra movements around the main action, walking, posing or holding are not a fail.

Decide "fail" only when you are sure from the frames. When a question passes, its problem is \
null. When it fails, write up to 3 sentences: what went wrong and with which part, the second it \
starts, and what a real one would do.

You may also be shown one shop photo of the product, and facts about it from the shop. They tell \
you what the product is, what it is made of, what is inside and how it is built. Use them only \
for that. Never compare the frames with the photo: differences in size, angle, colour, label or \
print are not a fail."""


class Moment(BaseModel):
    time: float = Field(description="Seconds, the time of the frame where it is seen.")
    what_happens: str


class ClipLook(BaseModel):
    in_the_clip: str = Field(
        description="The product, the hands touching it, the thing the action changes, where "
        "each is."
    )
    start: str = Field(
        description="How everything is at the first frame: what is held, where, which way it faces."
    )
    moments: list[Moment]
    end: str = Field(description="How everything is at the last frame.")


class ChangedThing(BaseModel):
    name: str
    state_at_start: str
    state_at_end: str
    when_it_changed_and_what_touched_it: str
    both_states_at_once_or_untouched_or_all_at_once: str


class Check(BaseModel):
    verdict: Literal["pass", "fail"]
    problem: str | None = Field(
        description="When it fails: up to 3 sentences: what went wrong and with which part, "
        "the second it starts, and what a real one would do. Null when it passes."
    )
    back_to_normal_at: str | None = Field(description=BACK_TO_NORMAL)


class MotionVerdict(BaseModel):
    look: ClipLook
    changed_thing: ChangedThing
    ordered_action: str = Field(
        description="The main action the order asks for, in your words, without camera or "
        "framing words."
    )
    seen_action: str = Field(description="The main action the clip actually does with the product.")
    real_motion: Check
    sudden_change: Check
    does_what_was_ordered: Check


MOTION_QUESTIONS = ("real_motion", "sudden_change", "does_what_was_ordered")

# A version of the judge's words, kept with each ruling: changes whenever they do.
WORDING = hashlib.sha256(
    "\n".join(
        [
            SIDES_CHECK,
            MOTION_CHECK,
            json.dumps(SidesVerdict.model_json_schema(), sort_keys=True),
            json.dumps(MotionVerdict.model_json_schema(), sort_keys=True),
        ]
    ).encode()
).hexdigest()[:10]

# ---------------------------------------------------------------- the ruling

Verdict = Literal["pass", "fail", "not_checked"]


def judged(
    sides: Sequence[dict[str, Any] | None], motion: Sequence[dict[str, Any] | None]
) -> dict[str, Any]:
    """The judge's ruling on a clip from its answers, in the order asked: each a SidesVerdict
    or MotionVerdict as a dict, or None for an ask that went unchecked. Each check (SIDES, and
    each MOTION question) is voted on by itself: it fails on FAILS_TO_FAIL failing answers,
    and is not checked with fewer than FAILS_TO_FAIL usable ones. The clip fails when any
    check fails. A failing answer whose thing is back to normal soon enough is let go.

    Gives "verdict" (pass, fail or not_checked), "checks" (each check's verdict),
    "failed_answers" (how many answers failed on anything, of all asked), "failures" (each
    failed answer's check, ask, problems and whole answer), "let_go" (each problem let go,
    with when it was back to normal) and "answers" (every answer, as given)."""
    # Each check's name and, by ask, the part of each answer that holds its verdict.
    asked: list[tuple[str, list[tuple[int, dict[str, Any]]]]] = [
        ("sides", [(ask, answer) for ask, answer in enumerate(sides, 1) if answer])
    ] + [
        (question, [(ask, answer[question]) for ask, answer in enumerate(motion, 1) if answer])
        for question in MOTION_QUESTIONS
    ]
    checks: dict[str, Verdict] = {}
    problems: dict[tuple[str, int], list[str]] = {}
    let_go: list[dict[str, Any]] = []
    for name, usable in asked:
        fails = 0
        for ask, said in usable:
            if said["verdict"] != "fail":
                continue
            if _back_to_normal_soon(said["problem"], said["back_to_normal_at"]):
                let_go.append(
                    {
                        "check": name,
                        "ask": ask,
                        "problem": said["problem"],
                        "back_to_normal_at": said["back_to_normal_at"],
                    }
                )
                continue
            fails += 1
            which = "sides" if name == "sides" else "motion"
            problems.setdefault((which, ask), []).append(said["problem"])
        checks[name] = (
            "fail"
            if fails >= FAILS_TO_FAIL
            else "not_checked"
            if len(usable) < FAILS_TO_FAIL
            else "pass"
        )
    failures = [
        {
            "check": which,
            "ask": ask,
            "problems": said,
            "answer": (sides if which == "sides" else motion)[ask - 1],
        }
        for (which, ask), said in sorted(
            problems.items(), key=lambda item: (item[0][0] != "sides", item[0][1])
        )
    ]
    verdicts = set(checks.values())
    return {
        "verdict": "fail"
        if "fail" in verdicts
        else "not_checked"
        if "not_checked" in verdicts
        else "pass",
        "checks": checks,
        "failed_answers": len(failures),
        "failures": failures,
        "let_go": let_go,
        "answers": {"sides": sides, "motion": motion},
    }


# The first second a problem names, where it starts: "at 8.0 s", "from 1.2-1.4 s", "3 seconds".
_SECONDS = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:(?:[-–—]|to|and)\s*\d+(?:\.\d+)?\s*)?(?:s\b|sec|second)"
)


def _back_to_normal_soon(problem: str | None, back_to_normal_at: str | None) -> bool:
    """Whether the thing `problem` is about is back to normal, at `back_to_normal_at`, within
    BACK_TO_NORMAL_WITHIN_SECONDS of when the problem starts. Never, when the problem names no
    second or the thing is never back (as score.py's `forgiven`)."""
    back = (back_to_normal_at or "").strip().lower()
    starts = _SECONDS.search(problem or "")
    back_at = re.search(r"\d+(?:\.\d+)?", back)
    if not back or "never" in back or starts is None or back_at is None:
        return False
    return 0 <= float(back_at.group(0)) - float(starts.group(1)) <= BACK_TO_NORMAL_WITHIN_SECONDS


# ---------------------------------------------------------------- judging a clip

# A model takes at most this many pictures in one ask (Azure: 400 "Exceeded maximum number of
# images (50)", seen 2026-10-12). When the frames and the shop photo don't fit, frames side by
# side in time order are joined into one picture.
MOST_PICTURES = 50
# At most this many photos of what a scene needs are shown beside the main one (plan.md §3).
MOST_NEEDS_PHOTOS = 5


class ClipCheckHandoff(Handoff):
    """Which of a check's asks about one clip this is, from 1; the facts about the product:
    what it is, from the plan and the shop's own text; and the conversation with the shop
    owner, for what the product is and looks like, with, for a fix's clip, what the producer
    restated the owner's notes as asking for (plan.md §3, Julian 2026-10-12 21:19). The number
    only keeps each ask's answer apart, so one asked again after the worker stopped isn't paid
    for twice. The judge never sees the clip's prompts or line."""

    ask: int
    facts: str
    about_the_conversation: str = CONVERSATION_FRAME
    conversation: list[ChatMessage]
    what_the_agent_understood_from_the_owner: str | None


class MotionCheckHandoff(ClipCheckHandoff):
    """And the scene's order: what the clip should show."""

    order: str


def judge_clip(
    step: SceneStep, clip: ProducedItem, *, conversation: list[ChatMessage]
) -> dict[str, Any]:
    """Judge the B-roll clip `step` made, keeping the ruling on the clip: see `judged`, plus
    "wording" (a version of the judge's words) and "models" (each check's), and "pictures"
    (what it was shown of the clip). `conversation` is what was said with the shop owner when
    the step started. Its pictures are cut once and kept on the clip before any ask, so a clip
    judged again after the worker stopped is shown the same pictures, and pays for no answer
    twice."""
    pictures = (clip.ruling or {}).get("pictures") or cut_pictures(clip.file)
    clip.ruling = {"pictures": pictures}
    clip.save(update_fields=["ruling"])
    planned = step.picture_step
    assert planned is not None and planned.photo is not None, (
        "a B-roll clip is made from a starting picture drawn from its main photo"
    )
    photo = planned.photo.file
    sides_facts = _facts(step, with_use=False)
    motion_facts = _facts(step, with_use=True)
    understood = step.fix.fix if step.fix else None
    needs = _needs_photos(step)
    asks: list[_Ask] = [
        _Ask(
            "check_clip_sides",
            ClipCheckHandoff(
                ask=ask,
                facts=sides_facts,
                conversation=conversation,
                what_the_agent_understood_from_the_owner=understood,
            ),
            SidesVerdict,
            _shown(photo, needs, pictures["stills"]),
        )
        for ask in range(1, ASKS + 1)
    ] + [
        _Ask(
            "check_clip_motion",
            MotionCheckHandoff(
                ask=ask,
                facts=motion_facts,
                order=step.shows,
                conversation=conversation,
                what_the_agent_understood_from_the_owner=understood,
            ),
            MotionVerdict,
            _shown(photo, needs, pictures["frames"]),
        )
        for ask in range(1, ASKS + 1)
    ]
    with ThreadPoolExecutor(max_workers=len(asks)) as pool:
        asked = [
            pool.submit(contextvars.copy_context().run, _in_its_own_thread, _answer, step, each)
            for each in asks
        ]
        answers = [each.result() for each in asked]
    ruling = {
        **judged(answers[:ASKS], answers[ASKS:]),
        "wording": WORDING,
        "models": {
            "sides": catalog.MODEL_FOR_PURPOSE["check_clip_sides"],
            "motion": catalog.MODEL_FOR_PURPOSE["check_clip_motion"],
        },
        "pictures": pictures,
    }
    clip.ruling = ruling
    clip.save(update_fields=["ruling"])
    return ruling


@dataclass(frozen=True)
class _Ask:
    """One ask of one check: what it is asked for, as, and shown."""

    purpose: str
    handoff: ClipCheckHandoff
    output: type[SidesVerdict] | type[MotionVerdict]
    images: list[Image]


def _answer(step: SceneStep, ask: _Ask) -> dict[str, Any] | None:
    """One ask's answer, or None when it can't be had: the clip is paid for, and failing it
    for that would pay for it again."""
    try:
        answer: BaseModel = call_model(
            job=step.scene.job,
            purpose=ask.purpose,
            instructions=SIDES_CHECK if ask.output is SidesVerdict else MOTION_CHECK,
            handoff=ask.handoff,
            output=ask.output,
            images=ask.images,
            pay_once=True,
        )
        return answer.model_dump(mode="json")
    except (UnusableReply, OutsideServiceDown, BlockedBySafetyFilter) as error:
        logger.warning(
            "%s ask %s about scene %s's clip went unchecked: %s",
            ask.purpose,
            ask.handoff.ask,
            step.scene.number,
            error,
        )
        return None


def _in_its_own_thread[Out](work: Callable[..., Out], *args: Any) -> Out:
    """Run `work` in a thread of its own, then close the database connection it opened."""
    try:
        return work(*args)
    finally:
        connection.close()


def _facts(step: SceneStep, *, with_use: bool) -> str:
    """The facts about the product the judge is told (context_text in the harness's
    prompts.py, with the colour and size the app knows too): its name, colour and size; for
    MOTION, how the scene uses it and what that changes; the parts the scene needs; and the
    shop's own text."""
    job = step.scene.job
    lines = [f"Product: {job.product_name}"]
    if job.product_colour:
        lines.append(f"Colour: {job.product_colour}")
    if job.product_size:
        lines.append(f"Size: {job.product_size}")
    if with_use and step.usage:
        lines.append(f"How the product is used in this scene: {step.usage}")
    if with_use and step.broll_result:
        lines.append(f"What that use changes: {step.broll_result}")
    needs = "; ".join(need["what"] for need in step.needs if need.get("what"))
    if needs:
        lines.append(f"Parts of the product this scene needs to show: {needs}")
    if job.page_text:
        lines.append(f"\n{PAGE_FRAME}\n<shop text>\n{job.page_text}\n</shop text>")
    return "\n".join(lines)


def _needs_photos(step: SceneStep) -> list[Image]:
    """A shop photo of each thing the scene needs that the main photo can't show, chosen as
    for its starting picture, at most MOST_NEEDS_PHOTOS, labelled with what it shows."""
    photos = {photo.position: photo for photo in step.scene.job.photos.all()}
    faces = {position: photo.has_face for position, photo in photos.items()}
    return [
        Image(label=f"A shop photo of {' and '.join(need.what)}:", key=photos[need.photo].file)
        for need in photos_for_needs(step.needs, faces, MOST_NEEDS_PHOTOS)
    ]


def _shown(photo: str, needs: list[Image], pictures: list[dict[str, Any]]) -> list[Image]:
    """The shop photo, the photos of what the scene needs, then the pictures of the clip in
    order, each labelled with the time of each frame in it."""
    shown = [Image(label="The shop photo of the product:", key=photo), *needs]
    for n, picture in enumerate(pictures):
        times = picture["times"]
        label = (
            f"Frame at {times[0]:g} s"
            if len(times) == 1
            else f"Frames at {', '.join(f'{t:g} s' for t in times)}, left to right"
        )
        if n == 0:
            label = f"Frames from the clip, in order:\n{label}"
        shown.append(Image(label=label, key=picture["key"]))
    return shown


def cut_pictures(clip: str) -> dict[str, list[dict[str, Any]]]:
    """The pictures the judge is shown of the clip at `clip`, kept in the file store, each
    with the time in seconds of each frame in it: "stills" for SIDES, "frames" for MOTION
    (frames.py in the harness). Frames that wouldn't fit in one ask beside the shop photo are
    joined side by side, as few in a row as fit."""
    with tempfile.TemporaryDirectory() as folder:
        video = Path(folder) / "clip.mp4"
        video.write_bytes(file_store.read(clip))
        frames = _every_frame(video, Path(folder) / "frames")
        stills = _stills(video, Path(folder) / "stills")
        return {"stills": _kept(stills), "frames": _kept(frames)}


def _kept(frames: list[tuple[Path, float]]) -> list[dict[str, Any]]:
    """`frames` kept as at most as many pictures as fit beside the shop photo and the photos
    of what the scene needs."""
    together = math.ceil(len(frames) / (MOST_PICTURES - 1 - MOST_NEEDS_PHOTOS))
    groups = [frames[i : i + together] for i in range(0, len(frames), together)]
    return [
        {"times": [t for _, t in group], "key": _side_by_side([path for path, _ in group])}
        for group in groups
    ]


def _side_by_side(paths: list[Path]) -> str:
    """The frames at `paths` joined left to right into one picture, kept in the file store:
    a single frame as it is."""
    if len(paths) == 1:
        return file_store.save("clip_frame.jpg", paths[0].read_bytes())
    pictures = [PIL.Image.open(path).convert("RGB") for path in paths]
    joined = PIL.Image.new(
        "RGB", (sum(p.width for p in pictures), max(p.height for p in pictures)), "white"
    )
    x = 0
    for picture in pictures:
        joined.paste(picture, (x, 0))
        x += picture.width
    saved = io.BytesIO()
    joined.save(saved, format="JPEG", quality=92)
    return file_store.save("clip_frames.jpg", saved.getvalue())


def _ffmpeg(*arguments: str) -> bytes:
    """What ffmpeg gives with `arguments`. Never reads stdin: an ffmpeg that does eats the
    input of whatever runs it."""
    return subprocess.run(
        [settings.FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        timeout=assembly.TIMEOUT_SECONDS,
    ).stdout


def _every_frame(video: Path, folder: Path) -> list[tuple[Path, float]]:
    """The frame nearest each 1/FRAMES_PER_SECOND second, from the start to the end."""
    folder.mkdir()
    _ffmpeg(
        "-i",
        str(video),
        "-vf",
        f"fps={FRAMES_PER_SECOND}:round=near",
        "-q:v",
        "2",
        str(folder / "%03d.jpg"),
    )
    files = sorted(folder.glob("*.jpg"))
    return [(file, round(i / FRAMES_PER_SECOND, 2)) for i, file in enumerate(files)]


def _stills(video: Path, folder: Path) -> list[tuple[Path, float]]:
    """STILLS frames: the first, the last, the one after the biggest change between two
    frames, and two evenly between (the middle when the biggest change is one of those)."""
    folder.mkdir()
    end = assembly.seconds_of(video) - 0.05
    biggest = round(_biggest_change(video), 2)
    picks = sorted({0.0, round(end / 3, 2), round(2 * end / 3, 2), biggest, round(end, 2)})
    if len(picks) < STILLS:
        picks = sorted({*picks, round(end / 2, 2)})
    return [_frame_at(video, t, folder / f"{i:03d}.jpg") for i, t in enumerate(picks)]


def _frame_at(video: Path, t: float, into: Path) -> tuple[Path, float]:
    """The frame at `t` seconds, and its time: a seek near the end can give no frame, so it
    steps back until one comes."""
    for back in (0, 0.1, 0.2, 0.3, 0.5):
        at = max(t - back, 0)
        _ffmpeg(
            "-y", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(into)
        )
        if into.exists():
            return into, round(at, 2)
    raise RuntimeError(f"no frame near {t} s")


def _biggest_change(video: Path) -> float:
    """The time of the frame right after the biggest change from the frame before, over the
    whole picture: a pointer to where something happens, never a judge."""
    width, height = 96, 172
    raw = _ffmpeg(
        "-i",
        str(video),
        "-vf",
        f"scale={width}:{height}",
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        "-",
    )
    size = width * height
    frames = [raw[i : i + size] for i in range(0, len(raw) - size + 1, size)]
    if len(frames) < 2:
        return 0.0
    changes = [
        sum(abs(a - b) for a, b in zip(before, after, strict=True))
        for before, after in zip(frames, frames[1:], strict=False)
    ]
    rate = len(frames) / assembly.seconds_of(video)
    return (changes.index(max(changes)) + 1) / rate
