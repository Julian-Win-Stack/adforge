"""Historical producer work fails normally without buying another render."""

from collections.abc import Callable
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from agents.models import ToolCall
from agents.tasks import run_scene_step
from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs.models import Job, ProducedItem, SceneStep

from .conftest import HeldSteps, recorded

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "kind",
    [
        SceneStep.Kind.CLIP,
        SceneStep.Kind.STARTING_PICTURE,
        SceneStep.Kind.LINE_AUDIO,
        SceneStep.Kind.TRANSCRIPT,
    ],
)
def test_retired_producer_step_fails_without_another_payment(
    api: APIClient, session_id: str, fake_model: FakeModel, kind: str
) -> None:
    job = Job.objects.create(session_id=session_id)
    scene = job.scenes.create(number=1, line="See our mug.")
    call = ToolCall.objects.create(
        session_id=session_id,
        job=job,
        agent="producer",
        tool="make_clip",
        call_id="retired",
        arguments={},
        finished_at=timezone.now(),
    )
    audio = scene.produced.create(
        job=job,
        kind=ProducedItem.Kind.LINE_AUDIO,
        version=1,
        file="historical/line.wav",
        seconds=4.0,
    )
    picture = scene.produced.create(
        job=job,
        kind=ProducedItem.Kind.STARTING_PICTURE,
        version=1,
        file="historical/picture.png",
    )
    step = scene.steps.create(
        tool_call=call,
        kind=kind,
        status=SceneStep.Status.RUNNING,
        made_from=audio,
        picture=picture,
        **SceneStep.snapshot(scene),
    )
    paid = ModelCall.objects.create(
        job=job,
        tool_call=call,
        purpose="make_talking_clip",
        provider="heygen",
        model="heygen/avatar-iv",
        outcome=ModelCall.Outcome.SUCCEEDED,
        attempt=1,
        duration_ms=10,
        handoff={},
        output={"video_id": "video-1"},
        cost_usd=Decimal("0.50"),
    )
    # The real worker boundary must report a normal failed step, even though the old
    # provider's purpose no longer exists. Keep the producer parked during recovery.
    session = job.session
    assert session is not None
    session.producer_running = True
    session.producer_seen_at = timezone.now()
    session.save(update_fields=["producer_running", "producer_seen_at"])

    run_scene_step(step.pk)

    step.refresh_from_db()
    assert step.status == SceneStep.Status.FAILED
    assert "retired" in step.result
    label = {
        "clip": "clip",
        "starting_picture": "starting picture",
        "line_audio": "audio",
        "transcript": "transcript",
    }[kind]
    expected = (
        [("problem", f"Scene 1's {label} couldn't be made: the video model couldn't make it.")]
        if kind in {SceneStep.Kind.CLIP, SceneStep.Kind.STARTING_PICTURE}
        else []
    )
    assert recorded(api, session_id) == expected
    assert list(job.model_calls.values_list("pk", "cost_usd")) == [(paid.pk, Decimal("0.50"))]
    assert not fake_model.clips_submitted


@pytest.mark.parametrize("child_kind", [SceneStep.Kind.CLIP, SceneStep.Kind.TRANSCRIPT])
def test_h3_child_checkpoint_resumes_with_its_parent_after_worker_restart(
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
    child_kind: str,
) -> None:
    from agents import tasks
    from jobs.models import Scene

    from .conftest import WorkerStopped, paid_for
    from .scene_script import make_scene_clip

    fake_model.respond("transcribe_line", WorkerStopped())
    with pytest.raises(WorkerStopped):
        make_scene_clip(fake_model, steps, say, scene=1)
    parent = SceneStep.objects.get(kind=SceneStep.Kind.SCENE_AGENT)
    child = SceneStep.objects.get(kind=child_kind)
    assert parent.status == SceneStep.Status.RUNNING
    assert paid_for().count("make_broll_clip") == 1
    SceneStep.objects.filter(pk=child.pk).update(status=SceneStep.Status.RUNNING)

    # A stale Celery delivery cannot dispatch the checkpoint through retired work.
    tasks.run_scene_step(child.pk)
    child.refresh_from_db()
    assert child.status == SceneStep.Status.FINISHED
    assert "retired" not in child.result
    # The startup boundary must also recognize children left by an older worker.
    SceneStep.objects.filter(pk=child.pk).update(status=SceneStep.Status.RUNNING)
    fake_model.respond("transcribe_line", {"text": Scene.objects.get(number=1).line})
    tasks.carry_on_after_the_worker_starts()
    steps.run_held()

    parent.refresh_from_db()
    child.refresh_from_db()
    assert parent.status == SceneStep.Status.FINISHED
    assert child.status == SceneStep.Status.FINISHED
    assert Scene.objects.get(number=1).status == Scene.Status.FINISHED
    assert paid_for().count("make_broll_clip") == 1
    assert paid_for().count("transcribe_line") == 1
    assert ProducedItem.objects.filter(kind=ProducedItem.Kind.CLIP).count() == 1
