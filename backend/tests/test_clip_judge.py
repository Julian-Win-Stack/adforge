"""The clip judge's ruling from its answers: each check fails a clip on 2 of its 3 answers,
and a problem whose thing is back to normal within half a second is let go, its note kept
(change 7, Julian 2026-10-13). What it rules is what the auto-redo acts on: pass or fail, how
many of the 6 answers failed, and each failed answer's problem and everything it saw."""

from typing import Any

import pytest

from jobs import clip_judge
from jobs.clip_judge import judged, wording_version

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
        "changed_thing": {
            "name": "the tea",
            "state_at_start": "empty",
            "state_at_end": "full",
            "when_it_changed_and_what_touched_it": "1 s, the pour",
            "both_states_at_once_or_untouched_or_all_at_once": "no",
        },
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


def test_sides_are_let_go_the_same_way() -> None:
    blink = "A logo appears on the case's back at 3 s and is gone by the next frame."
    ruling = judged([sides(blink, "3.2 s")] * 3, [motion()] * 3)

    assert ruling["verdict"] == "pass"
    assert [note["check"] for note in ruling["let_go"]] == ["sides"] * 3


def test_a_thing_back_to_normal_later_than_half_a_second_still_fails() -> None:
    # The toilet scrub's streaks fade for about a second: Julian tapped it "worth fixing".
    streaks = "The streaks fade at 2.0 s where the brush isn't; real ones stay."
    ruling = judged([sides()] * 3, [motion(sudden_change=(streaks, "2.6"))] * 3)

    assert ruling["verdict"] == "fail"
    assert ruling["failed_answers"] == 3


def test_a_thing_never_back_to_normal_still_fails() -> None:
    ruling = judged([sides(HANDLE, "never")] * 2 + [sides()], [motion()] * 3)

    assert ruling["verdict"] == "fail"


def test_a_problem_that_names_no_second_is_never_let_go() -> None:
    no_time = "The mug shows a second handle where its plain side should be."
    ruling = judged([sides(no_time, "0.1")] * 2 + [sides()], [motion()] * 3)

    assert ruling["verdict"] == "fail"


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


def test_a_thing_back_to_normal_before_the_problem_starts_isnt_let_go() -> None:
    # A misread time: it can't be back before it went wrong.
    ruling = judged([sides(HANDLE, "2.2")] * 2 + [sides()], [motion()] * 3)

    assert ruling["verdict"] == "fail"


def test_a_thing_never_back_to_normal_isnt_let_go_whatever_seconds_it_names() -> None:
    ruling = judged(
        [sides(HANDLE, "never; it stays from 2.4 s on")] * 2 + [sides()], [motion()] * 3
    )

    assert ruling["verdict"] == "fail"


def test_two_answers_settle_a_check_when_its_third_went_unchecked() -> None:
    ruling = judged([sides(), None, sides()], [motion()] * 3)

    assert ruling["verdict"] == "pass"


@pytest.mark.parametrize(
    "told", ["MOTION_CHECK", "SIDES_CHECK", "PAGE_FRAME", "CONVERSATION_FRAME", "FRAMES_LABEL"]
)
def test_the_wording_version_changes_whenever_what_the_judge_is_told_does(
    told: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = wording_version()
    monkeypatch.setattr(clip_judge, told, getattr(clip_judge, told) + " Changed.")

    assert wording_version() != before


def test_a_time_span_with_an_about_is_measured_from_where_it_starts() -> None:
    # Back 2.3 s after it started at 1.2: read from 3.4, it would look like 0.1 s.
    span = "The handle melts into the side from 1.2 to about 3.4 s; a real one stays put."
    ruling = judged([sides(span, "3.5")] * 2 + [sides()], [motion()] * 3)

    assert ruling["verdict"] == "fail"


@pytest.mark.parametrize(
    "back",
    ["Not back to normal; still fused at the last frame, 2.6 s", "about 2.6", "2.6 or so"],
)
def test_only_a_bare_time_counts_as_back_to_normal(back: str) -> None:
    ruling = judged([sides(HANDLE, back)] * 2 + [sides()], [motion()] * 3)

    assert ruling["verdict"] == "fail"


@pytest.mark.parametrize("back", ["2.6", "2.6 s", " 2.6s ", "2.6 seconds"])
def test_a_bare_time_with_or_without_its_unit_counts_as_back_to_normal(back: str) -> None:
    ruling = judged([sides(HANDLE, back)] * 2 + [sides()], [motion()] * 3)

    assert ruling["verdict"] == "pass"
