"""Finding a track's beats and putting every cut on one (#773): pure logic, and the music
decoded by ffmpeg."""

import io
import wave

import pytest

from gateway.fake import _tone_frames, _with_knocks
from jobs.beats import find_beats, on_the_beat

RATE = 8000


def track(seconds: float, beat_every: float | None) -> bytes:
    frames = _tone_frames(seconds, RATE, hertz=110)
    if beat_every is not None:
        frames = _with_knocks(frames, RATE, 0.3, beat_every)
    out = io.BytesIO()
    with wave.open(out, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(RATE)
        file.writeframes(frames)
    return out.getvalue()


def test_the_beats_of_a_steady_track_are_found() -> None:
    beats = find_beats(track(10, 0.5))

    assert beats is not None
    expected = [round(0.3 + 0.5 * n, 3) for n in range(20)]
    assert len(beats) == len(expected)
    assert all(abs(found - want) <= 1 / 30 for found, want in zip(beats, expected, strict=True))


def test_a_held_tone_has_no_beat() -> None:
    assert find_beats(track(10, None)) is None


GRID = [round(0.3 + 0.6 * n, 3) for n in range(22)]


@pytest.mark.parametrize(
    ("planned", "held", "beats", "lengths", "off_beat"),
    [
        # Each cut on the beat nearest its planned place.
        ([4, 5, 4], [5, 5, 5], GRID, [3.9, 4.8, 4.2], []),
        # A cut never past what its clip holds: scene 1's nearest beat, 4.5, is past 4.2.
        ([4.3, 4], [4.2, 5], GRID, [3.9, 4.2], []),
        # A scene planned shorter than a beat still keeps one.
        ([0.1, 4], [5, 5], GRID, [0.9, 3.0], []),
        # A short clip shortens the ad: the scenes after it keep their planned lengths.
        ([4, 5, 4], [2, 10, 10], GRID, [1.5, 5.4, 4.2], []),
        # Past the last beat, a scene keeps its planned length, off the beat.
        ([4, 5], [5, 5], GRID[:7], [3.9, 5], [1]),
    ],
)
def test_every_cut_lands_on_the_beat_nearest_its_planned_place(
    planned: list[float],
    held: list[float],
    beats: list[float],
    lengths: list[float],
    off_beat: list[int],
) -> None:
    assert on_the_beat(planned, held, beats) == (lengths, off_beat)


def test_music_ffmpeg_cant_read_has_no_beats_and_says_why() -> None:
    from types import SimpleNamespace

    from adforge import file_store
    from jobs.product_shots import music_beats

    music = SimpleNamespace(file=file_store.save("music.mp3", b"not music"))

    found = music_beats(music)  # type: ignore[arg-type]

    assert found.times is None and "ffmpeg couldn't read the music" in found.missing
