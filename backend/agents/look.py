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
from typing import Any

import PIL.Image

from adforge import file_store
from gateway.types import Image
from jobs import clip_judge, library, owner_parts, work
from jobs.models import ProducedItem, Scene

from .loop import Refused

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
