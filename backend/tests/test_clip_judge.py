"""The clip judge's ruling from its answers: each check is asked once and fails the clip when
its answer does (Julian 2026-10-22, "drop the vote"), every failing answer counting (the
half-second forgive rule is gone, Julian 2026-10-19). What it rules is what the fixing agent
acts on: pass or fail, how many of its answers failed, and each failed answer's problem and
everything it saw."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import create_model

from adforge import file_store
from gateway import catalog
from jobs import clip_judge, fix_check
from jobs.clip_judge import SIDES_CHECK, SidesVerdict, judged, still_failing, wording_version
from jobs.library import SentList

from .sent_lists import FIXTURE, piece

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


def test_a_failing_sides_answer_fails_the_clip() -> None:
    ruling = judged([sides(HANDLE)], [motion()])

    assert ruling["verdict"] == "fail"
    assert ruling["checks"] == {
        "sides": "fail",
        "real_motion": "pass",
        "sudden_change": "pass",
        "does_what_was_ordered": "pass",
    }
    assert ruling["failed_answers"] == 1
    assert "agreement" not in ruling


def test_each_motion_question_is_settled_by_itself() -> None:
    ruling = judged([sides()], [motion(sudden_change=FILLS)])

    assert ruling["verdict"] == "fail"
    assert ruling["checks"] == {
        "sides": "pass",
        "real_motion": "pass",
        "sudden_change": "fail",
        "does_what_was_ordered": "pass",
    }
    assert still_failing(ruling) == [FILLS]


def test_passing_answers_pass_the_clip() -> None:
    ruling = judged([sides()], [motion()])

    assert ruling["verdict"] == "pass"
    assert (ruling["failed_answers"], ruling["failures"]) == (0, [])


def test_each_failed_answer_gives_its_problems_and_everything_it_saw() -> None:
    both = motion(real_motion=FLOATS, sudden_change=FILLS)
    ruling = judged([sides(HANDLE)], [both])

    assert ruling["verdict"] == "fail"
    assert ruling["failed_answers"] == 2
    assert ruling["failures"] == [
        {"check": "sides", "ask": 1, "problems": [HANDLE], "answer": sides(HANDLE)},
        {"check": "motion", "ask": 1, "problems": [FLOATS, FILLS], "answer": both},
    ]
    assert still_failing(ruling) == [HANDLE, FLOATS, FILLS]


def test_every_answer_is_kept() -> None:
    answers = ([sides(HANDLE)], [None])

    assert judged(*answers)["answers"] == {"sides": answers[0], "motion": answers[1]}


def test_a_thing_back_to_normal_soon_still_fails_the_check() -> None:
    # The half-second forgive rule is gone (Julian 2026-10-19 03:37): every failing answer
    # counts, however soon its thing is back to normal.
    blink = "The ring at the rim vanishes at 8.0 s; a real one stays."
    ruling = judged([sides()], [motion(sudden_change=(blink, "8.1"))])

    assert ruling["verdict"] == "fail"
    assert still_failing(ruling) == [blink]


def test_a_sides_problem_back_to_normal_soon_still_fails_the_check() -> None:
    blink = "A logo appears on the case's back at 3 s and is gone by the next frame."
    ruling = judged([sides(blink, "3.1 s")], [motion()])

    assert ruling["checks"]["sides"] == "fail"


def test_a_check_with_no_usable_answer_is_not_checked() -> None:
    ruling = judged([None], [motion()])

    assert ruling["verdict"] == "not_checked"
    assert ruling["checks"]["sides"] == "not_checked"
    assert ruling["checks"]["real_motion"] == "pass"


def test_a_failed_check_fails_the_clip_even_when_another_went_unchecked() -> None:
    ruling = judged([None], [motion(real_motion=FLOATS)])

    assert ruling["verdict"] == "fail"
    assert ruling["checks"]["sides"] == "not_checked"


def test_the_judge_asks_each_check_once_with_no_vote_call() -> None:
    assert clip_judge.ASKS == 1
    assert "agree_on_fault" not in catalog.MODEL_FOR_PURPOSE


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


def test_still_failing_leaves_out_what_a_ruling_from_the_vote_outvoted() -> None:
    # Rulings made while each check was asked 3 times are kept on clips (the fixing agent's
    # tries, shown to the shop owner): an answer whose fault no other answer named is left out.
    ruling = {
        "checks": {"sides": "pass", "real_motion": "fail"},
        "answers": {
            "sides": [sides()] * 3,
            "motion": [motion(real_motion=FLOATS), motion(real_motion=FILLS), motion()],
        },
        "agreement": {"real_motion": {"groups": [], "sharing": [1]}},
    }

    assert still_failing(ruling) == [FLOATS]


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
    asked = [{**motion(real_motion=FLOATS), "owner_change": NOT_MADE}]

    ruling = judged([sides()], asked)

    # Only the clip's own faults: the change not made is no fault of the clip.
    assert ruling["checks"] == {
        "sides": "pass",
        "real_motion": "fail",
        "sudden_change": "pass",
        "does_what_was_ordered": "pass",
    }
    assert ruling["failed_answers"] == 1
    assert all("owner_change" not in failed["answer"] for failed in ruling["failures"])
    assert clip_judge.still_failing(ruling) == [FLOATS]
    # It is kept with the answers, for the fix check.
    assert [answer["owner_change"] for answer in ruling["answers"]["motion"]] == [NOT_MADE]


def test_a_change_not_made_alone_passes_the_clip() -> None:
    ruling = judged([sides()], [{**motion(), "owner_change": NOT_MADE}])

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
    ruling = judged([sides()], [{**motion(real_motion=FLOATS), "owner_change": blank}])

    assert ruling["checks"]["real_motion"] == "fail"
    assert clip_judge.owner_change_answers(ruling) == [None]
    assert fix_check.settle_all(clip_judge.owner_change_answers(ruling)).verdict == "unchecked"


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


def test_a_long_piece_is_shown_as_its_frames_thinned_evenly_to_the_most() -> None:
    frames = clip_judge.piece_frames(file_store.save("piece.mp4", piece(4.0)), most=5)

    # 20 frames at 5 a second (0 to 3.8 s), thinned to 5 spread from the first to the last:
    # frames 0, 5, 10, 14 and 19.
    assert [frame["times"] for frame in frames] == [[0.0], [1.0], [2.0], [2.8], [3.8]]


def test_a_piece_cut_at_its_videos_end_is_said_so() -> None:
    raw = json.loads(FIXTURE.read_text())
    raw["pieces"][1]["trim"] = "video_ended"

    said = clip_judge.references_said(SentList.model_validate(raw))

    assert "of its 3 s to 15 s because the moment ran past its video's end." in said


def test_a_piece_asked_for_again_gives_the_same_frames() -> None:
    kept = file_store.save("piece.mp4", piece(1.0))

    assert clip_judge.piece_frames(kept) == clip_judge.piece_frames(kept)
