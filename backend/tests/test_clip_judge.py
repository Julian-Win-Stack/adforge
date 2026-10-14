"""The clip judge's ruling from its answers: each check fails a clip on 2 of its 3 answers,
and a problem whose thing is back to normal within half a second is let go, its note kept
(change 7, Julian 2026-10-13). What it rules is what the auto-redo acts on: pass or fail, how
many of the 6 answers failed, and each failed answer's problem and everything it saw."""

import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from pydantic import create_model

from adforge import file_store
from jobs import clip_judge
from jobs.clip_judge import STILLS, cut_pictures, judged, still_failing, wording_version

SEEN = [{"time": 0.0, "objects": []}]


def sides(problem: str | None = None, back: str | None = None) -> dict[str, Any]:
    """A SIDES answer: failing with `problem` when one is given."""
    return {
        "frames": SEEN,
        "across_frames": "The mug turns once, showing its plain side.",
        "verdict": "fail" if problem else "pass",
        "problem": problem,
        "back_to_normal_at": back,
    }


def check(problem: str | None = None, back: str | None = None) -> dict[str, Any]:
    return {"verdict": "fail" if problem else "pass", "problem": problem, "back_to_normal_at": back}


def motion(**failing: tuple[str, str | None] | str) -> dict[str, Any]:
    """A MOTION answer: each question named fails with its problem, and when given as a pair,
    the second it is back to normal."""
    answer: dict[str, Any] = {
        "look": {"in_the_clip": "a mug", "start": "upright", "moments": [], "end": "upright"},
        "ordered_action": "pour tea",
        "seen_action": "pours tea",
    }
    for question in ("real_motion", "sudden_change", "does_what_was_ordered"):
        given = failing.get(question)
        answer[question] = check(*given) if isinstance(given, tuple) else check(given)
    return answer


HANDLE = "The mug shows a second handle at 2.4 s where its plain side should be."
FLOATS = "The teapot rises at 1.2 s with no hand on it; a real one stays put."
FILLS = "The mug fills at 0.8 s before the pour reaches it; a real one fills as tea lands."


def test_a_check_failed_by_two_of_its_three_answers_fails_the_clip() -> None:
    ruling = judged([sides(HANDLE), sides(), sides(HANDLE)], [motion()] * 3)

    assert ruling["verdict"] == "fail"
    assert ruling["checks"]["sides"] == "fail"
    assert ruling["failed_answers"] == 2


def test_one_failing_answer_in_three_passes_the_clip_but_is_counted() -> None:
    ruling = judged([sides(HANDLE), sides(), sides()], [motion()] * 3)

    assert ruling["verdict"] == "pass"
    assert ruling["failed_answers"] == 1


def test_each_motion_question_is_voted_on_by_itself() -> None:
    # Two answers fail, but on different questions: neither question has 2 fails.
    ruling = judged(
        [sides()] * 3, [motion(real_motion=FLOATS), motion(sudden_change=FILLS), motion()]
    )

    assert ruling["verdict"] == "pass"
    assert ruling["checks"] == {
        "sides": "pass",
        "real_motion": "pass",
        "sudden_change": "pass",
        "does_what_was_ordered": "pass",
    }
    assert ruling["failed_answers"] == 2


def test_each_failed_answer_gives_its_problems_and_everything_it_saw() -> None:
    both = motion(real_motion=FLOATS, sudden_change=FILLS)
    ruling = judged([sides(HANDLE), sides(), sides()], [motion(), both, motion(real_motion=FLOATS)])

    assert ruling["verdict"] == "fail"
    assert ruling["checks"]["real_motion"] == "fail"
    assert ruling["failed_answers"] == 3
    assert ruling["failures"] == [
        {"check": "sides", "ask": 1, "problems": [HANDLE], "answer": sides(HANDLE)},
        {"check": "motion", "ask": 2, "problems": [FLOATS, FILLS], "answer": both},
        {"check": "motion", "ask": 3, "problems": [FLOATS], "answer": motion(real_motion=FLOATS)},
    ]


def test_every_answer_is_kept() -> None:
    answers = ([sides(HANDLE), None, sides()], [motion(), motion(), None])

    assert judged(*answers)["answers"] == {"sides": answers[0], "motion": answers[1]}


def test_a_thing_back_to_normal_within_half_a_second_is_let_go_with_its_note_kept() -> None:
    blink = "The ring at the rim vanishes at 8.0 s; a real one stays."
    ruling = judged([sides()] * 3, [motion(sudden_change=(blink, "8.4"))] * 2 + [motion()])

    assert ruling["verdict"] == "pass"
    assert ruling["failed_answers"] == 0
    assert ruling["failures"] == []
    assert ruling["let_go"] == [
        {"check": "sudden_change", "ask": 1, "problem": blink, "back_to_normal_at": "8.4"},
        {"check": "sudden_change", "ask": 2, "problem": blink, "back_to_normal_at": "8.4"},
    ]


def test_a_thing_back_to_normal_later_than_half_a_second_still_fails() -> None:
    # The toilet scrub's streaks fade for about a second: Julian tapped it "worth fixing".
    streaks = "The streaks fade at 2.0 s where the brush isn't; real ones stay."
    ruling = judged([sides()] * 3, [motion(sudden_change=(streaks, "2.6"))] * 3)

    assert ruling["verdict"] == "fail"
    assert ruling["failed_answers"] == 3


def test_a_time_range_is_measured_from_where_it_starts() -> None:
    blink = "The screen pattern changes from 1.2-1.4 s mid-air; a real screen stays the same."
    ruling = judged([sides()] * 3, [motion(real_motion=(blink, "1.6 s"))] * 3)

    assert ruling["verdict"] == "pass"


def test_a_check_with_fewer_than_two_answers_is_not_checked() -> None:
    ruling = judged([sides(), None, None], [motion()] * 3)

    assert ruling["verdict"] == "not_checked"
    assert ruling["checks"]["sides"] == "not_checked"


def test_a_failed_check_fails_the_clip_even_when_another_went_unchecked() -> None:
    ruling = judged([None, None, None], [motion(real_motion=FLOATS)] * 2 + [motion()])

    assert ruling["verdict"] == "fail"


def test_two_answers_settle_a_check_when_its_third_went_unchecked() -> None:
    ruling = judged([sides(), None, sides()], [motion()] * 3)

    assert ruling["verdict"] == "pass"


@pytest.mark.parametrize(
    "told",
    [
        "MOTION_CHECK",
        "SIDES_CHECK",
        "PAGE_FRAME",
        "CONVERSATION_FRAME",
        "FRAMES_LABEL",
        "SHOP_PHOTO_LABEL",
        "NEEDS_PHOTO_LABEL",
    ],
)
def test_the_wording_version_changes_whenever_what_the_judge_is_told_does(
    told: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = wording_version()
    monkeypatch.setattr(clip_judge, told, getattr(clip_judge, told) + " Changed.")

    assert wording_version() != before


@pytest.mark.parametrize(
    "form", ["SidesVerdict", "MotionVerdict", "ClipCheckHandoff", "MotionCheckHandoff"]
)
def test_the_wording_version_changes_whenever_an_answer_form_or_handoff_does(
    form: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = wording_version()
    # The form with one more field, as when a question is added to it.
    changed = create_model("Changed", __base__=getattr(clip_judge, form), changed_thing=(str, ""))
    monkeypatch.setattr(clip_judge, form, changed)

    assert wording_version() != before


def test_still_failing_lists_sides_problems_before_motions_and_leaves_out_what_was_let_go() -> None:
    blink = "The ring at the rim vanishes at 8.0 s; a real one stays."
    # SIDES and real_motion fail on 2 of 3; the blink is let go each time, and the third
    # answer's does_what_was_ordered fail is outvoted.
    ruling = judged(
        [sides(HANDLE), sides(), sides(HANDLE)],
        [motion(real_motion=FLOATS, sudden_change=(blink, "8.4"))] * 2
        + [motion(does_what_was_ordered=FILLS)],
    )

    assert ruling["checks"] == {
        "sides": "fail",
        "real_motion": "fail",
        "sudden_change": "pass",
        "does_what_was_ordered": "pass",
    }
    assert still_failing(ruling) == [HANDLE, HANDLE, FLOATS, FLOATS]


# The HANDLE problem starts at 2.4 s. Two SIDES answers fail with a problem and a time it is
# back to normal at; the clip fails unless the time is a bare one within half a second.
NO_TIME = "The mug shows a second handle where its plain side should be."
# Back 2.3 s after it started at 1.2: read from 3.4, it would look like 0.1 s.
SPAN = "The handle melts into the side from 1.2 to about 3.4 s; a real one stays put."
BLINK = "A logo appears on the case's back at 3 s and is gone by the next frame."
FAILS: tuple[str, list[str]] = ("fail", [])
LET_GO: tuple[str, list[str]] = ("pass", ["sides", "sides"])


@pytest.mark.parametrize(
    ("problem", "back", "ruled"),
    [
        pytest.param(HANDLE, "never", FAILS, id="never back to normal"),
        pytest.param(NO_TIME, "0.1", FAILS, id="a problem that names no second"),
        # A misread time: it can't be back before it went wrong.
        pytest.param(HANDLE, "2.2", FAILS, id="back before the problem starts"),
        pytest.param(HANDLE, "never; it stays from 2.4 s on", FAILS, id="never, whatever it names"),
        pytest.param(SPAN, "3.5", FAILS, id="a span with an about is measured from its start"),
        pytest.param(
            HANDLE,
            "Not back to normal; still fused at the last frame, 2.6 s",
            FAILS,
            id="a time in a sentence saying it isn't back",
        ),
        pytest.param(HANDLE, "about 2.6", FAILS, id="about a time"),
        pytest.param(HANDLE, "2.6 or so", FAILS, id="a time or so"),
        pytest.param(HANDLE, "2.6", LET_GO, id="a bare time"),
        pytest.param(HANDLE, "2.6 s", LET_GO, id="a bare time with its unit"),
        pytest.param(HANDLE, " 2.6s ", LET_GO, id="a bare time with its unit stuck on"),
        pytest.param(HANDLE, "2.6 seconds", LET_GO, id="a bare time in seconds"),
        pytest.param(BLINK, "3.2 s", LET_GO, id="sides are let go the same way as motions"),
    ],
)
def test_a_sides_problem_is_let_go_only_at_a_bare_time_within_half_a_second_of_its_start(
    problem: str, back: str, ruled: tuple[str, list[str]]
) -> None:
    ruling = judged([sides(problem, back)] * 2 + [sides()], [motion()] * 3)

    assert (ruling["verdict"], [note["check"] for note in ruling["let_go"]]) == ruled


# --- The stills SIDES is shown ----------------------------------------------------------------


def a_clip(path: Path, *parts: tuple[str, float], fps: int = 25) -> None:
    """A real 72 x 128 clip of plain colours at `path`, one after another, each for its
    seconds, as the fake's clips are made."""
    inputs: list[str] = []
    for colour, seconds in parts:
        inputs += ["-f", "lavfi", "-i", f"color=c={colour}:s=72x128:r={fps}:d={seconds}"]
    joined = "".join(f"[{n}]" for n in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0"
    subprocess.run(
        [settings.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *inputs]
        + ["-filter_complex", joined, "-pix_fmt", "yuv420p", str(path)],
        check=True,
        capture_output=True,
        timeout=60,
    )


def stills_of(*parts: tuple[str, float], fps: int = 25) -> list[float]:
    """The time of each still SIDES is shown of a clip of `parts`, kept in the file store."""
    with tempfile.TemporaryDirectory() as folder:
        clip = Path(folder) / "clip.mp4"
        a_clip(clip, *parts, fps=fps)
        key = file_store.save("clip.mp4", clip.read_bytes())
    cut = cut_pictures(key, beside=1)
    times = [still["times"] for still in cut["stills"]]
    assert all(len(each) == 1 for each in times), "each still is a picture of its own"
    return [each[0] for each in times]


@pytest.mark.parametrize(
    ("parts", "fps", "times"),
    [
        pytest.param(
            (("red", 2.0), ("blue", 3.0)),
            25,
            # The first, the last (a twentieth before the end), the biggest change at 2 s,
            # and two evenly between: a third and two thirds of the way.
            [0.0, 1.65, 2.0, 3.3, 4.95],
            id="a change at 2 s",
        ),
        pytest.param(
            (("red", 1.65), ("blue", 3.35)),
            20,
            # The biggest change falls on the one-third still, so the middle is taken instead.
            [0.0, 1.65, 2.48, 3.3, 4.95],
            id="a change on the one-third still",
        ),
        pytest.param(
            (("red", 5.0),),
            25,
            # Nothing changes, so the "biggest change" is the first one measured: the second
            # frame, as the harness's frames.py reads it too.
            [0.0, 0.04, 1.65, 3.3, 4.95],
            id="one colour throughout",
        ),
    ],
)
def test_sides_stills_are_the_first_the_last_the_biggest_change_and_two_evenly_between(
    parts: tuple[tuple[str, float], ...], fps: int, times: list[float]
) -> None:
    stills = stills_of(*parts, fps=fps)

    assert len(stills) == STILLS == 5
    assert stills == pytest.approx(times, abs=0.05)
