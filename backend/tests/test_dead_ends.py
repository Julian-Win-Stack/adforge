"""The round 6 dead ends (decisions/dead-ends-root-cause.md): each left an ad that nothing in
the app could finish. Each test runs the way it happened and checks the ad gets out."""

import dataclasses
import io
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import PIL.Image
import PIL.ImageDraw
import pytest
from django.utils import timezone
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from agents import fixing_agent, run_plan, tasks
from agents.models import ToolCall
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import work
from jobs.models import (
    AgentFix,
    AgentFixTry,
    Job,
    ProducedItem,
    ProductPhoto,
    ProductVideo,
    Scene,
    SceneStep,
)
from jobs.picture_check import PictureKeptFailing
from jobs.planning import producer_decision_for
from jobs.product_videos import save_videos

from .conftest import MUG_FRONT, PLAN, HeldSteps, WorkerStopped, a_plan_with
from .test_fixing_agent import film_again
from .test_producer_auto_fix import FLOATS, clip_of_scene_2, failing, judged
from .test_producer_broll import (
    calling,
    checked,  # noqa: F401 (a fixture)
    made_ready,
    run,
)
from .test_producer_scenes import step_messages

pytestmark = pytest.mark.django_db(transaction=True)


def striped() -> bytes:
    """A photo unlike either of the page's, which are each one colour."""
    image = PIL.Image.new("RGB", (800, 600), (236, 229, 206))
    draw = PIL.ImageDraw.Draw(image)
    for left in range(0, 800, 100):
        draw.rectangle((left, 0, left + 49, 599), fill=(60, 40, 120))
    file = io.BytesIO()
    image.save(file, format="PNG")
    return file.getvalue()


MUG_BACK = striped()

CHOICE: dict[str, Any] = {
    "photo": 1,
    "photo_reason": "The side view shows the glaze best.",
    "prompt": "She holds the cream mug from the left up beside her face, in her workshop.",
    "prompt_reason": "Holding it up by her face introduces the mug as the line does.",
}


def last_result(tool: str) -> str:
    return ToolCall.objects.filter(tool=tool).latest("id").result


# --- A scene is finished only when its clip step is ---------------------------------------


@pytest.mark.parametrize(
    "stop",
    ["worker_restart", "step_error"],
    ids=["killed mid-judge then the worker restarts", "an error after the clip is kept"],
)
def test_a_clip_step_that_stops_after_its_first_clip_leaves_its_scene_to_make_again(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    stop: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calling(fake_model, say, ("create_music", {"mood": "light upbeat lo-fi"}))
    made_ready(fake_model, steps, say, (1, 2, 3))
    calling(fake_model, say, ("make_clip", {"scene": 1}), ("make_clip", {"scene": 3}))
    run(fake_model, steps)

    # Scene 2 shows the mug: its clip is made and kept, then the judge runs.
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    if stop == "worker_restart":
        fake_model.respond("check_clip_sides", WorkerStopped())
        fake_model.respond("check_clip_motion", WorkerStopped())
        with pytest.raises(WorkerStopped):
            steps.run_next()
        # Kept, but the step still runs, so the scene isn't finished.
        assert ProducedItem.objects.filter(kind="clip", scene__number=2).exists()
        assert Scene.objects.get(number=2).status == Scene.Status.PLANNED
        fake_model.respond("produce", turn(says="The clip stopped."))
        tasks.carry_on_after_the_worker_starts()
    else:

        def broken(*_: object, **__: object) -> None:
            raise RuntimeError("the fix check broke after the clip was kept")

        monkeypatch.setattr(tasks, "check_fix", broken)
        fake_model.respond("produce", turn(says="The clip failed."))
        steps.run_next()
        monkeypatch.undo()

    assert SceneStep.objects.filter(kind="clip", scene__number=2).latest("id").status == "failed"
    assert Scene.objects.get(number=2).status == Scene.Status.PLANNED

    # Assembly says plainly scene 2 isn't finished, and its clip can be made again.
    calling(fake_model, say, ("assemble_ad", {}))
    assert "scene 2 isn't finished" in last_result("assemble_ad")
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    assert last_result("make_clip").startswith("Started scene 2's clip")
    run(fake_model, steps)
    assert Scene.objects.get(number=2).status == Scene.Status.FINISHED
    calling(fake_model, say, ("assemble_ad", {}))
    assert ProducedItem.objects.filter(kind="finished_ad").exists(), last_result("assemble_ad")


# --- The worker-start cleanup survives a try killed mid-judge ------------------------------


def killed_mid_judge_of_try_1(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> SceneStep:
    """Scene 2's clip failed by the judge, the fixing agent films try 1, and the worker is
    killed while the judge asks about try 1: no handler of the app runs."""
    fake_model.respond("check_clip_motion", *judged(failing(FLOATS), failing(FLOATS)))
    fake_model.respond("check_clip_motion", WorkerStopped())
    fake_model.respond("fix_scene", turn(calls=[film_again()]))
    with monkeypatch.context() as kill:
        kill.setattr(fixing_agent, "_failed", lambda *_a, **_k: None)
        kill.setattr(AgentFixTry, "delete", lambda *_a, **_k: None)
        kill.setattr(fixing_agent, "_put_back", lambda *_a, **_k: None)
        with pytest.raises(WorkerStopped):
            clip_of_scene_2(fake_model, steps, say)
    return SceneStep.objects.filter(kind="clip", scene__number=2).order_by("id")[0]


def test_a_try_killed_mid_judge_is_cleaned_up_as_the_worker_starts(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = killed_mid_judge_of_try_1(fake_model, steps, say, monkeypatch)
    try_clip = ProducedItem.objects.get(kind="clip", scene__number=2, remake=1)
    # What the kill leaves: the try judged only as far as its pictures.
    assert try_clip.ruling is not None and set(try_clip.ruling) == {"pictures"}

    # The try isn't taken for an old auto-fix remake, so reading the scene doesn't crash.
    work.versions(Scene.objects.get(number=2))
    fake_model.respond("produce", turn(says="Scene 2 stopped."))
    tasks.carry_on_after_the_worker_starts()

    step.refresh_from_db()
    assert step.status == SceneStep.Status.FAILED
    assert AgentFix.objects.get().finished_at is not None


def test_a_step_whose_fixing_cant_be_ended_is_still_failed_with_a_red_notice(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made_ready(fake_model, steps, say, (1, 3))
    calling(fake_model, say, ("make_clip", {"scene": 1}), ("make_clip", {"scene": 3}))
    steps.held.clear()  # The worker stopped before running either.
    first, second = SceneStep.objects.filter(kind="clip", status="running").order_by("id")
    ending = work.end_the_agents_fixing

    def broken_for_the_first(step: SceneStep) -> None:
        if step.pk == first.pk:
            raise KeyError("something unforeseen")
        ending(step)

    monkeypatch.setattr(tasks, "end_the_agents_fixing", broken_for_the_first)
    fake_model.respond("produce", turn(says="Scene 3 stopped."))
    tasks.carry_on_after_the_worker_starts()

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == SceneStep.Status.FAILED
    assert second.status == SceneStep.Status.FAILED
    assert any(
        warning["level"] == "problem"
        and warning["text"].startswith("Scene 1's clip stopped partway, and the fixing agent")
        for warning in Job.objects.get().warnings
    )


def test_one_step_whose_end_cant_carry_on_leaves_the_others_ended(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made_ready(fake_model, steps, say, (1, 3))
    calling(fake_model, say, ("make_clip", {"scene": 1}), ("make_clip", {"scene": 3}))
    steps.held.clear()
    first, second = SceneStep.objects.filter(kind="clip", status="running").order_by("id")
    carrying_on = run_plan.carry_on_scene

    def broken_for_the_first(scene: Scene) -> None:
        if scene.pk == first.scene_id:
            raise KeyError("something unforeseen")
        carrying_on(scene)

    monkeypatch.setattr(run_plan, "carry_on_scene", broken_for_the_first)
    fake_model.respond("produce", turn(says="Both stopped."))
    tasks.carry_on_after_the_worker_starts()

    assert SceneStep.objects.get(pk=first.pk).status == SceneStep.Status.FAILED
    assert SceneStep.objects.get(pk=second.pk).status == SceneStep.Status.FAILED


# --- A step whose worker was killed is failed once its heartbeat stops ---------------------


def started_clip_of_scene_2(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> SceneStep:
    """Scene 2's clip step, started and waiting for a worker."""
    made_ready(fake_model, steps, say, (2,))
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    (step_id,) = steps.held
    steps.held.clear()
    return SceneStep.objects.get(pk=step_id)


def test_a_step_whose_worker_was_killed_is_failed_and_its_scene_made_again(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    step = started_clip_of_scene_2(fake_model, steps, say)
    # A worker took it, then was killed (out of memory): its heartbeat stopped 6 minutes ago.
    long_ago = timezone.now() - timedelta(minutes=6)
    SceneStep.objects.filter(pk=step.pk).update(taken_at=long_ago, seen_at=long_ago)

    fake_model.respond("produce", turn(says="Scene 2's clip stopped."))
    tasks.fail_dead_steps()

    step.refresh_from_db()
    assert step.status == SceneStep.Status.FAILED
    assert step.result.startswith("Background step failed: scene 2's clip couldn't be made")
    # The producer was told, and the scene's clip can be made again.
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    assert last_result("make_clip").startswith("Started scene 2's clip")


def test_a_live_or_waiting_step_is_never_failed_as_dead(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    step = started_clip_of_scene_2(fake_model, steps, say)
    long_ago = timezone.now() - timedelta(minutes=30)
    # Waiting for a worker for long, as behind other work: no worker took it, so its
    # heartbeat says nothing yet.
    SceneStep.objects.filter(pk=step.pk).update(seen_at=long_ago)
    tasks.fail_dead_steps()
    assert SceneStep.objects.get(pk=step.pk).status == SceneStep.Status.RUNNING

    # Working for long, as on a slow paid render, its heartbeat still beating.
    SceneStep.objects.filter(pk=step.pk).update(taken_at=long_ago, seen_at=timezone.now())
    tasks.fail_dead_steps()
    assert SceneStep.objects.get(pk=step.pk).status == SceneStep.Status.RUNNING


def test_a_step_failed_as_dead_that_finishes_after_all_stays_failed(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made_ready(fake_model, steps, say, (1,))
    calling(fake_model, say, ("make_clip", {"scene": 1}))
    (step_id,) = steps.held
    making = tasks.STEP_WORK[SceneStep.Kind.CLIP].make

    def silent_for_long(step: SceneStep) -> ProducedItem | None:
        # Its heartbeat stopped for longer than the limit while it worked, so it was failed.
        made = making(step)
        long_ago = timezone.now() - timedelta(minutes=6)
        SceneStep.objects.filter(pk=step.pk).update(seen_at=long_ago)
        fake_model.respond("produce", turn(says="Scene 1's clip stopped."))
        tasks.fail_dead_steps()
        return made

    monkeypatch.setitem(
        tasks.STEP_WORK,
        SceneStep.Kind.CLIP,
        dataclasses.replace(tasks.STEP_WORK[SceneStep.Kind.CLIP], make=silent_for_long),
    )
    steps.run_next()

    step = SceneStep.objects.get(pk=step_id)
    assert step.status == SceneStep.Status.FAILED
    assert Scene.objects.get(number=1).status == Scene.Status.PLANNED


def test_a_step_failed_as_dead_whose_work_then_fails_says_so_once(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made_ready(fake_model, steps, say, (1,))
    calling(fake_model, say, ("make_clip", {"scene": 1}))
    (step_id,) = steps.held

    def failed_as_dead_then_raises(step: SceneStep) -> ProducedItem | None:
        SceneStep.objects.filter(pk=step.pk).update(seen_at=timezone.now() - timedelta(minutes=6))
        fake_model.respond("produce", turn(says="Scene 1's clip stopped."))
        tasks.fail_dead_steps()
        raise RuntimeError("the video service broke too")

    monkeypatch.setitem(
        tasks.STEP_WORK,
        SceneStep.Kind.CLIP,
        dataclasses.replace(tasks.STEP_WORK[SceneStep.Kind.CLIP], make=failed_as_dead_then_raises),
    )
    steps.run_next()

    step = SceneStep.objects.get(pk=step_id)
    assert step.status == SceneStep.Status.FAILED
    assert step.reason == "Its work stopped partway, and it was never heard from again."
    not_made = [
        text for _, _, text, _ in step_messages(api, session_id) if "couldn't be made" in text
    ]
    assert not_made == ["Scene 1's clip couldn't be made: it was stopped partway."]


@pytest.mark.parametrize(
    "stopped",
    [PictureKeptFailing(1, [("picture.png", ["wrong"])]), work.AgentGaveUp("No try worked.")],
    ids=["pictures kept failing", "fixing agent gave up"],
)
def test_a_step_failed_as_dead_that_stops_the_run_or_gives_up_stays_failed(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
    stopped: Exception,
) -> None:
    made_ready(fake_model, steps, say, (1,))
    calling(fake_model, say, ("make_clip", {"scene": 1}))
    (step_id,) = steps.held

    def failed_as_dead_then_stops(step: SceneStep) -> ProducedItem | None:
        SceneStep.objects.filter(pk=step.pk).update(seen_at=timezone.now() - timedelta(minutes=6))
        fake_model.respond("produce", turn(says="Scene 1's clip stopped."))
        tasks.fail_dead_steps()
        raise stopped

    monkeypatch.setitem(
        tasks.STEP_WORK,
        SceneStep.Kind.CLIP,
        dataclasses.replace(tasks.STEP_WORK[SceneStep.Kind.CLIP], make=failed_as_dead_then_stops),
    )
    steps.run_next()

    step = SceneStep.objects.get(pk=step_id)
    assert step.status == SceneStep.Status.FAILED
    assert step.reason == "Its work stopped partway, and it was never heard from again."


# --- A page read again only adds photos -----------------------------------------------------


def test_a_page_read_again_after_the_plan_keeps_every_photo_and_its_colour_mark(
    checked: None,  # noqa: F811
) -> None:
    job = Job.objects.get()
    before = list(job.photos.order_by("position").values_list("pk", "position", "source_url"))
    marked = list(job.photos.filter(shows_product_colour=True).values_list("pk", flat=True))
    assert marked

    # A second read of the same page, as a slow read and a producer started again did.
    work.save_photos(job, [url for _pk, _position, url in before], faces=frozenset())

    after = list(job.photos.order_by("position").values_list("pk", "position", "source_url"))
    assert after == before
    assert list(job.photos.filter(shows_product_colour=True).values_list("pk", flat=True)) == (
        marked
    )


def test_a_page_read_again_adds_only_the_photos_the_job_doesnt_have(
    checked: None,  # noqa: F811
    httpserver: HTTPServer,
) -> None:
    job = Job.objects.get()
    kept = list(job.photos.order_by("position").values_list("source_url", flat=True))
    # The page now also has a copy of its front photo under another name, and a new photo.
    httpserver.expect_request("/cdn/mug-front-copy.png").respond_with_data(
        MUG_FRONT, content_type="image/png"
    )
    httpserver.expect_request("/cdn/mug-back.png").respond_with_data(
        MUG_BACK, content_type="image/png"
    )
    copy, new = (
        httpserver.url_for("/cdn/mug-front-copy.png"),
        httpserver.url_for("/cdn/mug-back.png"),
    )

    work.save_photos(job, [new, *kept, copy], merge_copies=True, faces=frozenset())

    assert list(job.photos.order_by("position").values_list("source_url", flat=True)) == [
        *kept,
        new,
    ]


# --- The ad's colour is advice, never a gate ------------------------------------------------


def test_a_plan_may_name_no_photo_in_the_ads_colour() -> None:
    plan = producer_decision_for(2).model_validate(a_plan_with(colour_photos=[]))
    assert plan.plan is not None and plan.plan.colour_photos == []


def test_a_video_with_no_link_is_kept_once_when_the_page_is_read_again(
    checked: None,  # noqa: F811
) -> None:
    job = Job.objects.get()
    player = {"src": "", "poster": "", "kind": "iframe"}
    ProductVideo.objects.create(job=job, position=1, source_url="", kind="iframe")

    assert save_videos(job, [player, {**player, "poster": "https://example.com/p.jpg"}]) == []
    assert job.videos.count() == 1


def test_with_more_photos_than_a_model_takes_the_colour_photos_are_still_shown(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    job = Job.objects.get()
    # A page with as many photos as a job keeps, plus video pictures, and the one photo in
    # the ad's colour last.
    job.photos.update(shows_product_colour=False)
    front = job.photos.get(position=1)
    for position in range(3, 56):
        ProductPhoto.objects.create(
            job=job,
            position=position,
            file=front.file,
            source_url=f"https://example.com/{position}.png",
            shows_product_colour=position == 55,
        )
    fake_model.respond(
        "produce",
        turn(calls=[("make_starting_picture", {"scene": 1, "note": None})]),
        turn(says="On it."),
    )
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", {**CHOICE, "photo": 55})
    fake_model.respond("produce", turn(says="It's ready."))
    steps.run_held()

    step = SceneStep.objects.get(kind="starting_picture")
    assert step.status == SceneStep.Status.FINISHED, step.reason
    asked = ModelCall.objects.get(purpose="choose_starting_picture")
    assert len(asked.images) == 50
    assert {"label": "Photo 55", "key": front.file} in asked.images


def test_a_starting_picture_is_made_with_no_photo_marked_in_the_ads_colour(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    job = Job.objects.get()
    # Every photo shows the product in a pack or beside its other colours, so none is marked.
    job.photos.update(shows_product_colour=False)
    choice: dict[str, Any] = {**CHOICE, "photo": 2}
    fake_model.respond(
        "produce",
        turn(calls=[("make_starting_picture", {"scene": 1, "note": None})]),
        turn(says="On it."),
    )
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", choice)
    fake_model.respond("produce", turn(says="It's ready."))
    steps.run_held()

    step = SceneStep.objects.get(kind="starting_picture")
    assert step.status == SceneStep.Status.FINISHED, step.reason
    assert step.photo is not None and step.photo.position == 2
    # The writer is shown every product photo, told none is marked in the colour.
    asked = ModelCall.objects.get(purpose="choose_starting_picture")
    assert asked.handoff["colour_photos"] == []
    assert asked.handoff["product_colour"] == PLAN["plan"]["product_colour"]
    assert [image["label"] for image in asked.images] == ["The portrait", "Photo 1", "Photo 2"]
