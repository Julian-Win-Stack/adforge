"""What the fixing agent is handed about a clip the judge failed: the problems of the judge's
checks that failed, never one a ruling from before let go or its vote outvoted, and the
judge's answers that saw them (Julian's yes, 2026-10-13 19:42). The rulings are the clip
judge's own, from hand-written answers: one ask per check since Julian's "drop the vote"
(2026-10-22); rulings made while each check was asked 3 times are still kept on clips."""

from typing import Any

from jobs.auto_fix import faults, judge_saw
from jobs.clip_judge import judged

FLOATS = "The teapot rises at 1.2 s with no hand on it. A real one stays on the bench."
SPINS = "The mug spins at 2 s and shows its back. A real one keeps its front to the camera."
JUMPS = "The lid jumps at 3 s. A real one stays on the teapot."
TURNS = "The mug turns at 1 s and shows a second handle. A real mug has one handle."

PASSED: dict[str, Any] = {"verdict": "pass", "problem": None, "back_to_normal_at": None}
LOOK: dict[str, Any] = {
    "in_the_clip": "A teapot and a mug on a bench.",
    "start": "The mug is empty.",
    "moments": [{"time": 1.0, "what_happens": "Tea pours into the mug."}],
    "end": "The mug is full.",
}
SIDES_OK: dict[str, Any] = {"frames": [], "across_frames": "The mug keeps its front.", **PASSED}


def motion(**failed: str) -> dict[str, Any]:
    """MOTION's answer, failing each question named with its problem, never back to normal."""
    return {
        "look": LOOK,
        "ordered_action": "Pour tea into the mug.",
        "seen_action": "Tea is poured into the mug.",
        "real_motion": PASSED,
        "sudden_change": PASSED,
        "does_what_was_ordered": PASSED,
        **{
            question: {"verdict": "fail", "problem": problem, "back_to_normal_at": "never"}
            for question, problem in failed.items()
        },
    }


def sides(problem: str) -> dict[str, Any]:
    """SIDES's answer when it sees `problem`."""
    return {**SIDES_OK, "verdict": "fail", "problem": problem, "back_to_normal_at": "never"}


def test_every_question_the_one_answer_fails_is_handed_over_with_all_it_saw() -> None:
    ruling = judged([SIDES_OK], [motion(real_motion=FLOATS, sudden_change=SPINS)])

    assert faults(ruling) == [FLOATS, SPINS]
    assert judge_saw(ruling) == [motion(real_motion=FLOATS, sudden_change=SPINS)]


def test_a_clip_the_judge_passed_hands_nothing_over() -> None:
    ruling = judged([SIDES_OK], [motion()])

    assert (faults(ruling), judge_saw(ruling)) == ([], [])


def test_a_fail_a_ruling_from_before_let_go_on_a_failed_question_is_left_out() -> None:
    # Made before the half-second forgive rule was gone: the first ask sees the teapot float,
    # and the mug spin at 2 s but back to normal at 2.2 s, and the spin was let go. The other
    # two see the mug spin for good, so sudden_change still fails, from them; the second sees
    # the teapot float too.
    first = motion(real_motion=FLOATS)
    first["sudden_change"] = {"verdict": "fail", "problem": SPINS, "back_to_normal_at": "2.2"}
    ruling = judged(
        [SIDES_OK] * 3,
        [first, motion(real_motion=FLOATS, sudden_change=SPINS), motion(sudden_change=SPINS)],
    )
    ruling["let_go"] = [
        {"check": "sudden_change", "ask": 1, "problem": SPINS, "back_to_normal_at": "2.2"}
    ]

    assert faults(ruling) == [FLOATS, SPINS]
    first_seen = {
        "look": LOOK,
        "ordered_action": "Pour tea into the mug.",
        "seen_action": "Tea is poured into the mug.",
        "real_motion": {"verdict": "fail", "problem": FLOATS, "back_to_normal_at": "never"},
        "does_what_was_ordered": PASSED,
    }
    assert judge_saw(ruling) == [
        first_seen,
        motion(real_motion=FLOATS, sudden_change=SPINS),
        motion(sudden_change=SPINS),
    ]


def test_a_fail_a_ruling_from_the_vote_found_no_other_answer_naming_is_left_out() -> None:
    # Made while each check was asked 3 times: all three fail real_motion, but the same-fault
    # vote found the second's fault, the spin, named by no other answer.
    answers = [motion(real_motion=FLOATS), motion(real_motion=SPINS), motion(real_motion=FLOATS)]
    ruling = {
        "checks": {"sides": "pass", "real_motion": "fail"},
        "failures": [
            {
                "check": "motion",
                "ask": ask,
                "problems": [said["real_motion"]["problem"]],
                "answer": said,
            }
            for ask, said in enumerate(answers, 1)
        ],
        "answers": {"sides": [SIDES_OK] * 3, "motion": answers},
        "agreement": {"real_motion": {"groups": [], "sharing": [1, 3]}},
    }

    assert faults(ruling) == [FLOATS]
    assert judge_saw(ruling) == [motion(real_motion=FLOATS)]


def test_every_failed_check_is_handed_over_sides_first_each_problem_once() -> None:
    ruling = judged([sides(TURNS)], [motion(real_motion=FLOATS, sudden_change=JUMPS)])

    assert faults(ruling) == [TURNS, FLOATS, JUMPS]
    assert judge_saw(ruling) == [sides(TURNS), motion(real_motion=FLOATS, sudden_change=JUMPS)]
