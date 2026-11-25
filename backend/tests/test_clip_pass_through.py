"""The clip judge catches pass-throughs in filmed clips, through the owner's chat."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from gateway.types import BlockedBySafetyFilter, ModelRequest, UnusableReply

from .conftest import HeldSteps, asked_with, handoffs, paid_for, recorded
from .test_producer_auto_fix import start_live_scene
from .test_producer_broll import checked, run  # noqa: F401
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM

pytestmark = pytest.mark.django_db(transaction=True)

CROSSES: dict[str, Any] = {
    "answer": "yes",
    "seconds": [0.4, 0.6, 0.8],
    "what": "The handle passes through the fingers holding it.",
}
CLEAR: dict[str, Any] = {
    "answer": "no",
    "seconds": [],
    "what": "The hand and handle stay on their own sides of each other.",
}


@pytest.mark.parametrize("references_available", [True, False])
def test_a_pass_through_fails_the_clip_and_tells_its_remake_the_fault(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
    references_available: bool,
) -> None:
    if not references_available:
        from .conftest import REFERENCES_ENOUGH
        from .test_clip_judge_references import CANNOT_TELL

        fake_model.respond("check_clip_references", CANNOT_TELL, REFERENCES_ENOUGH)
    fake_model.respond("check_clip_pass_through", CROSSES, CLEAR)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
    )
    start_live_scene(fake_model, steps, say)
    run(fake_model, steps)

    card = next(
        row for row in api.get(f"/api/sessions/{session_id}/scenes/").json() if row["scene"] == 2
    )
    assert [version["verdict"] for version in card["versions"]] == ["fail", "pass"]
    assert [version["in_ad"] for version in card["versions"]] == [False, True]
    told = str(handoffs("scene_agent")[2])
    assert CROSSES["what"] in told
    for second in ("0.4 s", "0.6 s", "0.8 s"):
        assert second in told
    assert paid_for().count("make_broll_clip") == 2


def test_a_clear_clip_passes_and_keeps_the_answer_on_the_ruling(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
    tmp_path: Path,
) -> None:
    from evals import grading_page, round_export

    requests = asked_with(fake_model, "check_clip_references")
    crossing: list[ModelRequest[Any]] = []

    def answer(request: ModelRequest[Any]) -> dict[str, Any]:
        crossing.append(request)
        return CLEAR

    fake_model.answer_unscripted("check_clip_pass_through", answer)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
    )
    start_live_scene(fake_model, steps, say)
    run(fake_model, steps)
    round_export.export_run({"mug": session_id}, tmp_path, "Pass-through check")
    clip = json.loads((tmp_path / "results.json").read_text())["mug:2"]["tries"][0]
    assert clip["ruling"]["verdict"] == "pass"
    assert clip["ruling"]["answers"]["pass_through"] == [CLEAR]
    assert clip["ruling"]["checks"]["pass_through"] == "pass"
    (asked,) = crossing
    # Shown the same frames and order as the references question, without the references.
    (references,) = requests
    assert asked.handoff.model_dump() == {"order": references.handoff.model_dump()["order"]}
    assert list(asked.images) == [image for image in references.images if "Frame" in image.label]
    assert asked.model == references.model
    page = grading_page.page(tmp_path)
    assert CLEAR["what"] in page


@pytest.mark.parametrize("reason", ["refused", "unusable", "service down"])
def test_an_unanswered_pass_through_check_leaves_the_clip_unsure_and_posts_a_notice(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
    reason: str,
) -> None:
    def cannot_answer(request: ModelRequest[Any]) -> dict[str, Any]:
        if reason == "refused":
            raise BlockedBySafetyFilter("Refused frames", input_tokens=0, output_tokens=0)
        if reason == "unusable":
            raise UnusableReply("No usable answer", input_tokens=0, output_tokens=0)
        raise RuntimeError("The service is down")

    fake_model.answer_unscripted("check_clip_pass_through", cannot_answer)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
    )
    start_live_scene(fake_model, steps, say)
    run(fake_model, steps)
    card = next(
        row for row in api.get(f"/api/sessions/{session_id}/scenes/").json() if row["scene"] == 2
    )
    assert card["versions"][0]["verdict"] == "unsure"
    notices = " ".join(text for _, text in recorded(api, session_id))
    assert ("refused" if reason == "refused" else "couldn't check") in notices


def test_a_refused_pass_through_frame_is_kept_unsure_with_its_seconds(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
    tmp_path: Path,
) -> None:
    from evals import round_export

    def answer(request: ModelRequest[Any]) -> dict[str, Any]:
        if any(image.label.endswith("Frame at 1 s") for image in request.images):
            raise BlockedBySafetyFilter("Refused this frame")
        return CLEAR

    fake_model.answer_unscripted("check_clip_pass_through", answer)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
    )
    start_live_scene(fake_model, steps, say)
    run(fake_model, steps)
    round_export.export_run({"mug": session_id}, tmp_path, "Refused frame")
    ruling = json.loads((tmp_path / "results.json").read_text())["mug:2"]["tries"][0]["ruling"]
    assert ruling["verdict"] == "unsure"
    assert ruling["answers"]["pass_through"] == [CLEAR]
    assert ruling["refused_frames"] == [{"check": "pass_through", "ask": 1, "seconds": [1.0]}]


def test_old_rulings_and_retired_move_records_still_load_and_export(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
    tmp_path: Path,
) -> None:
    from django.utils import timezone

    from evals import grading_page, round_export
    from gateway.models import ModelCall

    from .legacy_records import legacy_clip_of_scene_2
    from .test_producer_auto_fix import historical_ruling

    step = legacy_clip_of_scene_2(fake_model, steps, say)
    clip = step.produced.get(kind="clip")
    clip.ruling = historical_ruling()
    clip.save(update_fields=["ruling"])
    job = step.scene.job
    # Stored history uses string names, independent of today's catalog and notice Kinds.
    job.warnings = [
        {
            "kind": "move_unchecked",
            "level": "problem",
            "text": "The historical move check could not answer.",
            "at": timezone.now().isoformat(),
        }
    ]
    job.save(update_fields=["warnings"])
    ModelCall.objects.create(
        job=job,
        session=job.session,
        purpose="check_move",
        provider="azure",
        model="gpt-6.2-sol",
        attempt=1,
        handoff={"video_prompt": "The old planned move."},
        output={"blocked": False, "says": "Clear.", "moving_parts": "A handle."},
        outcome=ModelCall.Outcome.SUCCEEDED,
        cost_usd="0.1",
        duration_ms=10,
    )
    card = next(
        row for row in api.get(f"/api/sessions/{session_id}/scenes/").json() if row["scene"] == 2
    )
    assert card["versions"][0]["verdict"] == "pass"
    round_export.export_run({"mug": session_id}, tmp_path, "Historical records")
    ruling = json.loads((tmp_path / "results.json").read_text())["mug:2"]["tries"][0]["ruling"]
    assert "pass_through" not in ruling["answers"]
    assert "judge: pass" in grading_page.page(tmp_path)
    assert ("problem", "The historical move check could not answer.") in recorded(api, session_id)
    costs = json.loads((tmp_path / "costs.json").read_text())
    assert costs["ads"]["mug"]["total_usd"] >= 0.1
