"""The clip judge of a clip that carries its sent list (#210 point 18, #228), driven through the
chat from the first message: it is shown exactly what the video API got, the sent pictures in
their order and each piece's frames, then the clip's frames, and is told the planner's note on
how this product behaves. Its first question is whether those show enough to judge the clip's
action: when they don't, the clip is kept, the shop owner is told in one line to check it
themselves and neither check runs; when they do, the product and motion checks answer pass,
fail or unsure, and an unsure check keeps the clip for the shop owner to decide (Julian, Oct 29
00:10). A clip with no sent list is judged as before. Every model is faked at the gateway; the
sent list is the #211 fixture, kept on each clip as it is made, as cut-and-send will."""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel
from jobs import work
from jobs.auto_fix import faults as judge_faults
from jobs.library import SentList
from jobs.models import AgentFix, Job, ProducedItem, SceneStep

from .conftest import (
    CLIP_MOTION_OK,
    CLIP_PRODUCT_OK,
    REFERENCES_ENOUGH,
    HeldSteps,
    handoffs,
    motion_answer,
    paid_for,
)
from .fake_fixing_agent import FakeAgent, ScriptedTry, install
from .sent_lists import holding_the_fixture
from .test_fix_wiring import decided, planning, plans, row, tap_yes
from .test_producer_broll import (
    assemble_through_the_chat,
    checked,  # noqa: F401 (a fixture)
)
from .test_producer_clip_judge import clip_of, ruling
from .test_producer_page import notices
from .test_safety_filter_judges import refusing
from .test_safety_filter_split import labels_shown

pytestmark = pytest.mark.django_db(transaction=True)

# What the judge is told the references never show, when it can't tell.
NEVER_SHOWN = "the press's chamber with garlic inside it"
CANNOT_TELL: dict[str, Any] = {
    **REFERENCES_ENOUGH,
    "verdict": "cannot tell",
    "references_never_show": NEVER_SHOWN,
}


@pytest.fixture
def sent() -> list[SentList]:
    """The sent list kept on each clip as it is made, in the order made."""
    return []


@pytest.fixture
def sent_on_every_clip(monkeypatch: pytest.MonkeyPatch, sent: list[SentList]) -> Iterator[None]:
    """Each B-roll clip carries the fixture's sent list from the moment it is made, its pieces
    3 s long, as cut-and-send will keep it (#239)."""
    make = work._make_clip

    def made(step: SceneStep, **kwargs: Any) -> ProducedItem:
        clip = make(step, **kwargs)
        sent.append(holding_the_fixture(clip, piece_seconds=3.0))
        return clip

    monkeypatch.setattr(work, "_make_clip", made)
    yield


def image_labels(sent: SentList) -> list[str]:
    return [f"Image {picture.image}: {picture.label}" for picture in sent.pictures]


def test_the_judge_is_shown_what_the_clip_was_sent_then_the_clip_and_told_how_it_behaves(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    sent_on_every_clip: None,
    sent: list[SentList],
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    clip = clip_of(fake_model, steps, say, 2)

    [kept] = sent
    behaviour = Job.objects.get().product_behaviour
    assert behaviour
    # The first question, then the two checks: the old SIDES check is not asked.
    assert paid_for().count("check_clip_references") == 1
    assert paid_for().count("check_clip_product") == 1
    assert paid_for().count("check_clip_motion") == 1
    assert "check_clip_sides" not in paid_for()
    for purpose in ("check_clip_references", "check_clip_product", "check_clip_motion"):
        [shown] = labels_shown(purpose)
        # The sent pictures in their order, then each piece's frames, at most 10 in all,
        # thinned evenly, then the clip's frames.
        assert shown[:9] == image_labels(kept)
        pieces = shown[9:19]
        assert [label.split(",")[0] for label in pieces] == ["Video 1"] * 5 + ["Video 2"] * 5
        assert pieces[0] == "Video 1, Frame at 0 s" and pieces[-1].startswith(
            "Video 2, Frame at 2."
        )
        assert shown[19].startswith("Frames from the clip, in order:\n")
        [handed] = handoffs(purpose)
        assert handed["how_this_product_behaves"] == behaviour
        assert handed["prompt_sent_to_the_video_api"] == kept.prompt
        assert handed["references"][:9] == image_labels(kept)
        # Never the old inputs: the shop's text, the shop photo or the needs photos.
        assert "facts" not in handed
    # The ruling records what it saw and what it was told.
    judged = ruling(clip)
    assert judged["verdict"] == "pass"
    assert judged["behaviour"] == behaviour
    assert [each["label"] for each in judged["pictures"]["references"]][:9] == image_labels(kept)
    assert judged["answers"]["references"]["verdict"] == "enough"
    assert set(judged["checks"]) == {
        "product",
        "real_motion",
        "sudden_change",
        "does_what_was_ordered",
    }


def test_when_the_references_cant_show_enough_the_clip_is_kept_and_the_owner_told_to_check_it(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    sent_on_every_clip: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted("check_clip_references", lambda _: CANNOT_TELL)

    clip = clip_of(fake_model, steps, say, 2)

    # Neither check runs, and no fix starts: the clip is the scene's, for the owner to judge.
    assert "check_clip_product" not in paid_for()
    assert "check_clip_motion" not in paid_for()
    assert not AgentFix.objects.exists()
    assert ProducedItem.objects.filter(kind="clip", scene__number=2).count() == 1
    step = SceneStep.objects.get(kind="clip", scene__number=2)
    assert step.status == "finished"
    judged = ruling(clip)
    assert judged["verdict"] == "unsure"
    assert judged["cannot_tell"] == NEVER_SHOWN
    assert judged["checks"] == {}
    line = (
        f"The clip judge can't tell whether scene 2's clip is right: the references never show "
        f"{NEVER_SHOWN}, check this one yourself."
    )
    assert [text for _, text in notices(api, session_id)].count(line) == 1
    assert "couldn't tell whether this clip is right" in step.result


def test_an_owners_no_on_a_clip_the_judge_couldnt_tell_about_goes_to_the_owner_note_door(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    sent_on_every_clip: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_model.answer_unscripted("check_clip_references", lambda _: CANNOT_TELL)
    assemble_through_the_chat(fake_model, steps, say)
    assert ruling(ProducedItem.objects.get(kind="clip", scene__number=2))["verdict"] == "unsure"
    assert not AgentFix.objects.exists()
    agent = install(monkeypatch, FakeAgent(tries=[ScriptedTry()]))

    goal = "The press is squeezed shut over the bowl and garlic comes out."
    fake_model.respond(
        "decide_fixes",
        planning(("look_at_references", {"scene": 2}), decided(row(2, "fix", goal))),
    )
    say("No, that's not how the press works in scene 2.")
    [plan] = plans()
    tap_yes(api, session_id, plan)
    steps.run_held()

    [handed] = agent.handed
    assert (handed["door"], handed["goal"]) == ("owner", goal)
    assert "not how the press works" in handed["failure"].note


def test_a_product_check_unsure_keeps_the_clip_for_the_owner_and_a_fail_goes_to_the_fixer(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    sent_on_every_clip: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted(
        "check_clip_product",
        lambda _: {**CLIP_PRODUCT_OK, "verdict": "unsure", "references_never_show": "its base"},
    )

    clip = clip_of(fake_model, steps, say, 2)

    judged = ruling(clip)
    assert judged["verdict"] == "unsure"
    assert judged["checks"]["product"] == "unsure"
    assert judged["checks"]["real_motion"] == "pass"
    assert not AgentFix.objects.exists()
    assert (
        "The clip judge can't tell whether scene 2's clip is right: the references never show "
        "its base, check this one yourself."
    ) in [text for _, text in notices(api, session_id)]


def test_a_product_check_fail_is_a_failed_clip_with_its_fault(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    sent_on_every_clip: None,
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fault = "The press has a second handle on its back at 2.1 s; the real one has one."
    fake_model.answer_unscripted(
        "check_clip_product",
        lambda _: {**CLIP_PRODUCT_OK, "verdict": "fail", "fault": fault},
    )
    agent = install(monkeypatch, FakeAgent(tries=[ScriptedTry()]))

    clip = clip_of(fake_model, steps, say, 2)

    judged = ruling(clip)
    assert judged["verdict"] == "fail"
    assert judged["checks"]["product"] == "fail"
    assert judged["failures"][0]["check"] == "product"
    assert judge_faults(judged) == [fault]
    [handed] = agent.handed
    assert handed["door"] == "judge"


def test_a_clip_frame_the_filter_refuses_makes_the_check_unsure_for_that_second(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    sent_on_every_clip: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    def the_frame_at_3_s(label: str) -> bool:
        return label.endswith("Frame at 3 s")

    fake_model.answer_unscripted(
        "check_clip_motion", refusing(the_frame_at_3_s, motion_answer(CLIP_MOTION_OK))
    )

    clip = clip_of(fake_model, steps, say, 2)

    # Asked again without that one frame, never without the references: the rest passes, so
    # MOTION is unsure for that second and the clip is kept for the owner to decide.
    first, *_, then = labels_shown("check_clip_motion")
    assert [label for label in first if label not in then] == ["Frame at 3 s"]
    judged = ruling(clip)
    assert judged["verdict"] == "unsure"
    assert judged["checks"]["real_motion"] == "unsure"
    assert judged["checks"]["product"] == "pass"
    assert judged["refused_frames"] == [{"check": "motion", "ask": 1, "seconds": [3.0]}]
    assert not AgentFix.objects.exists()
    ((level, said),) = [
        (level, text) for level, text in notices(api, session_id) if "safety filter" in text
    ]
    assert level == "problem" and "scene 2's clip at 3 s" in said and "check this one" in said
    assert said in [warning["text"] for warning in Job.objects.get().warnings]
