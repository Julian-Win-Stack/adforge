"""A product-shots ad (spec #767): silent scenes of the product, overlays drawn by code and
music, no presenter and no spoken line. Its planned lengths, its music and its planning
check; its scenes are made by the scene agent (agents/scene_agent/silent.py)."""

import math
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

from django.db import transaction

from adforge import file_store

from . import assembly
from .beats import find_beats, on_the_beat
from .checks import LineToCheck
from .models import AdStyle, Job, ProducedItem, Scene, SceneStep
from .planning import END_CARD_SECONDS

# The music plays alone, with no voice over it, so it is asked for as the ad's whole sound.
MUSIC_FOR_THE_STYLE = (
    "Instrumental music for a short, beautiful product video ad, with no voice over it. A "
    "steady, clear beat from the first second. Instrumental only, no vocals, no singing, no "
    "spoken words."
)
MUSIC_PROMPT = "{mood}. " + MUSIC_FOR_THE_STYLE
# The music plays alone under a product-shots ad, so it is played near full level.
MUSIC_ALONE_VOLUME = 0.9
# The music runs this much longer than the planned ad, so it never ends before the end card.
MUSIC_SPARE_SECONDS = 3


def is_product_shots(job: Job) -> bool:
    return job.ad_style == AdStyle.PRODUCT_SHOTS


def is_silent(scene: Scene) -> bool:
    return scene.kind == Scene.Kind.SILENT


def planned_seconds(scene: Scene) -> float:
    """How long the producer planned the silent scene to play."""
    return float((scene.silent or {})["seconds"])


def ad_seconds(job: Job) -> float:
    """The planned ad's length: every scene's planned length, then the end card."""
    return sum(planned_seconds(scene) for scene in job.scenes.all()) + END_CARD_SECONDS


def music_prompt(mood: str) -> str:
    return MUSIC_PROMPT.format(mood=mood)


def music_seconds(job: Job) -> int:
    return math.ceil(ad_seconds(job) + MUSIC_SPARE_SECONDS)


def has_its_music(job: Job) -> bool:
    """Whether the job's newest music was made for this product-shots plan: instrumental for
    the style, and long enough for the planned ad. Music made for an earlier plan isn't."""
    from . import work

    music = work.latest(job, ProducedItem.Kind.MUSIC)
    return (
        music is not None
        and music.text.endswith(MUSIC_FOR_THE_STYLE)
        and (music.seconds or 0) >= music_seconds(job)
    )


# What edit_scene can change on a silent scene; anything else is planned again.
SILENT_EDITABLE = ("overlay", "shows")


def edit_silent_scene(scene: Scene, changes: dict[str, str]) -> list[str]:
    """Change a silent scene's overlay or what it shows; the fields that changed. A changed
    overlay is fact-checked again; a changed picture is out of date, so the scene is filmed
    again. CantEdit while the scene's agent works and what it shows would change."""
    from .plan_edits import CantEdit

    with transaction.atomic():
        Job.objects.select_for_update().get(pk=scene.job_id)
        scene = Scene.objects.select_for_update().get(pk=scene.pk)
        new = {field: " ".join(changes[field].split()) for field in changes}
        changed = [
            field
            for field in SILENT_EDITABLE
            if field in new and new[field] != getattr(scene, field)
        ]
        if "shows" in changed and scene.steps.filter(status=SceneStep.Status.RUNNING).exists():
            raise CantEdit(
                f"scene {scene.number}'s agent is working; pass_note sends it the owner's change."
            )
        if not changed:
            return []
        for field in changed:
            setattr(scene, field, new[field])
        scene.fact_checked = False
        scene.save(update_fields=[*changed, "fact_checked"])
    return changed


def check_overlays(job: Job) -> str | None:
    """The planning fact check, run on the overlays of the scenes not yet checked, and on the
    end card with the last scene, against the page and what the shop owner said. A silent
    scene has no line, so nothing is rewritten: what failed is given back for the producer to
    fix. None once every scene has passed."""
    from . import work

    scenes = list(job.scenes.order_by("number"))
    last = scenes[-1]
    to_check = [
        scene for scene in scenes if not scene.fact_checked and (scene.overlay or scene is last)
    ]
    # A scene with no words on it, but the last, has nothing to check.
    Scene.objects.filter(
        pk__in=[scene.pk for scene in scenes if scene not in to_check], fact_checked=False
    ).update(fact_checked=True)
    if not to_check:
        return None
    lines = [
        LineToCheck(
            scene=scene.number,
            line="",
            overlay=scene.overlay or None,
            end_card_line=job.end_card_line if scene is last else None,
            end_card_price=job.end_card_price if scene is last else None,
        )
        for scene in to_check
    ]
    check = work._ask_fact_check(job, lines, work._conversation(job))
    verdicts = {verdict.scene: verdict for verdict in check.lines}
    failed = []
    for scene in to_check:
        verdict = verdicts[scene.number]
        if verdict.verdict == "ok":
            Scene.objects.filter(pk=scene.pk).update(fact_checked=True)
            continue
        scene.fact_problems.append(
            {
                "wrong": "overlay",
                "problem": verdict.problem,
                "page_says": verdict.page_says,
                "rewritten": False,
            }
        )
        scene.save(update_fields=["fact_problems"])
        what = f'overlay "{scene.overlay}"' if scene.overlay else "end card"
        if scene is last and scene.overlay:
            what += " or the end card"
        failed.append(
            f"Scene {scene.number}'s {what} isn't supported: {verdict.problem} The page says: "
            f"{verdict.page_says}"
        )
    if failed:
        return " ".join(failed)
    job.status = Job.Status.READY_TO_RENDER
    job.save(update_fields=["status"])
    return None


def cuts(
    clips: Sequence[ProducedItem], beats: Sequence[float] | None
) -> tuple[list[assembly.Cut], list[int]]:
    """Each silent scene's clip, one after another with the scene's overlay, cut so every
    cut lands on one of the music's `beats` nearest its planned place (jobs.beats), or, with
    no beats, at its planned length (or its whole length, if shorter); and the scenes, by
    number, whose cut is off the beat. A silent scene has no voice."""
    for clip in clips:
        assert clip.scene is not None and clip.seconds is not None, "a clip is a scene's, measured"
    planned = [planned_seconds(clip.scene) for clip in clips if clip.scene is not None]
    held = [float(clip.seconds or 0.0) for clip in clips]
    if beats is None:
        lengths = [round(min(want, most), 3) for want, most in zip(planned, held, strict=True)]
        off_beat: list[int] = []
    else:
        lengths, off_beat = on_the_beat(planned, held, beats)
    made = []
    start = 0.0
    for clip, length in zip(clips, lengths, strict=True):
        assert clip.scene is not None
        end = round(start + length, 3)
        made.append(
            assembly.Cut(
                scene=clip.scene.number,
                clip=clip.pk,
                clip_start=0.0,
                clip_end=length,
                start=start,
                end=end,
                overlay=clip.scene.overlay,
                voice_clip_start=0.0,
                voice_clip_end=0.0,
                voice_start=start,
                voice_end=end,
            )
        )
        start = end
    return made, [made[position].scene for position in off_beat]


@dataclass(frozen=True)
class Beats:
    """The beats found in the ad's music, or None and why not."""

    times: list[float] | None
    missing: str = ""


def music_beats(music: ProducedItem) -> Beats:
    """The beats of the ad's music (jobs.beats)."""
    try:
        times = find_beats(file_store.read(music.file))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as failed:
        said = failed.stderr.decode(errors="replace").strip() if failed.stderr else ""
        return Beats(None, f"ffmpeg couldn't read the music ({said or failed})")
    return Beats(times, "" if times is not None else "no clear beat was found in the music")
