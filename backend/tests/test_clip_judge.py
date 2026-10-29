"""The clip judge's ruling from its answers: each check is asked once and fails the clip when
its answer does (Julian 2026-10-22, "drop the vote"), every failing answer counting (the
half-second forgive rule is gone, Julian 2026-10-19). What it rules is what the fixing agent
acts on: pass or fail, how many of its answers failed, and each failed answer's problem and
everything it saw."""

import json
from typing import Any

import pytest
from pydantic import create_model

from adforge import file_store
from gateway import catalog
from jobs import clip_judge, fix_check
from jobs.auto_fix import judge_saw
from jobs.clip_judge import judged, still_failing, wording_version
from jobs.library import SentList

from .sent_lists import FIXTURE, piece

SEEN = [{"time": 0.0, "seen": "broad_face", "face": "front", "parts": "", "thin_edge_side": "none"}]


def product(verdict: str = "pass", fault: str | None = None) -> dict[str, Any]:
    """A PRODUCT answer."""
    return {
        "product_from_references": "A garlic press with two handles.",
        "frames": [],
        "verdict": verdict,
        "compared_against": "Image 1" if fault else None,
        "clip_shows": None,
        "real_product_shows": None,
        "fault_frame_times": [],
        "fault": fault,
        "references_never_show": "its open chamber" if verdict == "unsure" else None,
    }


def sides(problem: str | None = None, back: str | None = None) -> dict[str, Any]:
    """A SIDES answer, as a ruling made before the SIDES check went (#241) holds it."""
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


def test_a_failing_product_answer_fails_the_clip() -> None:
    ruling = judged([product("fail", HANDLE)], [motion()])

    assert ruling["verdict"] == "fail"
    assert ruling["checks"] == {
        "product": "fail",
        "real_motion": "pass",
        "sudden_change": "pass",
        "does_what_was_ordered": "pass",
    }
    assert ruling["failed_answers"] == 1
    assert "agreement" not in ruling


def test_each_motion_question_is_settled_by_itself() -> None:
    ruling = judged([product()], [motion(sudden_change=FILLS)])

    assert ruling["verdict"] == "fail"
    assert ruling["checks"] == {
        "product": "pass",
        "real_motion": "pass",
        "sudden_change": "fail",
        "does_what_was_ordered": "pass",
    }
    assert still_failing(ruling) == [FILLS]


def test_passing_answers_pass_the_clip() -> None:
    ruling = judged([product()], [motion()])

    assert ruling["verdict"] == "pass"
    assert (ruling["failed_answers"], ruling["failures"]) == (0, [])


def test_each_failed_answer_gives_its_problems_and_everything_it_saw() -> None:
    both = motion(real_motion=FLOATS, sudden_change=FILLS)
    ruling = judged([product("fail", HANDLE)], [both])

    assert ruling["verdict"] == "fail"
    assert ruling["failed_answers"] == 2
    assert ruling["failures"] == [
        {"check": "product", "ask": 1, "problems": [HANDLE], "answer": product("fail", HANDLE)},
        {"check": "motion", "ask": 1, "problems": [FLOATS, FILLS], "answer": both},
    ]
    assert still_failing(ruling) == [HANDLE, FLOATS, FILLS]


def test_every_answer_is_kept() -> None:
    answers = ([product("fail", HANDLE)], [None])

    assert judged(*answers)["answers"] == {"product": answers[0], "motion": answers[1]}


def test_a_thing_back_to_normal_soon_still_fails_the_check() -> None:
    # The half-second forgive rule is gone (Julian 2026-10-19 03:37): every failing answer
    # counts, however soon its thing is back to normal.
    blink = "The ring at the rim vanishes at 8.0 s; a real one stays."
    ruling = judged([product()], [motion(sudden_change=(blink, "8.1"))])

    assert ruling["verdict"] == "fail"
    assert still_failing(ruling) == [blink]


def test_a_check_with_no_usable_answer_is_not_checked() -> None:
    ruling = judged([None], [motion()])

    assert ruling["verdict"] == "not_checked"
    assert ruling["checks"]["product"] == "not_checked"
    assert ruling["checks"]["real_motion"] == "pass"


def test_a_failed_check_fails_the_clip_even_when_another_went_unchecked() -> None:
    ruling = judged([None], [motion(real_motion=FLOATS)])

    assert ruling["verdict"] == "fail"
    assert ruling["checks"]["product"] == "not_checked"


def test_the_judge_asks_each_check_once_with_no_vote_call() -> None:
    assert clip_judge.ASKS == 1
    assert "agree_on_fault" not in catalog.MODEL_FOR_PURPOSE


@pytest.mark.parametrize(
    "told",
    [
        "REFERENCES_QUESTION",
        "PRODUCT_CHECK",
        "MOTION_FROM_REFERENCES",
        "BEHAVIOUR_FRAME",
        "SENT_PICTURE_LABEL",
        "SENT_PIECE_LABEL",
        "SENT_PIECE_FRAME_LABEL",
        "FRAMES_LABEL",
        "PRODUCT_FRAMES_PER_SECOND",
        "FRAMES_PER_SECOND",
        "PIECE_FRAMES_SHOWN",
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
        "ProductVerdict",
        "MotionVerdict",
        "ReferencesVerdict",
        "SentClipCheckHandoff",
        "SentMotionCheckHandoff",
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


# --- A fix's clip: the question about the owner's change (Julian 2026-10-14) -----------------

NOT_MADE = {
    "old_shows": "The hand hovers beside the mug.",
    "new_shows": "The hand still hovers beside the mug.",
    "verdict": "not fixed",
    "still_wrong": "The hand never turns the mug.",
}


def test_the_answer_about_the_owners_change_never_counts_in_the_ruling() -> None:
    asked = [{**motion(real_motion=FLOATS), "owner_change": NOT_MADE}]

    ruling = judged([product()], asked)

    # Only the clip's own faults: the change not made is no fault of the clip.
    assert ruling["checks"] == {
        "product": "pass",
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
    ruling = judged([product()], [{**motion(), "owner_change": NOT_MADE}])

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
    ruling = judged([product()], [{**motion(real_motion=FLOATS), "owner_change": blank}])

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


# ---------------------------------------------------------------- a clip with its sent list (#228)


def test_a_product_check_unsure_with_nothing_failing_makes_the_clip_unsure() -> None:
    ruling = judged([product("unsure")], [motion()])

    assert ruling["verdict"] == "unsure"
    assert ruling["checks"]["product"] == "unsure"
    assert ruling["answers"]["product"] == [product("unsure")]


def test_a_fail_beats_an_unsure_and_its_fault_is_the_problem() -> None:
    wrong = "A second handle on the back at 2.1 s."
    ruling = judged([product("fail", wrong)], [motion()])

    assert ruling["verdict"] == "fail"
    assert ruling["failures"][0]["check"] == "product"
    assert still_failing(ruling) == [wrong]
    assert judge_saw(ruling) == [product("fail", wrong)]


def test_an_ask_answered_without_refused_frames_is_unsure_unless_it_fails() -> None:
    blind = [("motion", 1)]
    ruling = judged([product()], [motion()], blind=blind)

    assert ruling["verdict"] == "unsure"
    assert ruling["checks"]["real_motion"] == "unsure"
    assert ruling["checks"]["product"] == "pass"
    failed = judged([product()], [motion(real_motion=FLOATS)], blind=blind)
    assert failed["checks"]["real_motion"] == "fail"
    assert failed["checks"]["sudden_change"] == "unsure"


def test_the_sent_lists_pieces_share_at_most_ten_frames_shown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = SentList.model_validate(json.loads(FIXTURE.read_text()))
    key = file_store.save("piece.mp4", piece(4.0))
    sent = sent.model_copy(
        update={"pieces": [each.model_copy(update={"file": key}) for each in sent.pieces]}
    )

    shown = clip_judge.sent_references(sent)

    assert [each["label"] for each in shown[:2]] == [
        "Image 1. The prompt says: Image 1 is the garlic press, closed, seen from the front.",
        "Image 2. The prompt says: Image 2 is a hand squeezing the press over a bowl: copy the "
        "hand and how it holds the press.",
    ]
    videos = [each["label"] for each in shown[9:]]
    assert len(videos) == 10
    assert videos[0].endswith("\nVideo 1, Frame at 0 s")
    assert videos[5].endswith("\nVideo 2, Frame at 0 s")


def test_a_picture_or_piece_the_prompt_never_names_is_labelled_from_the_sent_list() -> None:
    sent = SentList.model_validate(json.loads(FIXTURE.read_text()))
    # A prompt with only its speed line and sentence, as a fixing agent's own prompt may be.
    sent = sent.model_copy(update={"prompt": "Film this in real-time speed. She squeezes it."})

    said = clip_judge.sent_said(sent)

    assert said[0] == "Image 1: The garlic press, closed, seen from the front."
    assert said[-1] == "Video 2: only for the movement, not the look"


def test_the_prompt_names_each_picture_by_its_first_sentence_naming_it() -> None:
    said = clip_judge.prompt_says(
        "Film this.\nImage 1 is the jar, lid on. Image 2 is a hand on it.\nImage 1 opens. "
        "Video 1 is only for the movement."
    )

    assert said == {
        "Image 1": "Image 1 is the jar, lid on.",
        "Image 2": "Image 2 is a hand on it.",
        "Video 1": "Video 1 is only for the movement.",
    }
