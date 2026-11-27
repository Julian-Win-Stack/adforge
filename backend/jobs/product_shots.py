"""A product-shots ad (spec #767): silent scenes of the product, overlays drawn by code, music
only, every cut on a beat of the music. Its planning check, its music, its beats and its
assembly; its scenes are made by the scene agent (agents/scene_agent/silent.py)."""

import array
import math
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db.models import Max

from adforge import file_store
from chat.models import Message

from . import assembly, notices
from .checks import LineToCheck
from .models import Job, ProducedItem, Scene
from .notices import post_notice
from .planning import END_CARD_SECONDS

PRODUCT_SHOTS = "product_shots"
PERSON_TALKING = "person_talking"
STYLE_LABELS = {PRODUCT_SHOTS: "Product shots", PERSON_TALKING: "Person talking"}
# The beat the music is asked for, so a short ad has room for a cut every two beats.
MUSIC_BPM = 100
# The music is a little longer than the ad, so it never runs out before the end card ends.
MUSIC_SPARE_SECONDS = 3
# Music played alone, with no voice over it, is played near full.
MUSIC_ALONE_VOLUME = 0.9

MUSIC_PROMPT = (
    "{mood}. Instrumental music for a short, beautiful product video ad with no voice over it. "
    "A steady, clear beat at about {bpm} BPM from the first second. Instrumental only, no "
    "vocals, no singing, no spoken words."
)


def is_product_shots(job: Job) -> bool:
    return job.ad_style == PRODUCT_SHOTS


def planned_seconds(scene: Scene) -> float:
    return float((scene.silent or {}).get("seconds") or 3)


def ad_seconds(job: Job) -> float:
    """The planned length: every scene's length plus the end card."""
    return sum(planned_seconds(scene) for scene in job.scenes.all()) + END_CARD_SECONDS


def music_prompt(mood: str) -> str:
    return MUSIC_PROMPT.format(mood=mood, bpm=MUSIC_BPM)


def music_seconds(job: Job) -> int:
    return math.ceil(ad_seconds(job) + MUSIC_SPARE_SECONDS)


# The planning check (#770): the fact check that runs on spoken lines runs on the overlays.


def run_checks(job: Job) -> str | None:
    """Fact-check every unchecked scene's overlay, and the end card with the last scene,
    against the page and what the owner said. None once every scene passed, else what failed,
    for the producer to fix."""
    from . import work

    scenes = list(job.scenes.order_by("number"))
    last = scenes[-1] if scenes else None
    to_check = [
        scene
        for scene in scenes
        if not scene.fact_checked and (scene.overlay.strip() or scene is last)
    ]
    for scene in scenes:
        if not scene.fact_checked and scene not in to_check:
            Scene.objects.filter(pk=scene.pk).update(fact_checked=True)
    if not to_check:
        return None
    lines = [
        LineToCheck(
            scene=scene.number,
            line=scene.overlay.strip() or "(no words on screen in this scene)",
            end_card_line=job.end_card_line if scene is last else None,
            end_card_price=job.end_card_price if scene is last else None,
        )
        for scene in to_check
    ]
    check = work._ask_fact_check(job, lines, work._conversation(job))
    problems = []
    for verdict in check.lines:
        scene = next(each for each in to_check if each.number == verdict.scene)
        if verdict.verdict == "ok":
            Scene.objects.filter(pk=scene.pk).update(fact_checked=True)
            continue
        Scene.objects.filter(pk=scene.pk).update(
            fact_problems=[
                *scene.fact_problems,
                {
                    "wrong": "line",
                    "problem": verdict.problem,
                    "page_says": verdict.page_says,
                    "rewritten": False,
                },
            ]
        )
        problems.append(
            f'Scene {scene.number}\'s overlay "{scene.overlay}" (or the end card): '
            f"{verdict.problem} The page says: {verdict.page_says}"
        )
    if not problems:
        job.status = Job.Status.READY_TO_RENDER
        job.save(update_fields=["status"])
        return None
    return " ".join(problems)


# Beats (#773): found in the music track by code, with no model.

SAMPLE_RATE = 11025
HOP = 256
FASTEST_BPM, SLOWEST_BPM = 160, 60
# How far the tempo's autocorrelation peak must stand above the average for a clear beat.
CLEAR_BEAT = 1.5
# A rise in log loudness at least this big, from one hop to the next, is a sharp onset.
STRONG_RISE = 0.1


def _samples(file: Path) -> array.array[int]:
    decoded = subprocess.run(
        [
            settings.FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(file),
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=120,
    ).stdout
    samples = array.array("h")
    samples.frombytes(decoded[: len(decoded) // 2 * 2])
    return samples


def find_beats(file: Path) -> list[float] | None:
    """The beat times in the music at `file`, in seconds, or None when it has no clear beat:
    an onset envelope from the rise in loudness, the tempo from its autocorrelation, and the
    phase that puts the most onsets on the beat."""
    samples = _samples(file)
    frames = len(samples) // HOP
    if frames < 50:
        return None
    energy = [
        math.log1p(sum(abs(value) for value in samples[i * HOP : (i + 1) * HOP]) / HOP)
        for i in range(frames)
    ]
    raw = [0.0] + [max(0.0, energy[i] - energy[i - 1]) for i in range(1, frames)]
    per_second = SAMPLE_RATE / HOP
    # A held tone or a wash of sound has almost no sharp rise: no beat to cut on.
    if sum(1 for value in raw if value > STRONG_RISE) < frames / per_second / 2:
        return None
    mean = sum(raw) / len(raw)
    onset = [value - mean for value in raw]
    shortest = int(per_second * 60 / FASTEST_BPM)
    longest = int(per_second * 60 / SLOWEST_BPM)
    scores = {}
    for lag in range(shortest, longest + 1):
        scores[lag] = sum(onset[i] * onset[i - lag] for i in range(lag, frames)) / (frames - lag)
    positive = [score for score in scores.values() if score > 0]
    if not positive:
        return None
    best = max(scores, key=lambda lag: scores[lag])
    if scores[best] < CLEAR_BEAT * (sum(positive) / len(positive)):
        return None
    phase = max(range(best), key=lambda start: sum(onset[start::best]))
    # Each beat of the grid moves to the strongest rise near it, so a tempo a hair off the
    # grid's whole frames doesn't drift away from the music.
    beats = []
    for index in range(phase, frames, best):
        near = range(max(0, index - 3), min(frames, index + 4))
        beats.append(round(max(near, key=lambda at: raw[at]) / per_second, 3))
    return sorted(set(beats))


def snapped(
    planned: Sequence[float], clips: Sequence[float], beats: Sequence[float]
) -> list[float]:
    """Each scene's length so every cut lands on a beat: the cut nearest the planned one,
    at least one beat after the cut before, never past what the clip holds when a beat fits."""
    lengths = []
    start = 0.0
    planned_cut = 0.0
    for want, held in zip(planned, clips, strict=True):
        planned_cut += want
        later = [beat for beat in beats if beat > start + 0.25]
        if not later:
            lengths.append(round(want, 3))
            start += want
            continue
        fits = [beat for beat in later if beat - start <= held] or later[:1]
        cut = min(fits, key=lambda beat: abs(beat - planned_cut))
        lengths.append(round(cut - start, 3))
        start = cut
    return lengths


# Assembly (#771).


def assemble(job: Job, clips: Sequence[ProducedItem], music: ProducedItem) -> ProducedItem:
    """Join the silent scenes' clips, each cut to its length (on the beat when the music has
    one), with each scene's overlay, no captions and none of the clips' own sound, the music
    alone under them, then the end card. Kept as the next finished ad, with no model call."""
    from . import work

    planned = [planned_seconds(clip.scene) for clip in clips if clip.scene is not None]
    held = [float(clip.seconds or 0.0) for clip in clips]
    with tempfile.TemporaryDirectory() as folder:
        under = Path(folder) / "music"
        under.write_bytes(file_store.read(music.file))
        beats = find_beats(under)
        if beats is None:
            post_notice(
                job,
                notices.BEATS_NOT_FOUND,
                "No clear beat was found in the music, so the product-shots ad is cut at the "
                "planned scene lengths.",
                Message.Level.PROBLEM,
            )
            lengths = planned
        else:
            lengths = snapped(planned, held, beats)
        cuts = []
        start = 0.0
        for clip, length in zip(clips, lengths, strict=True):
            assert clip.scene is not None
            end = round(start + length, 3)
            cuts.append(
                assembly.Cut(
                    scene=clip.scene.number,
                    clip=clip.pk,
                    clip_start=0.0,
                    clip_end=round(min(length, clip.seconds or length), 3),
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
        card = work.end_card_of(job, cuts)
        photo = work.end_card_photo(job)
        parts = []
        for clip, cut in zip(clips, cuts, strict=True):
            path = Path(folder) / f"scene-{cut.scene}.mp4"
            path.write_bytes(file_store.read(clip.file))
            parts.append((path, cut))
        on_the_card = None
        if photo is not None:
            try:
                flat = assembly.flat_photo(file_store.read(photo.file))
            except ValueError:
                photo = None
            else:
                on_the_card = Path(folder) / "end-card-photo.png"
                on_the_card.write_bytes(flat)
        ad = Path(folder) / "ad.mp4"
        assembly.join(
            parts,
            ad,
            music=under,
            drawn=[],
            end_card=card,
            photo=on_the_card,
            clip_sound=False,
            music_volume=MUSIC_ALONE_VOLUME,
        )
        file = file_store.save("ad.mp4", ad.read_bytes())
    last = job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).aggregate(last=Max("version"))
    record: dict[str, Any] = work.end_card_record(card, photo)
    record["beats"] = beats[:80] if beats is not None else None
    return ProducedItem.objects.create(
        job=job,
        kind=ProducedItem.Kind.FINISHED_AD,
        version=(last["last"] or 0) + 1,
        file=file,
        made_from=music,
        seconds=card.end,
        cuts=[asdict(cut) for cut in cuts],
        captions=[],
        end_card=record,
    )
