"""Looking at what the ad is made of: a scene's versions, its clip's frames and what its clip
was sent, written and fitted for a model to be shown (fix redesign, decisions 10 and 17; spec
#210 point 21). The decider's looks were built from these, and the producer's look is (spec
#247 point 2): a sub-call on the producer's own model that is shown them with the producer's
question and answers in words only."""

import hashlib
import io
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, Literal

import PIL.Image
from pydantic import BaseModel, Field

from adforge import file_store
from gateway.gateway import call_model
from gateway.types import Handoff, Image
from jobs import clip_judge, library, library_items, owner_parts, work
from jobs.models import AgentFix, Job, ProducedItem, Scene

from .fixing_agent import judge_words, try_shown
from .loop import Refused, Tool
from .models import ToolCall

# ---------------------------------------------------------------- versions


def version_of(scene: Scene, number: int | None) -> work.Version:
    """Version `number` of the scene, or its current one."""
    versions = work.versions(scene)
    if not versions:
        raise Refused(f"scene {scene.number} has nothing made yet.")
    if number is None:
        return versions[-1]
    if not 1 <= number <= len(versions):
        raise Refused(f"scene {scene.number} has versions 1 to {len(versions)}, not {number}.")
    return versions[number - 1]


def named(version: work.Version, current: int) -> str:
    return f"{version.label}{' (the current version)' if version.number == current else ''}"


# ---------------------------------------------------------------- a clip's frames


def clip_frames(clip: ProducedItem, named: str) -> list[Image]:
    return [
        Image(label=f"{named}, {clip_judge.frame_label(each['times'])}", key=each["key"])
        for each in frames(clip)
    ]


def frames(clip: ProducedItem) -> list[dict[str, Any]]:
    """The clip's frames as the judge was shown them for movement, kept on its ruling, or
    cut now for a clip it never judged."""
    kept = ((clip.ruling or {}).get("pictures") or {}).get("frames")
    if kept:
        return kept  # type: ignore[no-any-return]
    return clip_judge.cut_pictures(clip.file, beside=0, sets=("frames",))["frames"]


def fitted[K](
    looks: Mapping[K, Sequence[dict[str, str]]], most: int
) -> dict[K, list[dict[str, str]]]:
    """Each look's pictures, with every run of one clip's frames (or one piece's) joined side
    by side, as few to a picture as lets every look fit in `most` pictures: the clip judge's
    way of fitting frames (`clip_judge.cut_pictures`), so each frame is kept at
    FRAMES_PER_SECOND (decision 10) and a note about two clips sees both at once. Any other
    picture, such as a reference sent with a clip, stays one to a picture."""
    parts = {key: _runs(pictures) for key, pictures in looks.items()}
    runs = [part for each in parts.values() for frames, part in each if frames]
    others = sum(len(part) for each in parts.values() for frames, part in each if not frames)
    together = 1
    while together < max((len(run) for run in runs), default=1) and (
        others + sum(math.ceil(len(run) / together) for run in runs) > most
    ):
        together += 1
    return {
        key: [
            shown
            for frames, part in each
            for shown in (
                [_joined_frames(part[i : i + together]) for i in range(0, len(part), together)]
                if frames
                else part
            )
        ]
        for key, each in parts.items()
    }


def _runs(pictures: Sequence[dict[str, str]]) -> list[tuple[bool, list[dict[str, str]]]]:
    """`pictures` cut into runs in order: each run of frames of one clip or piece, labelled
    as `clip_frames` labels them, as (True, frames); every other picture as (False, [it])."""
    runs: list[tuple[bool, list[dict[str, str]]]] = []
    of = None
    for picture in pictures:
        frame = FRAME_LABEL.fullmatch(picture["label"])
        if frame is None:
            runs.append((False, [picture]))
            of = None
        elif of == frame["clip"]:
            runs[-1][1].append(picture)
        else:
            runs.append((True, [picture]))
            of = frame["clip"]
    return runs


# A frame's label, as `clip_frames` gives it: the clip, then the time of each frame in it.
FRAME_LABEL = re.compile(
    r"^(?P<clip>.*), Frames? at (?P<times>\d+(?:\.\d+)? s(?:, \d+(?:\.\d+)? s)*)"
    r"(?:, left to right)?$"
)


def _joined_frames(frames: list[dict[str, str]]) -> dict[str, str]:
    """`frames` of one clip as one picture, joined left to right, labelled with the time of
    each. Kept under a name made from the frames, so a later turn, or a restart, shows the
    same picture and a paid turn is replayed for free."""
    if len(frames) == 1:
        return frames[0]
    labels = [FRAME_LABEL.fullmatch(frame["label"]) for frame in frames]
    assert all(labels), "a look at a clip labels each frame with its times"
    times = [
        float(time.removesuffix(" s"))
        for label in labels
        for time in label["times"].split(", ")  # type: ignore[index]
    ]
    keys = [frame["key"] for frame in frames]
    key = file_store.save_once(
        f"decider_frames_{hashlib.sha256(chr(10).join(keys).encode()).hexdigest()[:32]}.jpg",
        lambda: clip_judge.joined([PIL.Image.open(io.BytesIO(file_store.read(k))) for k in keys]),
        whole=file_store.whole_picture,
    )
    return {"label": f"{labels[0]['clip']}, {clip_judge.frame_label(times)}", "key": key}  # type: ignore[index]


# ---------------------------------------------------------------- a scene in words


def scene_facts(scene: Scene) -> str:
    """The scene as it stands, written by code: its kind, line and overlay, what it shows,
    its current clip's video prompt, a settled owner part, the shop owner's photos it uses and
    its versions."""
    versions = work.versions(scene)
    current = versions[-1] if versions else None
    # The overlay is read with the line it goes with.
    said = [f'Line: "{scene.line}"', *([f'Overlay: "{scene.overlay}"'] if scene.overlay else [])]
    if not scene.shows:
        facts = [f"Scene {scene.number}: the person talks to camera.", *said]
    else:
        kind = ", ".join(each for each in [scene.broll_kind, scene.person_shown] if each)
        facts = [
            f"Scene {scene.number}: shows the product{f' ({kind})' if kind else ''}.",
            *said,
            f"Shows: {scene.shows}",
            *([f"How the product is used: {scene.usage}"] if scene.usage else []),
            *([f"What it ends on: {scene.result}"] if scene.result else []),
            *(
                [
                    "What it needs: "
                    + "; ".join(
                        f"{need['what']} (product photos {', '.join(map(str, need['photos']))})"
                        for need in scene.needs
                    )
                ]
                if scene.needs
                else []
            ),
        ]
        prompt = video_prompt(current.clip) if current and current.clip else ""
        if prompt:
            facts.append(f"The current clip's video prompt: {prompt}")
    part = owner_parts.owner_part(scene)
    if part is not None and owner_parts.is_approved(scene):
        facts.append(f"Its owner part, which the shop owner said yes to: {part.what}. {part.looks}")
    in_use = scene.reference_photos.filter(dropped_by__isnull=True).values_list("thing", flat=True)
    if in_use:
        facts.append(f"The shop owner's photos it uses: {', '.join(in_use)}")
    if current is not None:
        facts.append(
            "Versions: " + "; ".join(named(version, current.number) for version in versions)
        )
    return "\n".join(facts)


def video_prompt(clip: ProducedItem) -> str:
    """The prompt `clip` was made from: its sent list's, else its own or its step's."""
    sent = library.sent_list_of(clip)
    if sent is not None:
        return sent.prompt
    return clip.motion_prompt or (clip.step.motion_prompt if clip.step else "")


# ---------------------------------------------------------------- the producer's look

# Its own call purpose, on the producer's model (gateway/catalog.py), so a lookup of the
# producer's last turn never finds a look (spec #247 point 15).
PURPOSE = "look"
# The most pictures one look is shown: what the model takes in one ask.
MOST_PICTURES = clip_judge.MOST_PICTURES

INSTRUCTIONS = """\
You look, for the producer of a short video ad, at what it can't see itself: a scene's clip \
as frames, each labelled with its time; what the clip's video model was sent (its pictures, \
each with its label, frames of each video piece and the prompt); a picture in the ad's \
library; or a try of a scene's fix. You are told the scene as planned, what you are shown, \
and the producer's question.
Answer the question only from what you are shown and told. You report: you never decide \
what to do next and you never make a plan. The producer decides.
Answer in four parts, each in plain words:
- sees: what the pictures or frames show that bears on the question, with the second it \
starts for a clip.
- wrong: what is wrong and with which part, the second it starts, and what a real one would \
do. "Nothing" when nothing the question is about is wrong.
- root_cause: what caused it, said by where it comes from: a picture or a video piece the \
video model was sent (by its number), words in the prompt, what the scene's plan asks, or \
what the video model can't do. Blank when nothing is wrong.
- direction: which way a fix should go, in one or two sentences, never a whole new prompt. \
Blank when nothing is wrong.
When what you are shown can't answer the question, say so in sees, and say what would."""


class LookHandoff(Handoff):
    """The scene as planned (blank for a library picture looked at alone), what the look is
    shown in words, and the producer's question."""

    scene: str
    shown: str
    question: str


class LookAnswer(BaseModel):
    """The look's words, in the judge's fault shape."""

    sees: str = Field(description="What is shown that bears on the question.")
    wrong: str = Field(
        description="What is wrong and with which part, the second it starts, and what a real "
        'one would do. "Nothing" when nothing is wrong.'
    )
    root_cause: str = Field(description="What caused it. Blank when nothing is wrong.")
    direction: str = Field(description="Which way a fix should go. Blank when nothing is wrong.")


class Look(Tool):
    """Look at something you can't see yourself, and be told about it in words: what it
    shows, what is wrong, its root cause and a direction for a fix. what: "clip" for a
    scene's current version, what its clip was sent and its frames; "version" for an earlier
    version of the scene by its number; "library_item" for a picture in the ad's library by
    its number; "try" for a try of the scene's newest fix by its number (try 0 is the clip it
    fixes). Ask one question, about what you need to know."""

    name: ClassVar[str] = "look"

    scene: int | None = Field(
        description="The scene's number. Null only for a library item looked at alone."
    )
    what: Literal["clip", "version", "library_item", "try"]
    number: int | None = Field(
        description="The version's, library item's or try's number; null for the clip."
    )
    question: str = Field(description="What you need to know, in one or two sentences.")

    def run(self, call: ToolCall) -> str:
        job = call.session.jobs.first()
        if job is None:
            raise Refused("there is no ad yet, so nothing to look at.")
        return look_at(job, self.scene, self.what, self.number, self.question)


def look_at(job: Job, scene: int | None, what: str, number: int | None, question: str) -> str:
    """The look's words on `what` of the scene numbered `scene` in `job` (spec #247 point
    2): shown its pictures and frames, fitted into MOST_PICTURES, and told the scene, what it
    is shown and `question`, on the producer's model. Raises Refused when there is nothing
    such to look at."""
    if not question.strip():
        raise Refused("ask the look one question, about what you need to know.")
    if (number is None) != (what == "clip"):
        raise Refused(
            "the clip takes no number; a version, a library item or a try takes its number."
        )
    found = _scene_of(job, scene, needed=what != "library_item")
    title, shown, pictures = _what_is_shown(job, found, what, number)
    fit = fitted({0: [{"label": each.label, "key": each.key} for each in pictures]}, MOST_PICTURES)
    answer = call_model(
        job=job,
        purpose=PURPOSE,
        instructions=INSTRUCTIONS,
        handoff=LookHandoff(
            scene=scene_facts(found) if found is not None else "",
            shown=shown,
            question=question.strip(),
        ),
        output=LookAnswer,
        images=[Image(label=each["label"], key=each["key"]) for each in fit[0]],
    )
    return "\n".join(
        [
            f"The look at {title}, asked: {question.strip()}",
            f"Sees: {answer.sees}",
            f"What is wrong: {answer.wrong}",
            f"Root cause: {answer.root_cause or '(none)'}",
            f"Direction: {answer.direction or '(none)'}",
        ]
    )


def _scene_of(job: Job, number: int | None, *, needed: bool) -> Scene | None:
    if number is None:
        if needed:
            raise Refused("name the scene to look at.")
        return None
    scene = job.scenes.filter(number=number).first()
    if scene is None:
        raise Refused(f"the ad has no scene {number}.")
    return scene


def _what_is_shown(
    job: Job, scene: Scene | None, what: str, number: int | None
) -> tuple[str, str, list[Image]]:
    """What the look is at, named; what it is shown, in words; and the pictures."""
    if what in ("clip", "version"):
        assert scene is not None
        return _version_shown(scene, number)
    assert number is not None, "look_at refuses a library item or a try with no number"
    if what == "library_item":
        return _library_item_shown(job, number)
    assert scene is not None
    return _try_of(scene, number)


def _library_item_shown(job: Job, number: int) -> tuple[str, str, list[Image]]:
    found = [each for each in library.library_of(job) if each.number == number]
    if not found:
        raise Refused(f"there is no library item {number}.")
    [item] = found
    return (
        f"library item {number}",
        f"Library item {library_items.library_line(item)}",
        [Image(label=f"Library item {item.number}: {item.label}", key=item.file)],
    )


def _try_of(scene: Scene, number: int) -> tuple[str, str, list[Image]]:
    """Try `number` of the scene's newest fix, as the fixing agent looks at it: what its
    clip was sent and its frames, with the judge's words; try 0 is the clip it fixes."""
    fixing = AgentFix.objects.filter(step__scene=scene).order_by("id").last()
    if fixing is None:
        raise Refused(f"scene {scene.number} has had no fix, so it has no tries.")
    if number == 0:
        picture, clip, ruling = (
            fixing.first_clip.picture if fixing.first_clip else None,
            fixing.first_clip,
            fixing.ruling,
        )
    else:
        row = fixing.tries.filter(number=number).first()
        if row is None:
            raise Refused(
                f"scene {scene.number}'s newest fix has tries 0 to {fixing.tries.count()}."
            )
        picture, clip = row.picture, row.clip
        ruling = clip.ruling if clip is not None else None
    label = f"Scene {scene.number}, try {number}"
    sent, shown = try_shown(label, picture, clip)
    said = f"What its clip was sent, then the clip's frames. {sent}" if sent else ""
    if not shown:
        raise Refused(f"scene {scene.number}'s try {number} has nothing to look at.")
    return (
        f"scene {scene.number}, try {number} of its newest fix",
        f"{said} {judge_words(ruling)}".strip(),
        shown,
    )


def _version_shown(scene: Scene, number: int | None) -> tuple[str, str, list[Image]]:
    """A version of the scene: what its clip was sent (or its starting picture, for a clip
    made from one), then its clip's frames, with the judge's words on it."""
    version = version_of(scene, number)
    current = work.versions(scene)[-1].number
    title = f"scene {scene.number}, {named(version, current)}"
    label = f"Scene {scene.number}, {named(version, current)}"
    clip = version.clip
    sent = library.sent_list_of(clip) if clip is not None else None
    said: list[str] = []
    pictures: list[Image] = []
    if sent is not None:
        said += [
            f"What its clip was sent, then the clip. {clip_judge.references_said(sent)}",
            f"The prompt it was sent: {sent.prompt}",
        ]
        pictures += clip_judge.references_shown(sent, label)
    elif version.picture is not None:
        said.append("Its starting picture, then the clip it was made from.")
        pictures.append(Image(label=f"{label}, starting picture", key=version.picture.file))
        if clip is not None and (prompt := video_prompt(clip)):
            said.append(f"The clip's video prompt: {prompt}")
    if clip is None:
        said.append("It has no clip yet.")
    else:
        said.append(
            f"The clip's frames, {clip_judge.FRAMES_PER_SECOND} a second, each labelled with its "
            f"time. {judge_words(clip.ruling)}"
        )
        pictures += clip_frames(clip, f"Scene {scene.number}, clip of {named(version, current)}")
    if not pictures:
        raise Refused(f"scene {scene.number}'s {version.label} has nothing to look at yet.")
    return title, " ".join(said), pictures
