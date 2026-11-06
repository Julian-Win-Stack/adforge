"""Remove only a clip's leading false start, before its picture and words are judged, and
record how fast the clip spoke."""

import json
import math
import tempfile
from pathlib import Path

from django.db import transaction

from adforge import file_store
from chat.models import Message

from . import assembly, notices, pace, product_videos, word_check
from .models import ProducedItem, SceneStep

LEADING_WORDS_CUT = notices.Kind("leading_words_cut", for_owner=False, failure=False)
LEADING_WORDS_NOT_CUT = notices.Kind("leading_words_not_cut", for_owner=False)
# How fast a clip spoke its line. Only measured: a slow clip is never failed or filmed again,
# as pace is set before filming by the clip's length and the voice words (Julian, Nov 6).
PACE = notices.Kind("pace", for_owner=False, failure=False)
# Leave a small breath before the first word rather than cutting into its consonant.
BEFORE_FIRST_WORD_SECONDS = 0.05


def trim_leading_words(step: SceneStep, clip: ProducedItem, transcript: ProducedItem) -> None:
    """Keep the same clip version with its sound and transcript shifted by the same cut.

    The transcript keeps the outcome because the clip judge replaces the clip's ruling.
    Updating both records and the notice together makes a resumed film keep the cut once.
    The generated original and the full transcription remain in their model-call records.
    """
    if "leading_words_cut" in (transcript.ruling or {}):
        return
    wanted = word_check.leading_words(step.line, transcript.text.split())
    heard = transcript.words
    first = word_check.leading_words(step.line, [word["text"] for word in heard])
    if not wanted and not first:
        return
    try:
        if not heard or not first:
            raise ValueError("The transcript has no word times locating the line's first word.")
        kept = heard[first:]
        for word in kept:
            start, end = word.get("start"), word.get("end")
            if (
                not isinstance(start, (int, float))
                or not isinstance(end, (int, float))
                or not math.isfinite(start)
                or not math.isfinite(end)
                or start < 0
                or end < start
            ):
                raise ValueError("The transcript's word times cannot be used for a cut.")
        removed = max(0.0, kept[0]["start"] - BEFORE_FIRST_WORD_SECONDS)
        if clip.seconds is None or removed <= 0 or kept[-1]["end"] > clip.seconds:
            raise ValueError("The word times do not leave the whole line within the clip.")
        trimmed = product_videos.cut_part(clip.file, removed, clip.seconds, sound=True)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "clip.mp4"
            path.write_bytes(file_store.read(trimmed))
            seconds = assembly.seconds_of(path)
        if seconds <= 0 or seconds + removed + 0.05 < kept[-1]["end"]:
            raise ValueError("The cut did not keep the line's last word.")
        shifted = [
            {
                **word,
                "start": round(word["start"] - removed, 3),
                "end": round(word["end"] - removed, 3),
            }
            for word in kept
        ]
    except Exception as error:
        with transaction.atomic():
            transcript.ruling = {
                **(transcript.ruling or {}),
                "leading_words_cut": {"error": str(error)},
            }
            transcript.save(update_fields=["ruling"])
            notices.post_notice(
                step.scene.job,
                LEADING_WORDS_NOT_CUT,
                f"Scene {step.scene.number}'s leading words were not cut: {error}",
                Message.Level.PROBLEM,
                scene=step.scene.number,
            )
        return
    record = {
        "seconds_removed": removed,
        "words_removed": " ".join(word["text"] for word in heard[:first]),
        "original_file": clip.file,
    }
    with transaction.atomic():
        clip.file, clip.seconds = trimmed, seconds
        clip.save(update_fields=["file", "seconds"])
        transcript.text = " ".join(word["text"] for word in kept)
        transcript.words = shifted
        transcript.ruling = {**(transcript.ruling or {}), "leading_words_cut": record}
        transcript.save(update_fields=["text", "words", "ruling"])
        notices.post_notice(
            step.scene.job,
            LEADING_WORDS_CUT,
            json.dumps(record),
            Message.Level.INFO,
            scene=step.scene.number,
        )


def record_pace(step: SceneStep, clip: ProducedItem, transcript: ProducedItem) -> None:
    """Keep the words a second `clip` spoke, from its transcript's word times, with the
    clip's verdicts and in a developer notice, once. Run after the clip judge, which
    replaces the clip's ruling."""
    if "pace" in (clip.ruling or {}):
        return
    words = transcript.words or []
    if not words:
        # Nothing was heard: the word check reports a line that wasn't said.
        return
    measured = pace.spoken_pace(words)
    record = {
        "words_per_second": measured,
        "words": len(words),
        "planned_words_per_second": pace.FIXED_WORDS_PER_SECOND,
    }
    with transaction.atomic():
        clip.ruling = {**(clip.ruling or {}), "pace": record}
        clip.save(update_fields=["ruling"])
        notices.post_notice(
            step.scene.job,
            PACE,
            json.dumps({"scene": step.scene.number, "clip": clip.pk, **record}),
            Message.Level.INFO,
            scene=step.scene.number,
        )
