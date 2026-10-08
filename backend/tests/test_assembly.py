"""Putting the finished ad together with ffmpeg, called directly on tiny generated clips
and music: what the chat can't easily set up, such as music that runs out before the ad."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings

from gateway.fake import FakeModel
from jobs import assembly

from .conftest import colour_at, loudness

# The fake's pitches: a test hears the voice and the music apart by them.
VOICE_HERTZ = FakeModel.VOICE_HERTZ
MUSIC_HERTZ = FakeModel.MUSIC_HERTZ


def generate(into: Path, *inputs: str, seconds: float) -> Path:
    """A real tiny media file made by ffmpeg from generated `inputs`, lasting `seconds`."""
    lavfi = [arg for source in inputs for arg in ("-f", "lavfi", "-i", source)]
    subprocess.run(
        [settings.FFMPEG, "-hide_banner", "-loglevel", "error", *lavfi, "-t", str(seconds)]
        + (["-pix_fmt", "yuv420p"] if into.suffix == ".mp4" else [])
        + [str(into)],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return into


def clip(folder: Path, name: str, *, seconds: float, colour: str = "red") -> Path:
    """A clip of one colour whose voice is a steady tone all the way through."""
    return generate(
        folder / f"{name}.mp4",
        f"color=c={colour}:s=72x128:r=25",
        f"sine=frequency={VOICE_HERTZ}:sample_rate=8000",
        seconds=seconds,
    )


def sized_clip(folder: Path, name: str, *, size: str, fps: int, seconds: float) -> Path:
    """A clip as a video model makes it: `size` such as 720x1280, at `fps` frames a second,
    with a steady tone for its voice."""
    return generate(
        folder / f"{name}.mp4",
        f"color=c=red:s={size}:r={fps}",
        f"sine=frequency={VOICE_HERTZ}:sample_rate=8000",
        seconds=seconds,
    )


def frame(video: Path) -> tuple[int, int, str, str]:
    """A video's width, height, frame rate and average frame rate, as ffprobe reads them.
    The two rates agree when every frame is shown for the same time."""
    probed = subprocess.run(
        [settings.FFPROBE, "-v", "error", "-select_streams", "v", "-show_streams", "-of", "json"]
        + [str(video)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    (stream,) = json.loads(probed.stdout)["streams"]
    return stream["width"], stream["height"], stream["r_frame_rate"], stream["avg_frame_rate"]


def music(folder: Path, *, seconds: float) -> Path:
    """Music that hums steadily for `seconds`."""
    return generate(
        folder / "music.wav", f"sine=frequency={MUSIC_HERTZ}:sample_rate=8000", seconds=seconds
    )


def a_cut(
    scene: int,
    *,
    picture: tuple[float, float, float, float],
    voice: tuple[float, float, float, float] | None = None,
) -> assembly.Cut:
    """Scene `scene`'s cut: the part of its clip's `picture` kept and where it plays in the
    ad, and the same for its `voice`, as (from, to in the clip, start, end in the ad): the
    picture's own, unless the voice is given."""
    clip_start, clip_end, start, end = picture
    voice_clip_start, voice_clip_end, voice_start, voice_end = voice or picture
    return assembly.Cut(
        scene=scene,
        clip=scene,
        clip_start=clip_start,
        clip_end=clip_end,
        start=start,
        end=end,
        overlay="",
        voice_clip_start=voice_clip_start,
        voice_clip_end=voice_clip_end,
        voice_start=voice_start,
        voice_end=voice_end,
    )


def one_scene(seconds: float) -> assembly.Cut:
    """An ad of one scene whose clip is kept whole."""
    return a_cut(1, picture=(0.0, seconds, 0.0, seconds))


def test_music_that_runs_out_before_the_ad_ends_fades_out_rather_than_stopping_dead(
    tmp_path: Path,
) -> None:
    parts = [(clip(tmp_path, "scene-1", seconds=9), one_scene(9.0))]
    ad = tmp_path / "ad.mp4"

    assembly.join(parts, ad, music=music(tmp_path, seconds=5), drawn=[])

    heard = ad.read_bytes()
    playing = loudness(heard, "music", between=(1, 2))
    fading = loudness(heard, "music", between=(4, 5))
    gone = loudness(heard, "music", between=(6, 9))
    # Full volume, then quieter but still there over its last 2 seconds, then nothing: as
    # near nothing as the voice's tone leaking faintly into the music's band allows.
    assert playing > -40
    assert playing - 20 < fading < playing - 6
    assert gone < playing - 25
    # The voice is untouched: heard as loud at the end as at the start.
    assert loudness(heard, "voice", between=(7, 9)) == pytest.approx(
        loudness(heard, "voice", between=(0, 2)), abs=1
    )


def test_clips_of_different_sizes_and_frame_rates_are_joined_into_one_ad_at_1080p_25fps(
    tmp_path: Path,
) -> None:
    # A talking clip as HeyGen makes it, then a B-roll clip as Boreal does.
    parts = [
        (
            sized_clip(tmp_path, "scene-1", size="1080x1920", fps=25, seconds=2),
            a_cut(1, picture=(0, 2, 0, 2)),
        ),
        (
            sized_clip(tmp_path, "scene-2", size="720x1280", fps=24, seconds=2),
            a_cut(2, picture=(0, 2, 2, 4)),
        ),
    ]
    ad = tmp_path / "ad.mp4"

    assembly.join(parts, ad, music=music(tmp_path, seconds=4), drawn=[])

    # The B-roll clip is scaled up, the same 9:16 shape, and every frame lasts 1/25 second:
    # a rate that changes partway is handled badly by some players and upload sites.
    assert frame(ad) == (1080, 1920, "25/1", "25/1")
    assert assembly.seconds_of(ad) == pytest.approx(4, abs=0.1)


def test_a_broll_picture_plays_whole_under_the_next_line_and_the_talking_lips_stay_in_step(
    tmp_path: Path,
) -> None:
    # A B-roll clip kept whole: 3 seconds of yellow, its line said over its first 2.
    broll_clip = generate(
        tmp_path / "scene-1.mp4",
        "color=c=yellow:s=72x128:r=25",
        f"sine=frequency={VOICE_HERTZ}:sample_rate=8000,volume=volume=0:enable='gte(t,2)'",
        seconds=3,
    )
    # A talking clip whose lips move from 1.5 seconds in: red while quiet, then blue while
    # the words are said.
    talking_clip = generate(
        tmp_path / "scene-2.mp4",
        "color=c=red:s=72x128:r=25,drawbox=c=blue:t=fill:enable='gte(t,1.5)'",
        f"sine=frequency={VOICE_HERTZ}:sample_rate=8000,volume=volume=0:enable='lt(t,1.5)'",
        seconds=3,
    )
    planned = assembly.cuts(
        [
            (1, 1, 3.0, [{"text": "Look", "start": 0.0, "end": 2.0}], "", True, 2.0),
            (2, 2, 3.0, [{"text": "Hi.", "start": 0.1, "end": 2.9}], "", False, 3.0),
        ]
    )
    ad = tmp_path / "ad.mp4"

    assembly.join(
        list(zip([broll_clip, talking_clip], planned, strict=True)),
        ad,
        music=music(tmp_path, seconds=10),
        drawn=[],
    )

    # The talking voice starts at 2, over the B-roll's last second, and lasts 3 seconds; the
    # B-roll picture plays to 3, then the talking picture, a second in, to 5.
    assert assembly.seconds_of(ad) == pytest.approx(5, abs=0.1)
    made = ad.read_bytes()
    assert colour_at(made, 2.5) == "yellow"
    # Its words start 1.5 seconds into the talking clip: 3.5 into the ad, where its lips
    # start moving.
    assert [colour_at(made, at) for at in (3.25, 3.75)] == ["red", "blue"]
    quiet = loudness(made, "voice", between=(2.1, 3.4))
    assert loudness(made, "voice", between=(0, 1.9)) > quiet + 30
    assert loudness(made, "voice", between=(3.6, 4.9)) > quiet + 30


def test_a_broll_picture_that_ends_the_ad_plays_its_end_over_the_music_only(
    tmp_path: Path,
) -> None:
    broll_clip = generate(
        tmp_path / "scene-1.mp4",
        "color=c=yellow:s=72x128:r=25",
        f"sine=frequency={VOICE_HERTZ}:sample_rate=8000,volume=volume=0:enable='gte(t,2)'",
        seconds=5,
    )
    planned = assembly.cuts(
        [(1, 1, 5.0, [{"text": "Look", "start": 0.0, "end": 2.0}], "", True, 2.0)]
    )
    ad = tmp_path / "ad.mp4"

    assembly.join([(broll_clip, planned[0])], ad, music=music(tmp_path, seconds=7), drawn=[])

    assert assembly.seconds_of(ad) == pytest.approx(5, abs=0.1)
    made = ad.read_bytes()
    assert colour_at(made, 4.5) == "yellow"
    assert loudness(made, "music", between=(2.5, 4.9)) > -40
    assert (
        loudness(made, "voice", between=(0, 1.9)) > loudness(made, "voice", between=(2.5, 4.9)) + 30
    )


def test_a_talking_scene_said_entirely_over_a_broll_picture_shows_none_of_its_own(
    tmp_path: Path,
) -> None:
    clips = [
        clip(tmp_path, "scene-1", seconds=3, colour="yellow"),
        clip(tmp_path, "scene-2", seconds=1, colour="red"),
        clip(tmp_path, "scene-3", seconds=2, colour="blue"),
    ]
    planned = assembly.cuts(
        [
            (1, 1, 3.0, [{"text": "Look", "start": 0.0, "end": 2.0}], "", True, 2.0),
            (2, 2, 1.0, [{"text": "Hi.", "start": 0.1, "end": 0.9}], "", False, 1.0),
            (3, 3, 2.0, [{"text": "Bye.", "start": 0.1, "end": 1.9}], "", False, 2.0),
        ]
    )
    ad = tmp_path / "ad.mp4"

    assembly.join(
        list(zip(clips, planned, strict=True)), ad, music=music(tmp_path, seconds=7), drawn=[]
    )

    # Scene 2 is said from 2 to 3, under the B-roll's last second: its red is never seen.
    assert assembly.seconds_of(ad) == pytest.approx(5, abs=0.1)
    made = ad.read_bytes()
    assert [colour_at(made, at) for at in (2.5, 3.5, 4.5)] == ["yellow", "blue", "blue"]
    assert loudness(made, "voice", between=(0.1, 4.9)) > -40


def test_captions_are_timed_from_where_each_scene_plays_in_the_ad() -> None:
    # Scene 2's clip has a second of silence before its words, and is kept from 0.9 seconds
    # in; that part plays from 4.2 seconds into the ad.
    cuts = [
        a_cut(1, picture=(0.0, 4.2, 0.0, 4.2)),
        a_cut(2, picture=(0.9, 3.1, 4.2, 6.4)),
    ]
    words = [
        [
            {"text": "Meet", "start": 0.1, "end": 0.5},
            {"text": "the", "start": 0.5, "end": 0.8},
            {"text": "mug.", "start": 0.8, "end": 4.1},
        ],
        [
            {"text": "Yours", "start": 1.0, "end": 1.5},
            {"text": "for", "start": 1.5, "end": 2.0},
            {"text": "twenty", "start": 2.0, "end": 2.5},
            {"text": "dollars.", "start": 2.5, "end": 3.0},
        ],
    ]

    assert assembly.captions(cuts, words) == [
        assembly.Caption(text="Meet the mug.", start=0.1, end=4.1),
        # A caption never runs on from one scene into the next: 4 words are split 2 and 2.
        assembly.Caption(text="Yours for", start=4.3, end=5.3),
        assembly.Caption(text="twenty dollars.", start=5.3, end=6.3),
    ]


@pytest.mark.parametrize(
    ("seconds", "written"), [(0.0, "0:00:00.00"), (59.996, "0:01:00.00"), (3661.5, "1:01:01.50")]
)
def test_a_time_is_written_as_the_subtitle_format_reads_it(seconds: float, written: str) -> None:
    assert assembly._timestamp(seconds) == written


def a_scene(
    words: list[dict[str, Any]],
    *,
    seconds: float,
    broll: bool = False,
    said_for: float | None = None,
    number: int = 1,
) -> tuple[int, int, float, list[dict[str, Any]], str, bool, float]:
    """One scene for `cuts`: its number, its clip's id, how long the clip's picture lasts,
    the words heard in it, its overlay, whether it shows the product rather than the person,
    and how long its line's audio lasts: as long as the clip, unless `said_for`."""
    return (number, number, seconds, words, "", broll, seconds if said_for is None else said_for)


def heard(*timed: tuple[str, float, float]) -> list[dict[str, Any]]:
    return [{"text": text, "start": start, "end": end} for text, start, end in timed]


@pytest.mark.parametrize(
    ("last", "seconds", "clip_end"),
    [
        # "sixty dollars" is heard as one token, "$60", timed only to the end of "sixty": the
        # clip is kept long enough after it for "dollars" to be said.
        ("$60", 5.0, 3.6),
        # But never past the end of the clip.
        ("$60", 3.3, 3.3),
        # A last word that is a word is kept with the usual margin.
        ("dollars.", 5.0, 3.1),
    ],
)
def test_a_line_ending_in_a_number_is_kept_long_enough_for_the_number_to_be_said(
    last: str, seconds: float, clip_end: float
) -> None:
    words = heard(("Only", 0.5, 1.0), (last, 2.5, 3.0))

    (cut,) = assembly.cuts([a_scene(words, seconds=seconds)])

    assert (cut.clip_start, cut.clip_end) == (0.4, clip_end)


def test_a_scene_that_shows_the_product_keeps_its_whole_clip() -> None:
    words = heard(("Drizzle", 0.3, 1.0), ("it", 1.0, 2.0))

    broll, talking = assembly.cuts(
        [a_scene(words, seconds=3.0, broll=True), a_scene(words, seconds=3.0)]
    )

    # The clip's motion plays out to its end, past the last word.
    assert (broll.clip_start, broll.clip_end, broll.start, broll.end) == (0.0, 3.0, 0.0, 3.0)
    # A talking scene is still cut to its words.
    assert (talking.clip_start, talking.clip_end, talking.start, talking.end) == (
        0.2,
        2.1,
        3.0,
        4.9,
    )


def talking(
    number: int, seconds: float
) -> tuple[int, int, float, list[dict[str, Any]], str, bool, float]:
    """A talking scene whose words fill its clip of `seconds`, but for the margin kept either
    side of them: the whole clip is kept for its words."""
    return a_scene(
        heard(("Hi", 0.1, 1.0), ("there.", 1.0, seconds - 0.1)), seconds=seconds, number=number
    )


def broll(
    number: int, *, picture: float, said_for: float
) -> tuple[int, int, float, list[dict[str, Any]], str, bool, float]:
    """A scene showing the product whose clip's picture lasts `picture` seconds, longer than
    its line's audio of `said_for`, as a B-roll clip kept whole does."""
    return a_scene(
        heard(("Look", 0.0, said_for)),
        seconds=picture,
        broll=True,
        said_for=said_for,
        number=number,
    )


Placed = tuple[tuple[float, float, float, float], tuple[float, float, float, float]]


def placed(cuts: list[assembly.Cut]) -> list[Placed]:
    """Where each part of each clip plays: its picture's (from, to in the clip, start, end in
    the ad), then its voice's."""
    return [
        (
            (cut.clip_start, cut.clip_end, cut.start, cut.end),
            (cut.voice_clip_start, cut.voice_clip_end, cut.voice_start, cut.voice_end),
        )
        for cut in cuts
    ]


def test_the_next_line_starts_over_the_end_of_a_broll_clip_and_the_talking_scene_catches_up() -> (
    None
):
    planned = assembly.cuts([talking(1, 4.0), broll(2, picture=5.0, said_for=3.5), talking(3, 3.0)])

    assert placed(planned) == [
        ((0.0, 4.0, 0.0, 4.0), (0.0, 4.0, 0.0, 4.0)),
        # The B-roll picture plays whole, to 9 seconds; its voice ends at 7.5.
        ((0.0, 5.0, 4.0, 9.0), (0.0, 3.5, 4.0, 7.5)),
        # Scene 3's voice starts at 7.5, over the B-roll's end, and keeps all its sound; its
        # picture skips the 1.5 seconds it is behind, so the lips match the words.
        ((1.5, 3.0, 9.0, 10.5), (0.0, 3.0, 7.5, 10.5)),
    ]


def test_a_second_broll_scene_plays_from_its_start_and_the_talking_scene_skips_more() -> None:
    planned = assembly.cuts(
        [
            broll(1, picture=5.0, said_for=4.0),
            broll(2, picture=5.0, said_for=3.5),
            talking(3, 3.0),
        ]
    )

    assert placed(planned) == [
        ((0.0, 5.0, 0.0, 5.0), (0.0, 4.0, 0.0, 4.0)),
        # Its line starts at 4, over the end of the first clip, but its picture plays whole
        # from 5: a second behind its line.
        ((0.0, 5.0, 5.0, 10.0), (0.0, 3.5, 4.0, 7.5)),
        # 2.5 seconds behind by now: the talking scene skips that much of its picture.
        ((2.5, 3.0, 10.0, 10.5), (0.0, 3.0, 7.5, 10.5)),
    ]


def test_a_broll_scene_that_ends_the_ad_plays_its_end_over_the_music_only() -> None:
    planned = assembly.cuts([talking(1, 4.0), talking(2, 3.0), broll(3, picture=5.0, said_for=1.5)])

    assert placed(planned)[-1] == ((0.0, 5.0, 7.0, 12.0), (0.0, 1.5, 7.0, 8.5))


def test_a_talking_scene_shorter_than_the_overlap_is_skipped_and_the_rest_carried_on() -> None:
    planned = assembly.cuts([broll(1, picture=5.0, said_for=3.0), talking(2, 1.0), talking(3, 3.0)])

    assert placed(planned) == [
        ((0.0, 5.0, 0.0, 5.0), (0.0, 3.0, 0.0, 3.0)),
        # Said while the B-roll still plays: none of its picture is seen.
        ((1.0, 1.0, 5.0, 5.0), (0.0, 1.0, 3.0, 4.0)),
        # Still a second behind: scene 3 skips it.
        ((1.0, 3.0, 5.0, 7.0), (0.0, 3.0, 4.0, 7.0)),
    ]


def test_a_broll_clip_a_little_shorter_than_its_line_holds_its_last_frame_to_its_end() -> None:
    planned = assembly.cuts([broll(1, picture=3.4, said_for=3.5), talking(2, 2.0)])

    assert placed(planned) == [
        ((0.0, 3.4, 0.0, 3.5), (0.0, 3.5, 0.0, 3.5)),
        ((0.0, 2.0, 3.5, 5.5), (0.0, 2.0, 3.5, 5.5)),
    ]


def test_clips_as_long_as_their_voice_play_picture_and_voice_together() -> None:
    planned = assembly.cuts([talking(1, 4.0), broll(2, picture=3.5, said_for=3.5), talking(3, 3.0)])

    assert [picture for picture, _ in placed(planned)] == [
        (0.0, 4.0, 0.0, 4.0),
        (0.0, 3.5, 4.0, 7.5),
        (0.0, 3.0, 7.5, 10.5),
    ]
    assert [picture for picture, _ in placed(planned)] == [voice for _, voice in placed(planned)]


def test_captions_follow_the_voice_and_not_the_picture_after_a_broll_scene() -> None:
    planned = assembly.cuts([broll(1, picture=5.0, said_for=3.0), talking(2, 3.0)])
    words = [heard(("Look", 0.0, 3.0)), heard(("Hi", 0.1, 1.0), ("there.", 1.0, 2.9))]

    assert assembly.captions(planned, words) == [
        assembly.Caption(text="Look", start=0.0, end=3.0),
        # Scene 2's words are said from 3 seconds into the ad, while the B-roll still plays.
        assembly.Caption(text="Hi there.", start=3.1, end=5.9),
    ]


def texts(timed: list[dict[str, Any]]) -> list[str]:
    return [word["text"] for word in timed]


def test_the_script_is_timed_by_the_words_heard_where_they_are_heard_differently() -> None:
    line = (
        "This is Beardbrand Fox Hunt Men's Cologne, a crisp and clean, alcohol-free eau de parfum."
    )
    words = heard(
        ("This", 0.08, 0.2),
        ("is", 0.26, 0.42),
        ("Beard", 0.46, 0.7),
        ("Brand", 0.78, 1.04),
        ("Foxhunt", 1.12, 1.62),
        ("Men's", 1.7, 1.94),
        ("Cologne,", 2.0, 2.42),
        ("a", 2.88, 2.94),
        ("crisp", 3.0, 3.3),
        ("and", 3.34, 3.44),
        ("clean", 3.52, 3.86),
        ("alcohol-free", 4.38, 5.08),
        ("eau", 5.14, 5.26),
        ("de", 5.34, 5.48),
        ("parfum", 5.58, 6.06),
    )

    timed = assembly.timed_script(line, words)

    assert texts(timed) == line.split()
    # "Beardbrand Fox Hunt" share the time "Beard Brand Foxhunt" was heard in, from 0.46 to
    # 1.62 seconds, split by how long each word is written: 10, 3 and 4 letters.
    assert timed[2:5] == heard(
        ("Beardbrand", 0.46, 1.142), ("Fox", 1.142, 1.347), ("Hunt", 1.347, 1.62)
    )
    # The words heard as written keep their own times.
    assert timed[5] == {"text": "Men's", "start": 1.7, "end": 1.94}
    assert timed[-1] == {"text": "parfum.", "start": 5.58, "end": 6.06}
    cut = a_cut(1, picture=(0.0, 6.2, 0.0, 6.2))
    assert [caption.text for caption in assembly.captions([cut], [timed])][:2] == [
        "This is Beardbrand",
        "Fox Hunt Men's",
    ]


def test_a_price_is_captioned_as_the_script_writes_it() -> None:
    line = "The Gripmunk Slim Case for iPhone 17e is fourteen ninety-nine."
    words = heard(
        ("The", 0.08, 0.12),
        ("Gripmonk", 0.2, 0.56),
        ("Slim", 0.64, 0.84),
        ("Case", 0.94, 1.2),
        ("for", 1.26, 1.38),
        ("iPhone", 1.46, 1.74),
        ("17E", 1.84, 2.4),
        ("is", 3.0, 3.14),
        ("$14.99", 3.14, 4.28),
    )

    timed = assembly.timed_script(line, words)

    assert texts(timed) == line.split()
    cut = a_cut(1, picture=(0.0, 4.4, 0.0, 4.4))
    last = assembly.captions([cut], [timed])[-1]
    assert (last.text, last.start, last.end) == ("fourteen ninety-nine.", 3.14, 4.28)


def test_a_word_the_transcript_missed_is_timed_between_its_neighbours() -> None:
    words = heard(("Meet", 0.0, 0.5), ("mug.", 1.0, 1.5))

    timed = assembly.timed_script("Meet the mug.", words)

    assert timed == heard(("Meet", 0.0, 0.5), ("the", 0.5, 1.0), ("mug.", 1.0, 1.5))


def test_a_line_heard_as_written_keeps_the_times_it_was_heard_at() -> None:
    words = heard(("Yours", 1.0, 1.5), ("for", 1.5, 2.0), ("$24.00.", 2.0, 2.9))

    assert assembly.timed_script("Yours for $24.00.", words) == words


@pytest.mark.parametrize(
    ("line", "words", "timed"),
    [
        # Heard back to back, with no gap between them: "the" shares the word before it, by
        # length.
        (
            "Meet the mug.",
            heard(("Meet", 0.0, 0.5), ("mug.", 0.5, 1.0)),
            heard(("Meet", 0.0, 0.286), ("the", 0.286, 0.5), ("mug.", 0.5, 1.0)),
        ),
        # The line's first word, with nothing before it: it shares the word after it.
        (
            "Hi there.",
            heard(("there.", 1.0, 1.5)),
            heard(("Hi", 1.0, 1.143), ("there.", 1.143, 1.5)),
        ),
    ],
)
def test_a_word_the_transcript_missed_with_no_gap_for_it_shares_a_neighbours_time(
    line: str, words: list[dict[str, Any]], timed: list[dict[str, Any]]
) -> None:
    assert assembly.timed_script(line, words) == timed
