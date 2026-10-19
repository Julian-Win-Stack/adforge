"""The clip judge's ruling from its answers: each check fails a clip on 2 of its 3 answers,
every failing answer counting (the half-second forgive rule is gone, Julian 2026-10-19), and
two failing MOTION answers agree only when they name the same fault. What it rules is what the
auto-redo acts on: pass or fail, how many of the 6 answers failed, and each failed answer's
problem and everything it saw."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import create_model

from gateway import catalog
from jobs import clip_judge, fix_check
from jobs.clip_judge import SIDES_CHECK, SameFault, SidesVerdict, still_failing, wording_version

PINNED = Path(__file__).parent / "fixtures"

SEEN = [{"time": 0.0, "seen": "broad_face", "face": "front", "parts": "", "thin_edge_side": "none"}]


def sides(problem: str | None = None, back: str | None = None) -> dict[str, Any]:
    """A SIDES answer: failing with `problem` when one is given."""
    return {
        "frames": SEEN,
        "turns": [],
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


def one_fault(check: str, failing: list[dict[str, Any]]) -> dict[str, Any]:
    """The same-fault call's reply when every failing answer names one fault."""
    return {"read": [], "groups": [{"asks": [each["ask"] for each in failing], "fault": "one"}]}


def apart(check: str, failing: list[dict[str, Any]]) -> dict[str, Any]:
    """Its reply when each failing answer names a fault of its own."""
    return {"read": [], "groups": [{"asks": [each["ask"]], "fault": "own"} for each in failing]}


def judged(
    sides: Sequence[dict[str, Any] | None],
    motion: Sequence[dict[str, Any] | None],
    same_fault: SameFault = one_fault,
) -> dict[str, Any]:
    """The ruling, when the same-fault call says `same_fault`: by default, one fault."""
    return clip_judge.judged(sides, motion, same_fault=same_fault)


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


def test_a_thing_back_to_normal_soon_still_fails_the_check() -> None:
    # The half-second forgive rule is gone (Julian 2026-10-19 03:37): every failing answer
    # counts, however soon its thing is back to normal.
    blink = "The ring at the rim vanishes at 8.0 s; a real one stays."
    ruling = judged(
        [sides()] * 3,
        [motion(sudden_change=(blink, "8.1"))] * 2 + [motion()],
    )

    assert ruling["verdict"] == "fail"
    assert ruling["failed_answers"] == 2
    assert still_failing(ruling) == [blink, blink]


def test_two_failing_answers_on_different_faults_pass_their_check() -> None:
    # Two unrelated complaints 2 s apart are not agreement (round 5, Cocolab floss scene 4).
    ruling = judged(
        [sides()] * 3,
        [motion(real_motion=FLOATS), motion(real_motion=FILLS), motion()],
        same_fault=apart,
    )

    assert ruling["checks"]["real_motion"] == "pass"
    assert ruling["verdict"] == "pass"


def test_two_of_three_failing_answers_on_one_fault_fail_their_check_on_that_fault() -> None:
    def first_and_last(check: str, failing: list[dict[str, Any]]) -> dict[str, Any]:
        return {"read": [], "groups": [{"asks": [1, 3], "fault": "floats"}, {"asks": [2]}]}

    ruling = judged(
        [sides()] * 3,
        [motion(real_motion=FLOATS), motion(real_motion=FILLS), motion(real_motion=FLOATS)],
        first_and_last,
    )

    assert ruling["checks"]["real_motion"] == "fail"
    # The second answer's fault is one no other answer saw: it is outvoted.
    assert still_failing(ruling) == [FLOATS, FLOATS]


@pytest.mark.parametrize(
    "groups",
    [
        pytest.param([{"asks": [0, 1]}], id="asks counted from 0"),
        pytest.param([{"asks": [5, 6]}], id="asks never sent"),
        pytest.param([], id="no groups"),
        pytest.param([{"asks": [1]}], id="a failing answer left out"),
        pytest.param([{"asks": [1, 3]}, {"asks": [2]}], id="a passing answer put in"),
        pytest.param([{"asks": [1, 2]}, {"asks": [2]}], id="an answer in two groups"),
    ],
)
def test_a_muddled_same_fault_reply_fails_the_check_on_its_votes(
    groups: list[dict[str, Any]],
) -> None:
    # Never a silent pass: a reply that doesn't put each failing answer in one group is no
    # answer, as when the call fails.
    def muddled(check: str, failing: list[dict[str, Any]]) -> dict[str, Any]:
        return {"read": [], "groups": groups}

    ruling = judged(
        [sides()] * 3, [motion(real_motion=FLOATS), motion(real_motion=FILLS), motion()], muddled
    )

    assert ruling["checks"]["real_motion"] == "fail"
    assert "error" in ruling["agreement"]["real_motion"]
    assert still_failing(ruling) == [FLOATS, FILLS]


def test_when_the_same_fault_call_fails_two_failing_answers_still_fail_their_check() -> None:
    def down(check: str, failing: list[dict[str, Any]]) -> dict[str, Any]:
        raise TimeoutError("no answer")

    ruling = judged(
        [sides()] * 3, [motion(real_motion=FLOATS), motion(real_motion=FILLS), motion()], down
    )

    assert ruling["checks"]["real_motion"] == "fail"
    assert still_failing(ruling) == [FLOATS, FILLS]
    assert ruling["agreement"]["real_motion"] == {"error": "no answer"}


def test_the_same_fault_call_is_made_only_for_a_motion_check_failed_by_two_answers() -> None:
    asked: list[str] = []

    def recorded(check: str, failing: list[dict[str, Any]]) -> dict[str, Any]:
        asked.append(check)
        return apart(check, failing)

    # SIDES is voted on as before: its two answers on different faults still fail it.
    ruling = judged(
        [sides(HANDLE), sides("The mug's base melts at 1 s."), sides()],
        [motion(real_motion=FLOATS), motion(sudden_change=FILLS), motion()],
        recorded,
    )

    assert asked == []
    assert ruling["checks"]["sides"] == "fail"


def test_the_same_fault_call_is_handed_each_failing_answers_problem_and_when_it_ends() -> None:
    handed: list[tuple[str, list[dict[str, Any]]]] = []

    def recorded(check: str, failing: list[dict[str, Any]]) -> dict[str, Any]:
        handed.append((check, failing))
        return one_fault(check, failing)

    judged(
        [sides()] * 3,
        [motion(), motion(sudden_change=(FILLS, "never")), motion(sudden_change=(FLOATS, "1.4"))],
        recorded,
    )

    assert handed == [
        (
            "sudden_change",
            [
                {"ask": 2, "problem": FILLS, "back_to_normal_at": "never"},
                {"ask": 3, "problem": FLOATS, "back_to_normal_at": "1.4"},
            ],
        )
    ]


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
        "SIDES_FRAMES_PER_SECOND",
        "FRAMES_PER_SECOND",
        "AGREE_ON_FAULT",
    ],
)
def test_the_wording_version_changes_whenever_what_the_judge_is_told_does(
    told: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = wording_version()
    now = getattr(clip_judge, told)
    monkeypatch.setattr(clip_judge, told, now + " Changed." if isinstance(now, str) else now + 1)

    assert wording_version() != before


@pytest.mark.parametrize(
    "form",
    [
        "SidesVerdict",
        "MotionVerdict",
        "ClipCheckHandoff",
        "MotionCheckHandoff",
        "AgreeHandoff",
        "Agreement",
    ],
)
def test_the_wording_version_changes_whenever_an_answer_form_or_handoff_does(
    form: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = wording_version()
    # The form with one more field, as when a question is added to it.
    changed = create_model("Changed", __base__=getattr(clip_judge, form), changed_thing=(str, ""))
    monkeypatch.setattr(clip_judge, form, changed)

    assert wording_version() != before


def test_the_wording_version_changes_whenever_a_same_fault_question_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = wording_version()
    monkeypatch.setitem(clip_judge.AGREE_QUESTIONS, "real_motion", "does it move?")

    assert wording_version() != before


def test_still_failing_lists_sides_problems_before_motions_and_leaves_out_the_outvoted() -> None:
    # SIDES and real_motion fail on 2 of 3; the third answer's does_what_was_ordered fail is
    # outvoted.
    ruling = judged(
        [sides(HANDLE), sides(), sides(HANDLE)],
        [motion(real_motion=FLOATS)] * 2 + [motion(does_what_was_ordered=FILLS)],
    )

    assert ruling["checks"] == {
        "sides": "fail",
        "real_motion": "fail",
        "sudden_change": "pass",
        "does_what_was_ordered": "pass",
    }
    assert still_failing(ruling) == [HANDLE, HANDLE, FLOATS, FLOATS]


def test_still_failing_leaves_out_what_a_ruling_from_before_let_go() -> None:
    # Rulings made before the forgive rule was gone are kept on clips, with what they let go.
    blink = "The ring at the rim vanishes at 8.0 s; a real one stays."
    ruling = {
        "checks": {"sides": "pass", "real_motion": "fail"},
        "answers": {
            "sides": [sides()] * 3,
            "motion": [motion(real_motion=FLOATS)] * 2 + [motion(real_motion=(blink, "8.4"))],
        },
        "let_go": [
            {"check": "real_motion", "ask": 3, "problem": blink, "back_to_normal_at": "8.4"}
        ],
    }

    assert still_failing(ruling) == [FLOATS, FLOATS]


def test_a_sides_problem_back_to_normal_soon_still_fails_the_check() -> None:
    blink = "A logo appears on the case's back at 3 s and is gone by the next frame."
    ruling = judged([sides(blink, "3.1 s")] * 2 + [sides()], [motion()] * 3)

    assert ruling["checks"]["sides"] == "fail"


# ---------------------------------------------------------------- the SIDES check as tested


# The phone-flip SIDES check (harness arm F1, evals/clip-judge-v2 prompts.py SIDES_FLIP, tested
# on all 110 graded clips 2026-10-13: 3 of 3 flips caught, 0 wrong fails; Julian's yes to ship
# 2026-10-14 18:30). The judge's words and its answer form are Julian's: both are pinned whole,
# so any change to either fails here until he says so and the pinned copy changes with it.
def test_the_sides_check_is_the_phone_flip_wording_as_tested_word_for_word() -> None:
    assert SIDES_CHECK == (PINNED / "clip_judge_sides_check.txt").read_text()


def test_the_sides_answer_form_is_the_one_tested_field_for_field() -> None:
    # The per-frame and turn fields stay: filling them is what makes the judge decide the
    # direction before it looks at the face that comes back. No code reads them (Julian,
    # 2026-10-14). The schema is what the model is handed, descriptions and choices included.
    pinned = json.loads((PINNED / "clip_judge_sides_verdict_schema.json").read_text())

    assert SidesVerdict.model_json_schema() == pinned


# --- A fix's clip: the question about the owner's change (Julian 2026-10-14) -----------------

NOT_MADE = {
    "old_shows": "The hand hovers beside the mug.",
    "new_shows": "The hand still hovers beside the mug.",
    "verdict": "not fixed",
    "still_wrong": "The hand never turns the mug.",
}


def test_the_answer_about_the_owners_change_never_counts_in_the_ruling() -> None:
    asked = [{**motion(real_motion=FLOATS), "owner_change": NOT_MADE}] * 2 + [
        {**motion(), "owner_change": NOT_MADE}
    ]

    ruling = judged([sides()] * 3, asked)

    # Only the clip's own faults: the change not made is no fault of the clip.
    assert ruling["checks"] == {
        "sides": "pass",
        "real_motion": "fail",
        "sudden_change": "pass",
        "does_what_was_ordered": "pass",
    }
    assert ruling["failed_answers"] == 2
    assert all("owner_change" not in failed["answer"] for failed in ruling["failures"])
    assert clip_judge.still_failing(ruling) == [FLOATS, FLOATS]
    # It is kept with the answers, for the fix check.
    assert [answer["owner_change"] for answer in ruling["answers"]["motion"]] == [NOT_MADE] * 3


def test_a_change_not_made_alone_passes_the_clip() -> None:
    ruling = judged([sides()] * 3, [{**motion(), "owner_change": NOT_MADE}] * 3)

    assert (ruling["verdict"], ruling["failures"]) == ("pass", [])


@pytest.mark.parametrize("told", ["MOTION_FIX_QUESTION", "OLD_FRAMES_LABEL", "NEW_FRAMES_LABEL"])
def test_a_fixs_wording_version_changes_with_its_question_and_no_other_clips_does(
    told: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain, fix = wording_version(), wording_version(fix=True)
    monkeypatch.setattr(clip_judge, told, getattr(clip_judge, told) + " Changed.")

    assert wording_version(fix=True) != fix
    assert wording_version() == plain


def test_a_fixs_wording_version_changes_with_its_answer_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fix = wording_version(fix=True)
    monkeypatch.setattr(
        clip_judge.MotionFixVerdict,
        "model_json_schema",
        classmethod(lambda cls: {"changed": True}),
    )

    assert wording_version(fix=True) != fix


def test_a_change_not_made_that_doesnt_say_why_goes_unchecked_and_the_clips_checks_stay() -> None:
    blank = {**NOT_MADE, "still_wrong": " "}
    # The form takes it, so the clip's own MOTION answers aren't lost with it (review F1).
    clip_judge.MotionFixVerdict.model_validate({**motion(), "owner_change": blank})
    ruling = judged(
        [sides()] * 3,
        [
            {**motion(real_motion=FLOATS), "owner_change": blank},
            None,
            {**motion(real_motion=FLOATS), "owner_change": NOT_MADE},
        ],
    )

    assert ruling["checks"]["real_motion"] == "fail"
    assert clip_judge.owner_change_answers(ruling) == [None, None, NOT_MADE]


# --- The pin: the clip fix check has no settings of its own (spec, part 5) -------------------


@pytest.mark.parametrize(
    "own",
    [
        "FIX_CHECK_CLIP",
        "CLIPS_SHOWN",
        "MOST_PICTURES_PER_CLIP",
        "FixClipVerdict",
        "FixClipCheckHandoff",
        "thinned",
    ],
)
def test_the_fix_check_has_no_clip_wording_frames_or_form_of_its_own(own: str) -> None:
    assert not hasattr(fix_check, own)


def test_the_clip_fix_check_has_no_model_of_its_own() -> None:
    assert not [
        purpose
        for purpose in (*catalog.MODEL_FOR_PURPOSE, *catalog.SLOW_ANSWER_SECONDS)
        if "fix_clip" in purpose
    ]


def test_the_fix_check_settles_the_judges_answers_about_the_owners_change() -> None:
    made = {**NOT_MADE, "verdict": "fixed", "still_wrong": None}

    assert fix_check.settle_all([NOT_MADE, None, NOT_MADE]).verdict == "failed"
    assert fix_check.settle_all([NOT_MADE, None, NOT_MADE]).reason == NOT_MADE["still_wrong"]
    assert fix_check.settle_all([made, NOT_MADE, made]).verdict == "passed"
    assert fix_check.settle_all([None, made, None]).verdict == "unchecked"
    assert fix_check.settle_all([None, made, None]).answers == [made]
