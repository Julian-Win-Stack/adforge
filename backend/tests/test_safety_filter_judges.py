"""Requirement 22 for the judges, driven through the chat: when the safety filter refuses the
clip judge or the fix check over one of the reference pictures it is shown beside what it
judges, the check is asked again without it and answers on the rest. What it
judges itself (the clip's frames, the new picture) is never left out: a check refused over it
is not checked, as before. Every refusal shows in the chat as a red notice, kept as a warning.
(A B-roll scene's starting picture and its check are gone, spec #210 point 19.)"""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel
from gateway.types import BlockedBySafetyFilter, ModelRequest
from jobs.models import Job, ProducedItem, SceneStep

from .agent_tries import agent_films
from .conftest import CLIP_MOTION_OK, CLIP_SIDES_OK, HeldSteps, motion_answer
from .test_producer_broll import (
    calling,
    checked,  # noqa: F401 (a fixture)
    made_ready,
    run,
)
from .test_producer_clip_judge import clip_of, ruling
from .test_safety_filter_split import labels_shown, refusals

pytestmark = pytest.mark.django_db(transaction=True)

SHOP_PHOTO = "The shop photo of the product:"


def refusing(
    refused: Callable[[str], bool], answer: Callable[[ModelRequest[Any]], dict[str, Any]]
) -> Callable[[ModelRequest[Any]], dict[str, Any]]:
    """A model whose safety filter refuses any call showing a picture whose label `refused`
    picks out, and otherwise answers with `answer`."""

    def answering(request: ModelRequest[Any]) -> dict[str, Any]:
        if any(refused(image.label) for image in request.images):
            raise BlockedBySafetyFilter("content_policy_violation")
        return answer(request)

    return answering


def test_a_shop_photo_the_clip_judges_filter_refuses_is_left_out_and_the_clip_judged(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted(
        "check_clip_sides", refusing(lambda label: label == SHOP_PHOTO, lambda _: CLIP_SIDES_OK)
    )
    fake_model.answer_unscripted(
        "check_clip_motion",
        refusing(lambda label: label == SHOP_PHOTO, motion_answer(CLIP_MOTION_OK)),
    )

    clip = clip_of(fake_model, steps, say, 2)

    # Each check is shown the shop photo and the clip's frames, then the frames alone.
    for purpose in ("check_clip_sides", "check_clip_motion"):
        first, then = labels_shown(purpose)
        assert first[0] == SHOP_PHOTO and then == first[1:]
    judged = ruling(clip)
    assert judged["verdict"] == "pass"
    assert judged["not_shown"] == [
        {"check": "sides", "ask": 1, "pictures": ["the shop photo of the product"]},
        {"check": "motion", "ask": 1, "pictures": ["the shop photo of the product"]},
    ]
    ((level, said),) = refusals(api, session_id)
    assert level == "problem"
    assert "the shop photo of the product" in said and "scene 2's clip" in said
    assert said in [warning["text"] for warning in Job.objects.get().warnings]
    assert SceneStep.objects.get(kind="clip", scene__number=2).status == "finished"


def test_a_clip_the_judges_filter_refuses_to_look_at_is_kept_unchecked_and_the_chat_told(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    def a_frame(label: str) -> bool:
        return label.startswith("Frames from the clip")

    fake_model.answer_unscripted("check_clip_sides", refusing(a_frame, lambda _: CLIP_SIDES_OK))

    clip = clip_of(fake_model, steps, say, 2)

    # The shop photo is left out to try, never the clip's frames: refused even so, SIDES
    # goes unchecked and MOTION's answer stands.
    first, then = labels_shown("check_clip_sides")
    assert then == first[1:]
    judged = ruling(clip)
    assert judged["verdict"] == "not_checked"
    assert judged["checks"]["real_motion"] == "pass"
    assert judged["refused_by_the_filter"] == [{"check": "sides", "ask": 1}]
    assert "not_shown" not in judged
    ((level, said),) = refusals(api, session_id)
    assert level == "problem" and "scene 2's clip" in said and "check it yourself" in said
    assert said in [warning["text"] for warning in Job.objects.get().warnings]
    step = SceneStep.objects.get(kind="clip", scene__number=2)
    assert step.status == "finished"
    assert "couldn't check this clip, so it is kept unchecked" in step.result


def test_a_clip_the_filter_refuses_to_look_at_but_the_other_check_fails_isnt_said_to_be_kept(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def a_frame(label: str) -> bool:
        return label.startswith("Frames from the clip")

    fake_model.answer_unscripted("check_clip_sides", refusing(a_frame, lambda _: CLIP_SIDES_OK))
    fails = {"verdict": "fail", "problem": "The mug floats.", "back_to_normal_at": "never"}
    fake_model.respond("check_clip_motion", {**CLIP_MOTION_OK, "real_motion": fails})
    agent = agent_films(monkeypatch, "The mug stays on the table.")

    made_ready(fake_model, steps, say, (2,))
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    run(fake_model, steps)
    clip = ProducedItem.objects.get(kind="clip", scene__number=2, version=1)

    # MOTION failed it, so the fixing agent films it again: the chat isn't told it stays.
    # (The try, refused the same way and passed by MOTION, is said to be kept.)
    assert ruling(clip)["verdict"] == "fail"
    assert [door for _, door, _, _ in agent.handed] == ["judge"]
    (level, said), (_, about_the_try) = refusals(api, session_id)
    assert "check it yourself" in about_the_try
    assert level == "problem" and "scene 2's clip" in said
    assert "kept" not in said and "check it yourself" not in said
