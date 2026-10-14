"""What the auto-fix (#155) hands the model writing a failed clip's video prompt again: the
problems of the judge's checks that failed, never one from an answer outvoted on its check
(only 1 of 3 asks failed it) or let go, and the judge's answers that saw them (Julian's yes,
2026-10-13 19:42). The rulings are the clip judge's own, from hand-written answers."""

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


def test_a_motion_question_failed_by_one_ask_of_three_is_left_out() -> None:
    # Two asks see the teapot float, so real_motion fails; only the first sees the mug spin,
    # so sudden_change passes 2 to 1.
    ruling = judged(
        [SIDES_OK] * 3,
        [motion(real_motion=FLOATS, sudden_change=SPINS), motion(real_motion=FLOATS), motion()],
    )

    assert faults(ruling) == [FLOATS]
    # The first ask's answer is handed over without the question it was outvoted on.
    first_seen = {
        "look": LOOK,
        "ordered_action": "Pour tea into the mug.",
        "seen_action": "Tea is poured into the mug.",
        "real_motion": {"verdict": "fail", "problem": FLOATS, "back_to_normal_at": "never"},
        "does_what_was_ordered": PASSED,
    }
    second_seen = {**first_seen, "sudden_change": PASSED}
    assert judge_saw(ruling) == [first_seen, second_seen]


def test_an_answer_whose_only_fail_was_outvoted_isnt_handed_over() -> None:
    # SIDES: one ask of three sees a second handle, so SIDES passes. MOTION: the third ask
    # alone sees the lid jump, while the first two see the teapot float.
    ruling = judged(
        [sides(TURNS), SIDES_OK, SIDES_OK],
        [motion(real_motion=FLOATS), motion(real_motion=FLOATS), motion(sudden_change=JUMPS)],
    )

    assert faults(ruling) == [FLOATS]
    assert judge_saw(ruling) == [motion(real_motion=FLOATS)]


def test_a_fail_let_go_on_a_failed_question_is_left_out() -> None:
    # The first ask sees the teapot float, and the mug spin at 2 s but back to normal at
    # 2.2 s: the spin is let go. The other two see the mug spin for good, so sudden_change
    # still fails, from them; the second sees the teapot float too.
    first = motion(real_motion=FLOATS)
    first["sudden_change"] = {"verdict": "fail", "problem": SPINS, "back_to_normal_at": "2.2"}
    ruling = judged(
        [SIDES_OK] * 3,
        [first, motion(real_motion=FLOATS, sudden_change=SPINS), motion(sudden_change=SPINS)],
    )

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


def test_every_failed_check_is_handed_over_sides_first_each_problem_once() -> None:
    ruling = judged(
        [sides(TURNS), sides(TURNS), SIDES_OK],
        [
            motion(real_motion=FLOATS, sudden_change=JUMPS),
            motion(real_motion=FLOATS, sudden_change=JUMPS),
            motion(),
        ],
    )

    assert faults(ruling) == [TURNS, FLOATS, JUMPS]
    assert judge_saw(ruling) == [sides(TURNS), motion(real_motion=FLOATS, sudden_change=JUMPS)]
