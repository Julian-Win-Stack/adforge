"""The clip judge's ruling from its answers: each check fails a clip on 2 of its 3 answers,
and a problem whose thing is back to normal within half a second is let go, its note kept
(change 7, Julian 2026-10-13). What it rules is what the auto-redo acts on: pass or fail, how
many of the 6 answers failed, and each failed answer's problem and everything it saw."""

import json
from pathlib import Path
from typing import Any

import pytest

from gateway import catalog
from jobs import clip_judge, fix_check
from jobs.clip_judge import SIDES_CHECK, SidesVerdict, judged, wording_version

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
    "told",
    [
        "MOTION_CHECK",
        "SIDES_CHECK",
        "PAGE_FRAME",
        "CONVERSATION_FRAME",
        "FRAMES_LABEL",
        "SIDES_FRAMES_PER_SECOND",
        "FRAMES_PER_SECOND",
    ],
)
def test_the_wording_version_changes_whenever_what_the_judge_is_told_does(
    told: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = wording_version()
    now = getattr(clip_judge, told)
    monkeypatch.setattr(clip_judge, told, now + " Changed." if isinstance(now, str) else now + 1)

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


def test_a_change_not_made_must_say_what_is_still_wrong() -> None:
    with pytest.raises(ValueError, match="must say what is still wrong"):
        clip_judge.FixAnswer(old_shows="a", new_shows="b", verdict="not fixed", still_wrong=" ")


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
