"""A spoken owner change is checked from the new clip's own transcript."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from gateway.types import ModelRequest, UnusableReply
from jobs import clip_judge

from .conftest import (
    CLIP_FIXED,
    CLIP_MOTION_OK,
    HeldSteps,
    facts_ok,
    handoffs,
    paid_for,
    recorded,
    results_of,
)
from .test_producer_broll import calling, checked  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_agent_changes import change
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
OLD_LINE = "This handmade mug costs ten dollars and holds warm tea every single day."
NEW_LINE = "This handmade mug holds warm tea for your cosy mornings every single day."


@pytest.mark.parametrize("has_transcript", [True, False])
def test_price_removed_from_speech_is_checked_without_waiting_or_an_extra_try(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    has_transcript: bool,
    api: APIClient,
    session_id: str,
) -> None:
    requests: list[ModelRequest[Any]] = []

    def motion(request: ModelRequest[Any]) -> dict[str, Any]:
        requests.append(request)
        if "owner_change" not in request.output.model_fields:
            return CLIP_MOTION_OK
        heard = request.handoff.model_dump().get("words_heard_in_the_new_clip")
        fixed = not has_transcript or heard == NEW_LINE
        return {
            **CLIP_MOTION_OK,
            "owner_change": {
                **CLIP_FIXED,
                "verdict": "fixed" if fixed else "not fixed",
                "still_wrong": None
                if fixed
                else "No heard words establish that the price was removed.",
            },
        }

    def note_during_render() -> dict[str, Any]:
        fake_model.respond(
            "produce",
            turn(
                calls=[
                    (
                        "pass_note",
                        {
                            "scenes": [
                                {
                                    "scene": 2,
                                    "instruction": "Leave the price out of the spoken line.",
                                    "quotes": ["leave the price out"],
                                    "owner_asked": True,
                                }
                            ]
                        },
                    )
                ]
            ),
            turn(says="I passed it on."),
        )
        say("leave the price out")
        return {"state": "working"}

    fake_model.answer_unscripted("check_clip_motion", motion)
    fake_model.respond("fact_check", facts_ok(2), facts_ok(2))
    fake_model.respond(
        "transcribe_line",
        {"text": OLD_LINE},
        {"text": NEW_LINE}
        if has_transcript
        else UnusableReply("No transcript", input_tokens=0, output_tokens=0),
    )
    fake_model.respond("collect_clip", note_during_render)
    fake_model.respond(
        "scene_agent",
        turn(calls=[change(line=OLD_LINE)]),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[change(line=NEW_LINE)]),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "The changed clip should pass."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 2,
                "instruction": "Show the pour.",
                "quotes": ["Go on"],
            },
        ),
    )
    fake_model.answer_unscripted("produce", lambda _: turn(says="The scene is ready."))
    steps.run_held()

    assert len(requests) == 2, results_of("film")
    assert "words_heard_in_the_new_clip" not in requests[0].handoff.model_dump()
    assert requests[0].instructions == clip_judge.check_words(
        clip_judge.MOTION_FROM_REFERENCES, scene=True
    )
    if has_transcript:
        assert requests[-1].handoff.model_dump().get("words_heard_in_the_new_clip") == NEW_LINE
        for wording in ("automatic transcript", "spoken", "frames", "small slips"):
            assert wording in requests[-1].instructions
    else:
        assert any("could not be transcribed" in text for _, text in recorded(api, session_id))
        assert "words_heard_in_the_new_clip" not in requests[-1].handoff.model_dump()
        assert requests[-1].instructions == (
            clip_judge.check_words(clip_judge.MOTION_FROM_REFERENCES, scene=True)
            + "\n\n"
            + clip_judge.SCENE_MOTION_FIX_QUESTION
        )
    assert "It passes." in results_of("film")[-1]
    assert paid_for().count("make_broll_clip") == 2
    assert paid_for().count("transcribe_line") == (2 if has_transcript else 1)
    assert "passed" in str(handoffs("produce")[-1])


@pytest.mark.parametrize("has_transcript", [True, False])
def test_retained_fix_question_uses_only_a_transcript_of_the_new_clip(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    has_transcript: bool,
) -> None:
    from jobs.models import ProducedItem

    from .legacy_records import legacy_clip_of_scene_2, seed_legacy_fix

    step = legacy_clip_of_scene_2(fake_model, steps, say)
    fixing = seed_legacy_fix(step, door="owner", goal="Leave the price out.")
    clip = fixing.tries.get().clip
    assert clip is not None and clip.step is not None
    if has_transcript:
        ProducedItem.objects.create(
            job=clip.job,
            scene=clip.scene,
            kind=ProducedItem.Kind.TRANSCRIPT,
            version=2,
            made_from=clip,
            text=NEW_LINE,
        )
    requests: list[ModelRequest[Any]] = []

    def motion(request: ModelRequest[Any]) -> dict[str, Any]:
        requests.append(request)
        return {**CLIP_MOTION_OK, "owner_change": CLIP_FIXED}

    fake_model.answer_unscripted("check_clip_motion", motion)
    result = clip_judge.judge_clip(clip.step, clip)
    [request] = requests
    assert result["compared_with"] == fixing.first_clip_id
    assert result["answers"]["motion"][0]["owner_change"]["verdict"] == "fixed"
    if has_transcript:
        assert request.handoff.model_dump()["words_heard_in_the_new_clip"] == NEW_LINE
        assert clip_judge.FIX_TRANSCRIPT_GUIDANCE in request.instructions
    else:
        assert "words_heard_in_the_new_clip" not in request.handoff.model_dump()
        assert request.instructions == (
            clip_judge.MOTION_FROM_REFERENCES + "\n\n" + clip_judge.MOTION_FIX_QUESTION
        )
