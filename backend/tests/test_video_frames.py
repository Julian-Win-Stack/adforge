"""Thinning a product video's frames: how the frames to keep are shared between its shots,
and which frame near each spot is kept. Frames are made as pictures here; the whole video
is thinned through the chat in test_product_videos.py."""

import io
import math
import random
from pathlib import Path

import PIL.Image
import PIL.ImageFilter
import pytest

from jobs import video_frames


def snow(seed: int, *, blurred: bool = False) -> bytes:
    """A picture of grey waves sprinkled with fine dots: no two seeds look alike, and
    blurred it loses its dots but keeps its waves."""
    dots = random.Random(seed)
    across, down, shift = dots.uniform(0.05, 0.2), dots.uniform(0.05, 0.2), dots.uniform(0, 6)
    image = PIL.Image.new("L", (96, 96))
    image.putdata(
        [
            round(128 + 90 * math.sin(x * across + y * down + shift)) + dots.randrange(-10, 11)
            for y in range(96)
            for x in range(96)
        ]
    )
    if blurred:
        image = image.filter(PIL.ImageFilter.GaussianBlur(3))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=95)
    return out.getvalue()


def frames_in(folder: Path, pictures: list[bytes]) -> list[Path]:
    files = []
    for number, picture in enumerate(pictures):
        file = folder / f"{number:05d}.jpg"
        file.write_bytes(picture)
        files.append(file)
    return files


def test_frames_are_shared_between_moving_shots_by_length() -> None:
    # 10 extra frames over 60 moving frames: 1.67, 5 and 3.33; the largest remainder rounds up.
    assert video_frames.share([10, 5, 30, 20], [True, False, True, True], 10) == [2, 0, 5, 3]


def test_a_shot_never_gets_more_frames_than_it_has() -> None:
    assert video_frames.share([3, 4], [True, True], 20) == [2, 3]
    assert video_frames.share([1, 6], [True, True], 3) == [0, 3]


def test_a_blurred_picture_is_less_sharp() -> None:
    assert video_frames.sharpness(snow(1)) > 5 * video_frames.sharpness(snow(1, blurred=True))
    flat = io.BytesIO()
    PIL.Image.new("L", (40, 40), 128).save(flat, "PNG")
    assert video_frames.sharpness(flat.getvalue()) == pytest.approx(0)


def test_each_spot_keeps_the_sharpest_frame_near_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(video_frames, "MOST_FRAMES", 3)
    sharp = {0, 9, 11}
    # One moving shot of 15 frames, in three parts of five, one sharp frame in each.
    frames = frames_in(tmp_path, [snow(n, blurred=n not in sharp) for n in range(15)])

    assert video_frames.thin(frames, [0.0] * 15) == [0, 9, 11]


def test_more_shots_than_frames_keep_the_longest_shots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(video_frames, "MOST_FRAMES", 2)
    frames = frames_in(tmp_path, [snow(n) for n in range(11)])
    # Shots of 2, 5 and 4 frames.
    scores = [0.0] * 11
    scores[2] = scores[7] = 0.9

    kept = video_frames.thin(frames, scores)

    assert len(kept) == 2
    assert 2 <= kept[0] <= 6 and 7 <= kept[1] <= 10


def test_sharpness_is_the_variance_of_the_laplacian() -> None:
    # One lit dot: the Laplacian is -400 on it, +100 beside it and 0 at the corners.
    dot = PIL.Image.new("L", (5, 5))
    dot.putpixel((2, 2), 100)
    out = io.BytesIO()
    dot.save(out, "PNG")
    assert video_frames.sharpness(out.getvalue()) == pytest.approx((400**2 + 4 * 100**2) / 9)


def test_a_short_video_keeps_the_sharpest_of_its_copies_wherever_it_is(tmp_path: Path) -> None:
    # One still shot: the same picture, sharp only in its first frame.
    frames = frames_in(tmp_path, [snow(1, blurred=n > 0) for n in range(9)])

    assert video_frames.thin(frames, [0.0] * 9) == [0]


def picture_with_fingerprint(bits: list[int]) -> bytes:
    """A 17 by 16 picture whose fingerprint (photos.dhash) is exactly `bits`."""
    image = PIL.Image.new("L", (17, 16))
    for y in range(16):
        shade = 128
        image.putpixel((0, y), shade)
        for x in range(16):
            shade += -4 if bits[y * 16 + x] else 4
            image.putpixel((x + 1, y), shade)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_copies_are_frames_at_most_sixteen_bits_apart_and_the_sharpest_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # B is 16 bits from A; C is 16 bits from B and 32 from A; D is 17 bits from every other.
    a = [0] * 256
    b = [1] * 16 + [0] * 240
    c = [1] * 32 + [0] * 224
    d = [0] * 128 + [1] * 17 + [0] * 111
    pictures = [picture_with_fingerprint(bits) for bits in (a, b, c, d)]
    sharper = {pictures[0]: 1.0, pictures[1]: 3.0, pictures[2]: 2.0, pictures[3]: 1.0}
    monkeypatch.setattr(video_frames, "sharpness", lambda content: sharper[content])

    # A and B are copies and B is sharper; C is then a copy of B; D is no one's copy.
    assert video_frames.thin(frames_in(tmp_path, pictures), [0.0] * 4) == [1, 3]
