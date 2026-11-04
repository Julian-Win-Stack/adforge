"""The retained clip-judge API reads authentic historical sent-list inputs (#210, #228).
A checked job comes from the owner's chat; its stored clip carries the fixed nine-picture,
two-video provenance fixture. The judge is then called explicitly, with only external models
faked at the gateway. No retired clip worker or video provider runs.

The cases preserve reference order, product/motion verdicts, partial-frame safety refusals,
retries and notices. Stored clips and their old sent-list format remain readable while new
clips use the scene-agent path covered in test_scene_film.py."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel
from gateway.gateway import MOST_REFUSED_PROBES
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter, Image, ModelRequest, UnusableReply
from jobs import clip_judge, work
from jobs.auto_fix import faults as judge_faults
from jobs.clip_judge import judged, still_failing
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
    recorded,
)
from .legacy_records import legacy_clip_of_scene_2
from .sent_lists import holding_the_fixture
from .test_producer_broll import (
    checked,  # noqa: F401 (a fixture)
)
from .test_producer_clip_judge import ruling
from .test_producer_page import notices
from .test_safety_filter_split import labels_shown

pytestmark = [pytest.mark.django_db(transaction=True)]


def clip_of(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], scene: int
) -> ProducedItem:
    """Judge stored media through the retained API, without dispatching its old worker."""
    assert scene == 2
    step = legacy_clip_of_scene_2(fake_model, steps, say)
    clip = step.produced.get(kind=ProducedItem.Kind.CLIP)
    holding_the_fixture(clip, piece_seconds=3.0)
    clip_judge.judge_clip(step, clip)
    return clip


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


# What the judge is told the references never show, when they don't show the product.
NEVER_SHOWN = "the press's chamber with garlic inside it"
CANNOT_TELL: dict[str, Any] = {
    **REFERENCES_ENOUGH,
    "verdict": "product not shown",
    "asked_for": True,
    "references_never_show": NEVER_SHOWN,
}
# The one detail the references never show, when they show the product.
DETAIL = "the press's base"
DETAIL_NOT_SHOWN: dict[str, Any] = {
    **REFERENCES_ENOUGH,
    "verdict": "detail not shown",
    "references_never_show": DETAIL,
}


def lines_of(sent: SentList) -> list[str]:
    """The fixture prompt's line for each of its 9 pictures, in order: each line is one
    sentence naming one picture, so it is the label the video API got for it."""
    return sent.prompt.splitlines()[1:10]


def image_labels(sent: SentList) -> list[str]:
    """Each picture as the judge is shown it: as the brief labels what the video API got."""
    return [f"Image {n}. The prompt says: {line}" for n, line in enumerate(lines_of(sent), 1)]


def test_the_judge_is_shown_what_the_clip_was_sent_then_the_clip_and_told_how_it_behaves(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    clip = clip_of(fake_model, steps, say, 2)

    assert clip.sent is not None
    kept = SentList.model_validate(clip.sent)
    behaviour = Job.objects.get().product_behaviour
    assert behaviour
    step = SceneStep.objects.get(kind="clip", scene__number=2)
    # The first question, then the two checks.
    assert paid_for().count("check_clip_references") == 1
    assert paid_for().count("check_clip_product") == 1
    assert paid_for().count("check_clip_motion") == 1
    for purpose in ("check_clip_references", "check_clip_product", "check_clip_motion"):
        [shown] = labels_shown(purpose)
        # The sent pictures in their order, then each piece's frames, at most 10 in all,
        # thinned evenly, then the clip's frames.
        assert shown[:9] == image_labels(kept)
        pieces = shown[9:19]
        assert [label.split(",")[0] for label in pieces] == ["Video 1"] * 5 + ["Video 2"] * 5
        assert pieces[0] == (
            "Video 1, frames cut from the reference video, time in seconds from its start. The "
            "prompt says: Video 1 is only for the movement, not the look.\nVideo 1, Frame at 0 s"
        )
        assert pieces[1] == "Video 1, Frame at 0.8 s"
        assert pieces[-1].startswith("Video 2, Frame at 2.")
        assert shown[19].startswith("Frames from the clip, in order:\n")
        # Never more pictures than a model takes: the clip's frames are joined to fit.
        assert len(shown) <= clip_judge.MOST_PICTURES
        [handed] = handoffs(purpose)
        assert (handed["ask"], handed["product"]) == (1, Job.objects.get().product_name)
        assert handed["clip_seconds"] == clip.seconds
        assert handed.get("order") == (None if purpose == "check_clip_product" else step.shows)
        assert handed["how_this_product_behaves"] == behaviour
        assert handed["prompt_sent_to_the_video_api"] == kept.prompt
        assert handed["references"] == [
            *(f"Image {n}: {line}" for n, line in enumerate(lines_of(kept), 1)),
            "Video 1: Video 1 is only for the movement, not the look.",
            "Video 2: Video 2 is only for the movement, not the look.",
        ]
        # Never the old inputs: the shop's text, the shop photo or the needs photos.
        assert "facts" not in handed
        # No detail to leave out when the references show enough.
        assert "detail_the_references_never_show" not in handed
    # The first question sees the clip as MOTION does, at MOTION's frames a second.
    [first] = labels_shown("check_clip_references")
    [moving] = labels_shown("check_clip_motion")
    assert first[19:] == moving[19:]
    # The ruling records what it saw and what it was told.
    judged = ruling(clip)
    assert judged["verdict"] == "pass"
    assert judged["behaviour"] == behaviour
    assert [each["label"] for each in judged["pictures"]["references"]][:9] == image_labels(kept)
    assert judged["answers"]["references"]["verdict"] == "enough"
    assert set(judged["checks"]) == {"product"}


def test_when_the_references_dont_show_the_product_only_motion_runs_and_the_owner_checks_it(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted("check_clip_references", lambda _: CANNOT_TELL)

    clip = clip_of(fake_model, steps, say, 2)

    # The product check is skipped, the motion check still runs (#290), and as it passes no
    # fix starts: the clip is the scene's, for the owner to judge.
    assert "check_clip_product" not in paid_for()
    assert paid_for().count("check_clip_motion") == clip_judge.ASKS
    assert not AgentFix.objects.exists()
    assert ProducedItem.objects.filter(kind="clip", scene__number=2).count() == 1
    step = SceneStep.objects.get(kind="clip", scene__number=2)
    assert step.status == "finished"
    judged = ruling(clip)
    assert judged["verdict"] == "unsure"
    assert judged["cannot_tell"] == NEVER_SHOWN
    assert judged["checks"] == {}
    assert judged["answers"]["product"] == []
    assert len(judged["answers"]["motion"]) == clip_judge.ASKS
    # What it was asked, said and told is kept as on any ruling.
    assert judged["answers"]["references"] == CANNOT_TELL
    assert judged["wording"] == clip_judge.wording_version()
    assert judged["behaviour"] == Job.objects.get().product_behaviour
    assert set(judged["models"]) == {"references", "product", "motion"}
    line = (
        "Please watch scene 2's clip: I couldn't check whether it's right, as your photos "
        f"never show {NEVER_SHOWN}."
    )
    assert notices(api, session_id).count(("info", line)) == 1
    [notice] = [
        one
        for one in api.get(f"/api/sessions/{session_id}/messages/").json()
        if one["text"] == line
    ]
    assert notice["watch"] == {"scene": 2, "clip": clip.pk}
    assert "couldn't tell whether this clip is right" in work.what_the_judge_did(step, clip)


def test_when_product_is_skipped_old_motion_fields_cannot_fail_the_clip(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted("check_clip_references", lambda _: CANNOT_TELL)
    fails = {
        "verdict": "fail",
        "problem": "A hand passes through the press.",
        "back_to_normal_at": "never",
    }
    fake_model.answer_unscripted(
        "check_clip_motion", motion_answer({**CLIP_MOTION_OK, "real_motion": fails})
    )

    clip = clip_of(fake_model, steps, say, 2)

    # #350: skipping PRODUCT stays unsure, regardless of a legacy motion answer.
    judged = ruling(clip)
    assert judged["verdict"] == "unsure"
    assert judged["checks"] == {}
    assert "product" not in judged["checks"]
    assert judged["cannot_tell"] == NEVER_SHOWN
    assert still_failing(judged) == []
    assert not AgentFix.objects.exists()
    assert any("Please watch" in text for _, text in notices(api, session_id))


def test_when_product_is_skipped_an_unreadable_description_stays_unsure(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted("check_clip_references", lambda _: CANNOT_TELL)
    fake_model.respond(
        "check_clip_motion", UnusableReply("no answer", input_tokens=1, output_tokens=1)
    )

    clip = clip_of(fake_model, steps, say, 2)

    judged = ruling(clip)
    assert judged["verdict"] == "unsure"
    assert judged["checks"] == {}
    assert judged["cannot_tell"] == NEVER_SHOWN
    step = SceneStep.objects.get(kind="clip", scene__number=2)
    assert "couldn't tell whether this clip is right" in work.what_the_judge_did(step, clip)


def test_when_one_detail_isnt_shown_both_checks_run_leaving_it_out_and_the_owner_is_told(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted("check_clip_references", lambda _: DETAIL_NOT_SHOWN)

    clip = clip_of(fake_model, steps, say, 2)

    # The product and motion checks still run, each told the detail to leave out.
    assert paid_for().count("check_clip_product") == 1
    assert paid_for().count("check_clip_motion") == 1
    for purpose in ("check_clip_product", "check_clip_motion"):
        [handed] = handoffs(purpose)
        assert handed["detail_the_references_never_show"] == DETAIL
        [call] = ModelCall.objects.filter(purpose=purpose)
        assert call.system_prompt is not None
        assert call.system_prompt.instructions.endswith(clip_judge.DETAIL_LEFT_OUT)
    # Their verdict is the clip's: it passes, and no fix starts.
    judged = ruling(clip)
    assert judged["verdict"] == "pass"
    assert judged["detail_not_shown"] == DETAIL
    assert "cannot_tell" not in judged
    assert judged["answers"]["references"] == DETAIL_NOT_SHOWN
    assert not AgentFix.objects.exists()
    # The detail the judge couldn't check goes to the owner, once.
    line = (
        "Please watch scene 2's clip: I checked it, but your photos never show "
        f"{DETAIL}, so I couldn't check that part."
    )
    assert notices(api, session_id).count(("info", line)) == 1
    [notice] = [
        one
        for one in api.get(f"/api/sessions/{session_id}/messages/").json()
        if one["text"] == line
    ]
    assert notice["watch"] == {"scene": 2, "clip": clip.pk}


def test_a_detail_not_shown_with_an_unsure_product_check_tells_the_owner_once(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted("check_clip_references", lambda _: DETAIL_NOT_SHOWN)
    fake_model.answer_unscripted(
        "check_clip_product",
        lambda _: {**CLIP_PRODUCT_OK, "verdict": "unsure", "references_never_show": "its base"},
    )

    clip = clip_of(fake_model, steps, say, 2)

    assert ruling(clip)["verdict"] == "unsure"
    told = [text for _, text in notices(api, session_id) if text.startswith("Please watch")]
    # Only the unsure check's line: never one saying the clip was checked.
    assert told == [
        "Please watch scene 2's clip: I couldn't check whether it's right, as your photos "
        "never show its base."
    ]


def test_a_detail_not_shown_on_a_failed_clip_adds_no_owner_notice(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted("check_clip_references", lambda _: DETAIL_NOT_SHOWN)
    fake_model.answer_unscripted(
        "check_clip_product",
        lambda _: {**CLIP_PRODUCT_OK, "verdict": "fail", "fault": "A second handle at 2 s."},
    )

    clip = clip_of(fake_model, steps, say, 2)

    judged = ruling(clip)
    assert judged["verdict"] == "fail"
    assert judged["detail_not_shown"] == DETAIL
    assert not AgentFix.objects.exists()
    assert not [text for _, text in notices(api, session_id) if DETAIL in text]


def test_a_product_check_unsure_keeps_the_clip_for_the_owner(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    # Said as the end of a sentence, with its own full stop.
    fake_model.answer_unscripted(
        "check_clip_product",
        lambda _: {**CLIP_PRODUCT_OK, "verdict": "unsure", "references_never_show": "its base."},
    )

    clip = clip_of(fake_model, steps, say, 2)

    judged = ruling(clip)
    assert judged["verdict"] == "unsure"
    assert judged["checks"]["product"] == "unsure"
    assert set(judged["checks"]) == {"product"}
    assert not AgentFix.objects.exists()
    assert (
        "Please watch scene 2's clip: I couldn't check whether it's right, as your photos "
        "never show its base."
    ) in [text for _, text in notices(api, session_id)]


def test_a_product_check_fail_is_a_failed_clip_with_its_fault(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fault = "The press has a second handle on its back at 2.1 s; the real one has one."
    fake_model.answer_unscripted(
        "check_clip_product",
        lambda _: {**CLIP_PRODUCT_OK, "verdict": "fail", "fault": fault},
    )

    clip = clip_of(fake_model, steps, say, 2)

    judged = ruling(clip)
    assert judged["verdict"] == "fail"
    assert judged["checks"]["product"] == "fail"
    assert judged["failures"][0]["check"] == "product"
    assert judge_faults(judged) == [fault]
    assert not AgentFix.objects.exists()


def test_a_refused_description_frame_does_not_make_a_product_pass_unsure(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
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

    # The description is answered without the refused frame; only PRODUCT settles the clip.
    first, *_, then = labels_shown("check_clip_motion")
    assert [label for label in first if label not in then] == ["Frame at 3 s"]
    judged = ruling(clip)
    assert judged["verdict"] == "pass"
    assert set(judged["checks"]) == {"product"}
    assert judged["checks"]["product"] == "pass"
    assert judged["refused_frames"] == [{"check": "motion", "ask": 1, "seconds": [3.0]}]
    assert not AgentFix.objects.exists()
    # A description refusal is recorded for the developer; PRODUCT passed every frame.
    ((level, said),) = [
        (level, text) for level, text in recorded(api, session_id) if "safety filter" in text
    ]
    assert level == "problem" and "scene 2's clip at 3 s" in said
    assert "kept for the shop owner to decide" not in said
    chat_said = [text for _, text in notices(api, session_id)]
    assert said not in chat_said
    assert not any("Please watch" in text for text in chat_said)


def test_a_refused_second_of_a_clip_the_judge_fails_isnt_said_to_be_kept(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted(
        "check_clip_product",
        lambda _: {**CLIP_PRODUCT_OK, "verdict": "fail", "fault": "A second handle at 2 s."},
    )
    fake_model.answer_unscripted(
        "check_clip_motion",
        refusing(lambda label: label.endswith("Frame at 3 s"), motion_answer(CLIP_MOTION_OK)),
    )

    clip = clip_of(fake_model, steps, say, 2)

    # Failed: the refused second is recorded, never said to
    # be kept, and the owner isn't asked to check the clip.
    assert ruling(clip)["verdict"] == "fail"
    [refused] = [text for _, text in recorded(api, session_id) if "safety filter" in text]
    assert "scene 2's clip at 3 s" in refused
    assert "kept" not in refused
    assert not [text for _, text in notices(api, session_id) if "Please watch" in text]


def test_product_unsure_still_asks_the_owner_even_with_old_motion_failures(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted(
        "check_clip_product",
        lambda _: {**CLIP_PRODUCT_OK, "verdict": "unsure", "references_never_show": "its base"},
    )
    fails = {"verdict": "fail", "problem": "The press floats.", "back_to_normal_at": "never"}
    fake_model.answer_unscripted(
        "check_clip_motion", motion_answer({**CLIP_MOTION_OK, "real_motion": fails})
    )

    clip = clip_of(fake_model, steps, say, 2)

    assert ruling(clip)["verdict"] == "unsure"
    assert any("Please watch" in text for _, text in notices(api, session_id))


def test_refused_description_frames_are_found_and_recorded_without_changing_the_verdict(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    def refused(label: str) -> bool:
        return label.endswith(("Frame at 1 s", "Frame at 3 s")) and not label.startswith("Video")

    fake_model.answer_unscripted(
        "check_clip_motion", refusing(refused, motion_answer(CLIP_MOTION_OK))
    )

    clip = clip_of(fake_model, steps, say, 2)

    asked = labels_shown("check_clip_motion")
    first, last = asked[0], asked[-1]
    # Answered once on every frame but the two refused, and never asked the whole call twice.
    assert [label for label in first if label not in last] == ["Frame at 1 s", "Frame at 3 s"]
    assert asked.count(first) == 1
    # The references were tried without (#218), then a capped few asks found the frames.
    references = 9 + 10
    assert len(asked) <= 1 + references + 1 + MOST_REFUSED_PROBES + 1
    # Each probe for the frames shows them beside the references, as the ask is made.
    without_references = next(
        n for n, labels in enumerate(asked) if all("Frame at" in label for label in labels)
    )
    shown_beside = [label for label in last if "Frame at" not in label]
    assert shown_beside
    for probe in asked[without_references + 1 :]:
        assert [label for label in probe if "Frame at" not in label] == shown_beside
    judged = ruling(clip)
    assert judged["verdict"] == "pass"
    assert judged["refused_frames"] == [{"check": "motion", "ask": 1, "seconds": [1.0, 3.0]}]
    [said] = [text for _, text in recorded(api, session_id) if "safety filter" in text]
    assert "scene 2's clip at 1 s, 3 s" in said
    assert "kept for the shop owner to decide" not in said
    assert not any("Please watch" in text for _, text in notices(api, session_id))


def test_a_clip_the_filter_refuses_every_frame_of_is_unsure_not_unchecked(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    def a_clip_frame(label: str) -> bool:
        return label.startswith(("Frames from the clip", "Frame at", "Frames at"))

    fake_model.answer_unscripted(
        "check_clip_product", refusing(a_clip_frame, lambda _: CLIP_PRODUCT_OK)
    )

    clip = clip_of(fake_model, steps, say, 2)

    judged = ruling(clip)
    assert judged["checks"]["product"] == "unsure"
    assert judged["verdict"] == "unsure"
    [blind] = judged["refused_frames"]
    assert blind["check"] == "product" and blind["seconds"][0] == 0.0
    assert len(blind["seconds"]) == 50


def test_a_cannot_tell_that_doesnt_say_what_is_never_shown_is_asked_again_once(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "check_clip_references", {**CANNOT_TELL, "references_never_show": None}, CANNOT_TELL
    )

    clip = clip_of(fake_model, steps, say, 2)

    assert ModelCall.objects.filter(purpose="check_clip_references").count() == 2
    assert ruling(clip)["cannot_tell"] == NEVER_SHOWN
    assert any(NEVER_SHOWN in text for _, text in notices(api, session_id))


def test_a_cannot_tell_broken_twice_stays_unsure_with_a_developer_notice(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    broken = {**CANNOT_TELL, "references_never_show": " "}
    fake_model.respond("check_clip_references", broken, broken)

    clip = clip_of(fake_model, steps, say, 2)

    assert ruling(clip)["verdict"] == "unsure"
    assert ruling(clip)["answers"]["references"] is None
    assert any(
        level == "problem" and "couldn't ask whether scene 2's references" in text
        for level, text in recorded(api, session_id)
    )


def test_an_unsure_or_a_fail_that_names_nothing_still_says_something(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted(
        "check_clip_product",
        lambda _: {**CLIP_PRODUCT_OK, "verdict": "unsure", "references_never_show": None},
    )

    clip_of(fake_model, steps, say, 2)

    assert "Please watch scene 2's clip: I couldn't check whether it's right." in [
        text for _, text in notices(api, session_id)
    ]
    # A PRODUCT fail naming no fault is said by what the clip shows, else a fixed sentence.
    failing = {**CLIP_PRODUCT_OK, "verdict": "fail", "fault": None}
    assert still_failing(judged([{**failing, "clip_shows": "Two handles at 2 s."}], [None])) == [
        "Two handles at 2 s."
    ]
    assert still_failing(judged([failing], [None])) == [clip_judge.NO_FAULT_NAMED]


def test_a_sent_picture_the_filter_refuses_is_left_out_of_each_check_and_said_once(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    def image_2(label: str) -> bool:
        return label.startswith("Image 2.")

    fake_model.answer_unscripted(
        "check_clip_references", refusing(image_2, lambda _: REFERENCES_ENOUGH)
    )
    fake_model.answer_unscripted("check_clip_product", refusing(image_2, lambda _: CLIP_PRODUCT_OK))
    fake_model.answer_unscripted(
        "check_clip_motion", refusing(image_2, motion_answer(CLIP_MOTION_OK))
    )

    clip = clip_of(fake_model, steps, say, 2)

    # Each check is answered without that picture, the clip's frames all shown.
    for purpose in ("check_clip_product", "check_clip_motion"):
        first, *_, then = labels_shown(purpose)
        assert [label for label in first if label not in then] == [first[1]]
        assert first[1].startswith("Image 2.")
    # The first question is never asked without a reference: it asks whether they show
    # enough, so it goes unasked and the checks judge alone.
    assert len(labels_shown("check_clip_references")) == 1
    judged = ruling(clip)
    assert judged["answers"]["references"] is None
    assert judged["verdict"] == "pass"
    named = clip_judge.named(Image(label=first[1], key=""))
    assert judged["not_shown"] == [
        {"check": "product", "ask": 1, "pictures": [named]},
        {"check": "motion", "ask": 1, "pictures": [named]},
    ]
    # Recorded for the developer, never shown in the chat (spec #272).
    assert not [text for _, text in notices(api, session_id) if "safety filter" in text]
    said = [(level, text) for level, text in recorded(api, session_id) if "safety filter" in text]
    assert said == [
        (
            "problem",
            f"The AI model's safety filter refused to look at {named}, so scene 2's "
            "clip was judged without it.",
        )
    ]
