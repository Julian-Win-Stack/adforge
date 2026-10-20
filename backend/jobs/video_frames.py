"""Thinning a product video to at most MOST_FRAMES of its frames, so that one look at them
sees the whole video: every shot, a still shot once, a moving shot more often the longer it
runs, each frame the sharpest near its spot, and no two frames nearly the same picture.

The video's frames are taken FRAMES_PER_SECOND times a second. A shot ends where ffmpeg's
scene score between two neighbouring frames reaches SCENE_SCORE: a fingerprint jump can't
tell a cut from fast movement inside one shot, and the scene score can."""

import functools
import io
import itertools
import math
from collections.abc import Callable, Sequence
from pathlib import Path

from PIL import Image, ImageMath

from jobs.photos import dhash

# The most frames a video is thinned to.
MOST_FRAMES = 80
# How many frames a second are taken from the video to thin.
FRAMES_PER_SECOND = 5
# ffmpeg's scene score, between two neighbouring frames, from which they are in two shots.
SCENE_SCORE = 0.25
# Two frames whose fingerprints are at most this many bits apart are the same picture.
COPY_BITS = 16
# How many frames either side of its spot a frame may be picked from for being sharper.
NEAR = 2


def thin(frames: Sequence[Path], scene_scores: Sequence[float]) -> list[int]:
    """The numbers of the frames kept, first to last, out of a video's frames taken
    FRAMES_PER_SECOND times a second; `scene_scores[n]` is ffmpeg's scene score between
    frame n and the one before it. A video of MOST_FRAMES frames or fewer keeps every frame
    but copies."""
    hashes = [dhash(frame.read_bytes()) for frame in frames]

    @functools.cache
    def sharpness_of(number: int) -> float:
        return sharpness(frames[number].read_bytes())

    if len(frames) <= MOST_FRAMES:
        return _without_copies(range(len(frames)), hashes, sharpness_of)
    shots = _shots(len(frames), scene_scores)
    if len(shots) > MOST_FRAMES:
        # More shots than frames to keep: the longest shots, one frame each.
        longest = sorted(shots, key=len, reverse=True)[:MOST_FRAMES]
        shots = sorted(longest, key=lambda shot: shot.start)
    moving = [not all(_same(hashes[n], hashes[shot.start]) for n in shot) for shot in shots]
    more = share([len(shot) for shot in shots], moving, MOST_FRAMES - len(shots))
    spots = {
        spot
        for shot, extra in zip(shots, more, strict=True)
        for spot in _spots(shot, 1 + extra, sharpness_of)
    }
    return _without_copies(sorted(spots), hashes, sharpness_of)


def share(lengths: Sequence[int], moving: Sequence[bool], frames: int) -> list[int]:
    """How many of `frames` each shot gets on top of its one frame: shared between the
    moving shots by their lengths (the largest remainders rounding up), never more than a
    shot has left. A still shot gets none."""
    given = [0] * len(lengths)
    room = [length - 1 if move else 0 for length, move in zip(lengths, moving, strict=True)]
    left = frames
    while left > 0:
        open_shots = [n for n in range(len(lengths)) if given[n] < room[n]]
        if not open_shots:
            break
        total = sum(lengths[n] for n in open_shots)
        wanted = {n: left * lengths[n] / total for n in open_shots}
        for n in open_shots:
            whole = min(math.floor(wanted[n]), room[n] - given[n])
            given[n] += whole
            left -= whole
        by_remainder = sorted(open_shots, key=lambda n: wanted[n] - math.floor(wanted[n]))
        for n in reversed(by_remainder):
            if left > 0 and given[n] < room[n]:
                given[n] += 1
                left -= 1
    return given


def sharpness(content: bytes) -> float:
    """How sharp a picture is: how much its edges stand out, as the variance of the
    Laplacian of its black and white version. A blurred picture scores lower."""
    with Image.open(io.BytesIO(content)) as image:
        grey = image.convert("L").convert("F")
    width, height = grey.size
    laplacian = ImageMath.lambda_eval(
        lambda a: a["up"] + a["down"] + a["left"] + a["right"] - a["middle"] * 4,
        up=grey.crop((1, 0, width - 1, height - 2)),
        down=grey.crop((1, 2, width - 1, height)),
        left=grey.crop((0, 1, width - 2, height - 1)),
        right=grey.crop((2, 1, width, height - 1)),
        middle=grey.crop((1, 1, width - 1, height - 1)),
    )
    squared = ImageMath.lambda_eval(lambda a: a["x"] * a["x"], x=laplacian)
    return float(_mean(squared) - _mean(laplacian) ** 2)


def _mean(image: Image.Image) -> float:
    return float(image.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0)))  # type: ignore[arg-type]


def _same(one: int, other: int) -> bool:
    return (one ^ other).bit_count() <= COPY_BITS


def _shots(count: int, scene_scores: Sequence[float]) -> list[range]:
    cuts = [n for n in range(1, count) if n < len(scene_scores) and scene_scores[n] >= SCENE_SCORE]
    bounds = [0, *cuts, count]
    return [range(start, end) for start, end in itertools.pairwise(bounds)]


def _spots(shot: range, picks: int, sharpness_of: Callable[[int], float]) -> list[int]:
    """`picks` frames spread evenly over the shot: each the sharpest within NEAR frames of
    the middle of its own equal part."""
    length = len(shot)
    chosen = []
    for part in range(picks):
        low = shot.start + math.floor(part * length / picks)
        high = max(low, shot.start + math.floor((part + 1) * length / picks) - 1)
        middle = shot.start + int((part + 0.5) * length / picks)
        near = range(max(low, middle - NEAR), min(high, middle + NEAR) + 1)
        chosen.append(max(near, key=sharpness_of))
    return chosen


def _without_copies(
    numbers: Sequence[int], hashes: Sequence[int], sharpness_of: Callable[[int], float]
) -> list[int]:
    """The frames left when each group of copies keeps only its sharpest frame, and then no
    two are the same picture."""
    groups: list[list[int]] = []
    for number in numbers:
        group = next((group for group in groups if _same(hashes[number], hashes[group[0]])), None)
        if group is None:
            groups.append([number])
        else:
            group.append(number)
    kept: list[int] = []
    for number in sorted(max(group, key=sharpness_of) for group in groups):
        if not any(_same(hashes[number], hashes[other]) for other in kept):
            kept.append(number)
    return kept
