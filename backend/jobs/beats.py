"""The beats of a product-shots ad's music, found by code with no model, and the scene lengths
that put every cut on one (#773).

The beats come from the rise in loudness from one short slice of the track to the next (the
onset envelope): its tempo is the lag at which the envelope best matches itself, and its
phase the start that puts the most rises on that grid. A held tone or a wash of sound has no
sharp rises, or no lag that stands out, so it has no clear beat."""

import array
import itertools
import math
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from django.conf import settings

SAMPLE_RATE = 11025
# Samples in one slice of the onset envelope: about 23 ms.
HOP = 256
SLICES_PER_SECOND = SAMPLE_RATE / HOP
FASTEST_BPM, SLOWEST_BPM = 160, 60
# How far the best lag's match must stand above the average lag's for a clear beat.
CLEAR_BEAT = 1.5
# A rise in log loudness at least this big, from one slice to the next, is a sharp onset;
# a track with fewer than one every two seconds has no beat to cut on.
STRONG_RISE = 0.1
# The track must be at least this long to find a tempo in it.
SHORTEST_TRACK_SECONDS = 2.0
# A lag near half the best one whose match is at least this share of the best one's is the
# beat, the best one every second beat.
HALF_AS_GOOD = 0.6
# How many slices either side of where a beat is due its rise may be.
NEAR = 3
# A scene lasts at least one beat: this share of the music's usual gap between beats, so a
# beat a little early still counts.
ONE_BEAT_SHARE = 0.9


def find_beats(music: bytes) -> list[float] | None:
    """The beat times in `music`, an audio file, in seconds, or None when it has no clear
    beat."""
    samples = _samples(music)
    slices = len(samples) // HOP
    if slices < SHORTEST_TRACK_SECONDS * SLICES_PER_SECOND:
        return None
    loudness = [
        math.log1p(sum(abs(value) for value in samples[i * HOP : (i + 1) * HOP]) / HOP)
        for i in range(slices)
    ]
    rises = [0.0] + [max(0.0, loudness[i] - loudness[i - 1]) for i in range(1, slices)]
    if sum(1 for rise in rises if rise > STRONG_RISE) < slices / SLICES_PER_SECOND / 2:
        return None
    # Each rise spread over its neighbours, so a beat that falls between two slices still
    # meets itself one lag later.
    spread = [max(rises[max(0, i - 1) : i + 2]) for i in range(slices)]
    mean = sum(spread) / slices
    onset = [value - mean for value in spread]
    shortest = int(SLICES_PER_SECOND * 60 / FASTEST_BPM)
    longest = int(SLICES_PER_SECOND * 60 / SLOWEST_BPM)
    matches = {
        lag: sum(onset[i] * onset[i - lag] for i in range(lag, slices)) / (slices - lag)
        for lag in range(shortest, longest + 1)
    }
    positive = [match for match in matches.values() if match > 0]
    if not positive:
        return None
    best = max(matches.values())
    if best < CLEAR_BEAT * sum(positive) / len(positive):
        return None
    lag = max(matches, key=matches.__getitem__)
    # A track that beats every half second matches itself every second just as well, or a
    # little better when the half second falls between two slices: a good match near half
    # the best lag is the beat.
    halves = [each for each in range(lag // 2 - 1, lag // 2 + 2) if each in matches]
    if halves and max(matches[each] for each in halves) >= HALF_AS_GOOD * best:
        lag = max(halves, key=matches.__getitem__)
    phase = max(range(lag), key=lambda first: sum(onset[first::lag]))
    # Each beat is the strongest rise near one lag after the beat before, so a tempo between
    # two whole slices doesn't drift away from the music.
    beats = []
    at = phase
    while at < slices:
        at = max(range(max(0, at - NEAR), min(slices, at + NEAR + 1)), key=rises.__getitem__)
        beats.append(round(at / SLICES_PER_SECOND, 3))
        at += lag
    return sorted(set(beats))


def on_the_beat(
    planned: Sequence[float], held: Sequence[float], beats: Sequence[float]
) -> tuple[list[float], list[int]]:
    """Each scene's length so its cut lands on a beat, and the scenes (by position, from 0)
    whose cut couldn't. A cut goes on the beat nearest its planned place, at least a beat
    after the cut before and within what its clip holds; a clip shorter than planned moves
    every later planned place earlier by what it lacks, so the scenes after it keep their
    lengths and the ad is that much shorter. A scene with no such beat (past the music's last
    beat) keeps its planned length, or its clip's if shorter."""
    gaps = sorted(later - earlier for earlier, later in itertools.pairwise(beats))
    shortest = ONE_BEAT_SHARE * gaps[len(gaps) // 2] if gaps else 0.0
    lengths = []
    off_beat = []
    start = 0.0
    planned_cut = 0.0
    for position, (want, most) in enumerate(zip(planned, held, strict=True)):
        planned_cut += min(want, most)
        fits = [beat for beat in beats if start + shortest <= beat <= start + most]
        if fits:
            length = min(fits, key=lambda beat: abs(beat - planned_cut)) - start
        else:
            length = min(want, most)
            off_beat.append(position)
        lengths.append(round(length, 3))
        start = round(start + length, 3)
    return lengths, off_beat


def _samples(music: bytes) -> array.array[int]:
    """`music` decoded to one channel of 16-bit samples at SAMPLE_RATE."""
    with tempfile.TemporaryDirectory() as folder:
        track = Path(folder) / "music"
        track.write_bytes(music)
        decoded = subprocess.run(
            [
                settings.FFMPEG,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(track),
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
