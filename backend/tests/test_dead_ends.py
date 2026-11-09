"""The round 6 dead ends (decisions/dead-ends-root-cause.md): each left an ad that nothing in
the app could finish. Historical clip checkpoints remain explicit fixtures; current
retries use the scene agent. Each test checks the ad gets out."""

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

from agents import tasks
from agents.models import ToolCall
from chat.models import Message
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import UnusableReply
from jobs import notices, work
from jobs.library import PagePhoto, PictureLabel, add_to_library
from jobs.models import (
    AgentFix,
    Job,
    ProducedItem,
    ProductPhoto,
    ProductVideo,
    Scene,
    SceneStep,
)
from jobs.notices import post_notice
from jobs.planning import producer_decision_for
from jobs.product_videos import save_videos

from .conftest import (
    MUG_FRONT,
    PLAN,
    REFERENCES_ENOUGH,
    HeldSteps,
    ScriptedProducer,
    a_plan_with,
    recorded,
    scripted_turns,
)
from .legacy_records import historical_snapshot, seed_legacy_clip, seed_legacy_fix
from .scene_script import script_scene_agents
from .test_producer_auto_fix import legacy_clip_of_scene_2 as clip_of_scene_2
from .test_producer_broll import (
    calling,
    checked,  # noqa: F401 (a fixture)
    made_ready,
    run,
)
from .test_producer_scenes import step_messages

pytestmark = [pytest.mark.django_db(transaction=True)]


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


def last_result(tool: str) -> str:
    return ToolCall.objects.filter(tool=tool).latest("id").result


def recorded_clip_waiting_for_worker(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> SceneStep:
    """An old clip was saved before its worker stopped, but has not been judged yet."""
    step = clip_of_scene_2(fake_model, steps, say, prepared=True, run_step=False)
    seed_legacy_clip(step)
    # The old producer made Scribe words before it queued filming. Restore that authentic
    # stored transcript so a free keep also keeps captions without asking Scribe again.
    transcript = SceneStep.objects.create(
        scene=step.scene,
        tool_call=step.tool_call,
        kind=SceneStep.Kind.TRANSCRIPT,
        status=SceneStep.Status.FINISHED,
        **historical_snapshot(step.scene),
    )
    ProducedItem.objects.create(
        job=step.scene.job,
        scene=step.scene,
        step=transcript,
        kind=ProducedItem.Kind.TRANSCRIPT,
        version=1,
        made_from=step.made_from,
        text=step.line,
        words=[
            {"text": word, "start": n * 0.4, "end": (n + 1) * 0.4}
            for n, word in enumerate(step.line.split())
        ],
    )
    return step


# --- A scene is finished only when its clip step is ---------------------------------------


@pytest.mark.parametrize(
    "stop",
    ["worker_restart", "retired_dispatch"],
    ids=["old worker restarts after keeping its clip", "old queued step is retired"],
)
def test_a_clip_step_that_stops_after_its_first_clip_leaves_its_scene_to_make_again(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    stop: str,
) -> None:
    calling(fake_model, say, ("create_music", {"mood": "light upbeat lo-fi"}))
    made_ready(fake_model, steps, say, (1, 2, 3))
    script_scene_agents(fake_model, scenes=2)
    calling(
        fake_model,
        say,
        *[
            (
                "start_scene_agent",
                {"scene": n, "instruction": "Film the planned action.", "quotes": ["Go on"]},
            )
            for n in (1, 3)
        ],
    )
    run(fake_model, steps)

    # The stored old clip remains readable; its retired worker never runs the old judge.
    old = recorded_clip_waiting_for_worker(fake_model, steps, say)
    if stop == "worker_restart":
        steps.held.remove(old.pk)
        SceneStep.objects.filter(pk=old.pk).update(taken_at=timezone.now())
        fake_model.respond("produce", turn(says="The clip stopped."))
        tasks.carry_on_after_the_worker_starts()
    else:
        fake_model.respond("produce", turn(says="The old step was retired."))
        steps.run_next()
    assert not ModelCall.objects.filter(
        tool_call=old.tool_call, purpose__startswith="check_clip"
    ).exists()

    failed = SceneStep.objects.filter(kind="clip", scene__number=2).latest("id")
    assert failed.status == "failed"
    assert Scene.objects.get(number=2).status == Scene.Status.PLANNED
    # The producer is told its ways on, as a tool's can't (spec #247 point 8).
    assert (
        "Its clip (version 1) was made before the step ended, but scene 2 isn't finished with "
        "it: keep it with keep_try for scene 2, try 0, or make a new one with start_scene_agent."
    ) in failed.result

    # Assembly says plainly scene 2 isn't finished, and its clip can be made again.
    calling(fake_model, say, ("assemble_ad", {}))
    assert "scene 2 isn't finished" in last_result("assemble_ad")
    script_scene_agents(fake_model)
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 2,
                "instruction": "Make the scene after its old step stopped.",
                "quotes": ["Go on"],
            },
        ),
    )
    assert last_result("start_scene_agent").startswith("Started scene 2's agent")
    run(fake_model, steps)
    assert Scene.objects.get(number=2).status == Scene.Status.FINISHED
    calling(fake_model, say, ("assemble_ad", {}))
    assert ProducedItem.objects.filter(kind="finished_ad").exists(), last_result("assemble_ad")


def test_a_clip_kept_before_its_step_ended_can_be_kept_for_nothing(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    calling(fake_model, say, ("create_music", {"mood": "light upbeat lo-fi"}))
    made_ready(fake_model, steps, say, (1, 2, 3))
    script_scene_agents(fake_model, scenes=2)
    calling(
        fake_model,
        say,
        *[
            (
                "start_scene_agent",
                {"scene": n, "instruction": "Film the planned action.", "quotes": ["Go on"]},
            )
            for n in (1, 3)
        ],
    )
    run(fake_model, steps)
    recorded_clip_waiting_for_worker(fake_model, steps, say)

    fake_model.respond("produce", turn(says="The old step was retired."))
    steps.run_next()
    kept = ProducedItem.objects.get(kind="clip", scene__number=2)
    paid = ModelCall.objects.count()

    calling(fake_model, say, ("keep_try", {"scene": 2, "try_number": 0}))

    assert last_result("keep_try") == (
        f"Scene 2's clip (version {kept.version}), made before its step ended, is the scene's "
        "clip now: nothing was made or paid for, and scene 2 is finished."
    )
    assert Scene.objects.get(number=2).status == Scene.Status.FINISHED
    # Only the agents' own turns were paid.
    assert set(ModelCall.objects.order_by("id")[paid:].values_list("purpose", flat=True)) <= {
        "produce",
    }
    calling(fake_model, say, ("assemble_ad", {}))
    (ad,) = ProducedItem.objects.filter(kind="finished_ad")
    assert [cut["clip"] for cut in ad.cuts if cut["scene"] == 2] == [kept.pk]
    assert ad.captions


# --- The worker-start cleanup survives a try killed mid-judge ------------------------------


def killed_mid_judge_of_try_1(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
) -> SceneStep:
    """Read an old unfinished try whose worker stopped while its clip was judged."""
    step = clip_of_scene_2(fake_model, steps, say)
    fixing = seed_legacy_fix(step, tries=1, finished=False)
    tried = fixing.tries.get()
    assert tried.clip is not None
    ProducedItem.objects.filter(pk=tried.clip.pk).update(ruling={"pictures": {}})
    SceneStep.objects.filter(pk=step.pk).update(status=SceneStep.Status.RUNNING, finished_at=None)
    return step


def test_a_try_killed_mid_judge_is_cleaned_up_as_the_worker_starts(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    step = killed_mid_judge_of_try_1(fake_model, steps, say)
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
    first, second = [
        clip_of_scene_2(fake_model, steps, say, prepared=True, run_step=False, scene_number=n)
        for n in (1, 3)
    ]
    steps.held.clear()  # The old worker stopped before running either recorded checkpoint.
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


# --- A step whose worker was killed is failed once its heartbeat stops ---------------------


def started_scene_agent_of_scene_2(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], *, prepared: bool = False
) -> SceneStep:
    """Scene 2's current agent step, queued through the producer and waiting for a worker."""
    if not prepared:
        made_ready(fake_model, steps, say, (2,))
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["Go on"]}),
    )
    (step_id,) = steps.held
    steps.held.clear()
    return SceneStep.objects.get(pk=step_id)


def test_a_step_whose_worker_was_killed_is_failed_and_its_scene_made_again(
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    step = started_scene_agent_of_scene_2(fake_model, steps, say)
    # A worker took it, then was killed (out of memory): its heartbeat stopped 6 minutes ago.
    long_ago = timezone.now() - timedelta(minutes=6)
    SceneStep.objects.filter(pk=step.pk).update(taken_at=long_ago, seen_at=long_ago)

    fake_model.respond("produce", turn(says="Scene 2's clip stopped."))
    tasks.fail_dead_steps()

    step.refresh_from_db()
    assert step.status == SceneStep.Status.FAILED
    assert step.result == (
        "Background step failed: scene 2's scene agent couldn't be made: "
        "Its work stopped partway, and "
        "it was never heard from again. Start it again with start_scene_agent for scene 2, "
        "or skip it: "
        "remove scene 2 with remove_scene, and the ad is made without it. Take a way on; tell "
        "the shop owner only if they can help."
    )
    # Recorded in red, the scene's card told with no words (spec #272), and the run goes on:
    # the producer took its turn on it.
    assert ("problem", "Scene 2's clip couldn't be made: it was stopped partway.") in recorded(
        api, session_id
    )
    assert ("", {"scene": 2, "kind": "clip", "state": "not_made"}) in [
        (text, step) for _, _, text, step in step_messages(api, session_id)
    ]
    assert ToolCall.objects.filter(tool="start_scene_agent").count() == 1
    # The producer was told, and the scene's clip can be made again.
    script_scene_agents(fake_model)
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 2,
                "instruction": "Make the scene after its old step stopped.",
                "quotes": ["Go on"],
            },
        ),
    )
    assert last_result("start_scene_agent").startswith("Started scene 2's agent")


def test_a_live_or_waiting_step_is_never_failed_as_dead(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    step = started_scene_agent_of_scene_2(fake_model, steps, say)
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
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 1, "instruction": "Film the planned action.", "quotes": ["Go on"]},
        ),
    )
    (step_id,) = steps.held
    fake_model.respond(
        "scene_agent", turn(calls=[("give_up", {"reason": "No useful picture could be made."})])
    )
    making = tasks.STEP_WORK[SceneStep.Kind.SCENE_AGENT].make

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
        SceneStep.Kind.SCENE_AGENT,
        dataclasses.replace(tasks.STEP_WORK[SceneStep.Kind.SCENE_AGENT], make=silent_for_long),
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
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 1, "instruction": "Film the planned action.", "quotes": ["Go on"]},
        ),
    )
    (step_id,) = steps.held

    def failed_as_dead_then_raises(step: SceneStep) -> ProducedItem | None:
        SceneStep.objects.filter(pk=step.pk).update(seen_at=timezone.now() - timedelta(minutes=6))
        fake_model.respond("produce", turn(says="Scene 1's clip stopped."))
        tasks.fail_dead_steps()
        raise RuntimeError("the video service broke too")

    monkeypatch.setitem(
        tasks.STEP_WORK,
        SceneStep.Kind.SCENE_AGENT,
        dataclasses.replace(
            tasks.STEP_WORK[SceneStep.Kind.SCENE_AGENT], make=failed_as_dead_then_raises
        ),
    )
    steps.run_next()

    step = SceneStep.objects.get(pk=step_id)
    assert step.status == SceneStep.Status.FAILED
    assert step.reason == "Its work stopped partway, and it was never heard from again."
    # The chat's scene card is told once, and why is recorded once (spec #272).
    ended = [
        step for _, _, _, step in step_messages(api, session_id) if step["state"] == "not_made"
    ]
    assert ended == [{"scene": 1, "kind": "clip", "state": "not_made"}]
    not_made = [text for _, text in recorded(api, session_id) if "couldn't be made" in text]
    assert not_made == ["Scene 1's clip couldn't be made: it was stopped partway."]


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

    # The picker put the copy in the front photo's copy group.
    work.save_photos(
        job,
        [new, *kept, copy],
        copies={new: (new,), **{url: (url,) for url in kept}, copy: (kept[0], copy)},
        faces=frozenset(),
    )

    assert list(job.photos.order_by("position").values_list("source_url", flat=True)) == [
        *kept,
        new,
    ]


# --- The ad's colour is advice, never a gate ------------------------------------------------


def test_a_plan_may_name_no_photo_in_the_ads_colour() -> None:
    plan = producer_decision_for(library=(1, 2), moments={}).model_validate(
        a_plan_with(colour_photos=[])
    )
    assert plan.plan is not None and plan.plan.colour_photos == []


def test_a_video_with_no_link_is_kept_once_when_the_page_is_read_again(
    checked: None,  # noqa: F811
) -> None:
    job = Job.objects.get()
    player = {"src": "", "poster": "", "kind": "iframe"}
    ProductVideo.objects.create(job=job, position=1, source_url="", kind="iframe")

    assert (
        save_videos(
            job,
            [player, {**player, "poster": "https://example.com/p.jpg"}],
            page_url=job.product_url,
        )
        == []
    )
    assert job.videos.count() == 1


def test_a_colour_photo_beyond_the_old_models_photo_limit_can_be_picked_explicitly(
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
    for position in range(3, 56):
        add_to_library(
            job,
            front.file,
            PictureLabel(
                kind="side", side="front", alone="yes", colour="green", label=f"View {position}."
            ),
            PagePhoto(photo=position),
        )
    draw_picture_from(fake_model, steps, say, 55)
    made = ProducedItem.objects.get(kind="starting_picture", scene__number=1)
    assert made.sent is not None
    assert made.sent["photos"][0]["library_number"] == 55
    asked = ModelCall.objects.get(purpose="draw_scene_picture")
    assert asked.handoff["pictures"][0] == front.file
    # The explicit product pick, page views filling the picture model's room, then her
    # portrait and the room alone (a talking scene); the views past it are left out.
    assert len(asked.handoff["pictures"]) == 16
    views = [p for p in made.sent["photos"] if p.get("page_view")]
    assert len(views) == 13 and made.sent["photos"][-1].get("library_number") is None


def test_a_starting_picture_is_made_with_no_photo_marked_in_the_ads_colour(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    job = Job.objects.get()
    # Every photo shows the product in a pack or beside its other colours, so none is marked.
    job.photos.update(shows_product_colour=False)
    draw_picture_from(fake_model, steps, say, 2)
    made = ProducedItem.objects.get(kind="starting_picture", scene__number=1)
    assert made.sent is not None
    assert made.sent["photos"][0]["library_number"] == 2
    assert made.sent["photos"][0]["file"] == job.photos.get(position=2).file
    assert (
        ModelCall.objects.get(purpose="check_scene_picture").handoff["product_colour"]
        == "sage-green"
    )


def draw_picture_from(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], number: int
) -> None:
    """The scene agent picks a library reference, draws, and leaves its checked picture."""
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    fake_model.respond("check_scene_picture", PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(
            calls=[
                (
                    "draw_starting_picture",
                    {
                        **PICTURE_FORM,
                        "picks": [{"item": number, "take": "Copy this view of the glaze."}],
                    },
                )
            ]
        ),
        turn(calls=[("give_up", {"reason": "The picture is ready for a later filming run."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 1,
                "instruction": "Draw the mug from its selected reference.",
                "quotes": ["Go on"],
            },
        ),
    )
    run(fake_model, steps)


# --- Whole-build check C (spec #247 Testing Decisions, ticket #266): each dead end of the Oct
# 21 audit, driven by the scripted producer, is a tool answer with the reason, never a stop. --


def red_in_the_chat(api: APIClient, session_id: str) -> list[str]:
    return [
        message["text"]
        for message in api.get(f"/api/sessions/{session_id}/messages/").json()
        if message["level"] == "problem"
    ]


@pytest.fixture
def clipped_but_2(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    """Every scene ready, its music made, and scenes 1 and 3 clipped; scene 2, which shows
    the mug, has no clip yet."""
    calling(fake_model, say, ("create_music", {"mood": "light upbeat lo-fi"}))
    made_ready(fake_model, steps, say, (1, 2, 3))
    script_scene_agents(fake_model, scenes=2)
    calling(
        fake_model,
        say,
        *[
            (
                "start_scene_agent",
                {"scene": n, "instruction": "Film the planned action.", "quotes": ["Go on"]},
            )
            for n in (1, 3)
        ],
    )
    run(fake_model, steps)


def test_dead_end_a_clip_from_a_failed_step_is_kept_or_made_again_on_the_producers_say(
    api: APIClient,
    say: Callable[..., None],
    producer: ScriptedProducer,
    fake_model: FakeModel,
    clipped_but_2: None,
    session_id: str,
) -> None:
    recorded_clip_waiting_for_worker(fake_model, producer.steps, say)

    # Retiring the queued old step leaves its clip available to the producer's free keep.
    producer.woken(("keep_try", {"scene": 2, "try_number": 0}), "Scene 2 keeps its clip.")

    # The step failed, its answer giving the ways on, recorded in red; kept now, it is done.
    failed = SceneStep.objects.filter(kind="clip", scene__number=2).latest("id")
    assert failed.result.startswith("Background step failed: scene 2's clip")
    assert "keep it with keep_try for scene 2, try 0, or make a new one with start_scene_agent" in (
        failed.result
    )
    assert failed.status == SceneStep.Status.FINISHED
    assert ("problem", "Scene 2's clip couldn't be made: the video model couldn't make it.") in (
        recorded(api, session_id)
    )
    kept = ProducedItem.objects.get(kind="clip", scene__number=2)
    assert last_result("keep_try").startswith(f"Scene 2's clip (version {kept.version})")
    assert Scene.objects.get(number=2).status == Scene.Status.FINISHED

    # The ad is made with it.
    producer.told("Put it together", ("assemble_ad", {}), "Here's your ad.")
    (ad,) = ProducedItem.objects.filter(kind="finished_ad")
    assert [cut["clip"] for cut in ad.cuts if cut["scene"] == 2] == [kept.pk]
    assert ad.captions


def test_dead_end_a_step_with_no_heartbeat_is_failed_and_the_producer_starts_it_again(
    api: APIClient,
    say: Callable[..., None],
    producer: ScriptedProducer,
    fake_model: FakeModel,
    clipped_but_2: None,
    session_id: str,
) -> None:
    step_id = started_scene_agent_of_scene_2(fake_model, producer.steps, say, prepared=True).pk
    # A worker took it and was killed: its heartbeat stopped 6 minutes ago.
    producer.steps.held.clear()
    long_ago = timezone.now() - timedelta(minutes=6)
    SceneStep.objects.filter(pk=step_id).update(taken_at=long_ago, seen_at=long_ago)

    script_scene_agents(fake_model)
    fake_model.respond(
        "produce",
        *scripted_turns(
            (
                "start_scene_agent",
                {
                    "scene": 2,
                    "instruction": "Make the scene after its old step stopped.",
                    "quotes": ["Go on"],
                },
            ),
            "Scene 2's clip stopped; I started it again.",
        ),
    )
    tasks.fail_dead_steps()
    assert fake_model.left("produce") == 0

    dead = SceneStep.objects.get(pk=step_id)
    assert dead.status == SceneStep.Status.FAILED
    assert "Start it again with start_scene_agent for scene 2, or skip it" in dead.result
    assert ("problem", "Scene 2's clip couldn't be made: it was stopped partway.") in recorded(
        api, session_id
    )
    assert last_result("start_scene_agent").startswith("Started scene 2's agent")

    # Started again, it finishes, and the ad is made.
    producer.steps_run()
    assert Scene.objects.get(number=2).status == Scene.Status.FINISHED
    producer.told("Put it together", ("assemble_ad", {}), "Here's your ad.")
    assert ProducedItem.objects.filter(kind="finished_ad").exists(), last_result("assemble_ad")


# The last line keeps saying the brand.
ON_SALE = "Yours today for just $19.00 from Kiln & Co, on sale."


def test_dead_end_a_note_with_one_impossible_part_has_the_rest_done_and_that_part_named(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    httpserver: HTTPServer,
) -> None:
    other_product = httpserver.url_for("/products/teapot")
    httpserver.expect_request("/products/teapot").respond_with_data(
        f'<html><head><link rel="canonical" href="{other_product}"></head>'
        "<body><h1>Glass Teapot</h1></body></html>",
        content_type="text/html",
    )
    checked_before = ModelCall.objects.filter(purpose="fact_check").count()
    producer.told(
        f"Add my teapot too ({other_product}), and have scene 3 say it's on sale for $19.",
        [
            ("read_page", {"link": other_product, "target_seconds": None}),
            ("edit_scene", {"scene": 3, "line": ON_SALE}),
        ],
        "Scene 3 now says it's on sale. The teapot needs an ad of its own: start a new ad for it.",
    )

    # The impossible part is a can't, naming why; the rest of the note is done.
    refused = last_result("read_page")
    assert refused.startswith("Refused:") and "start a new ad for that" in refused
    assert ToolCall.objects.filter(tool="read_page").latest("id").answer == "cant"
    assert Scene.objects.get(number=3).line == ON_SALE
    edited = ToolCall.objects.get(tool="edit_scene")
    assert edited.answer == "made"
    # The new line isn't checked by the edit: it waits for the producer's planning checks
    # (#254: the checks run again on changed lines; code never reruns on its own).
    assert "The planning checks haven't checked scene 3 against the page" in edited.result
    assert not Scene.objects.get(number=3).fact_checked
    assert ModelCall.objects.filter(purpose="fact_check").count() == checked_before


NO_PICTURE_9 = "There's no picture 9 in the library."


def test_dead_end_a_paid_answer_the_validator_rejects_goes_back_to_its_model_once_with_why(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    page_read: str,
) -> None:
    names_no_photo = {**PLAN, "plan": {**PLAN["plan"], "colour_photos": [9]}}
    fake_model.respond("plan_ad", names_no_photo, PLAN)

    producer.told("Plan it", ("plan_ad", {}), "Here's the plan.")

    # The planner was asked again, told the rule its paid answer broke; its second answer is
    # the plan, and the producer's one call made it.
    asks = [asked for asked in fake_model.asked if asked.purpose == "plan_ad"]
    assert len(asks) == 2
    first, again = asks
    assert NO_PICTURE_9 not in first.instructions
    assert NO_PICTURE_9 in again.instructions
    # A rule of the whole plan is told as it is, not as pydantic's "Value error".
    assert "Value error" not in again.instructions
    # The record keeps what each ask was told.
    told = [
        call.system_prompt.instructions
        for call in ModelCall.objects.filter(purpose="plan_ad").order_by("id")
        if call.system_prompt is not None
    ]
    assert [NO_PICTURE_9 in instructions for instructions in told] == [False, True]
    planned = ToolCall.objects.get(tool="plan_ad")
    assert planned.answer == "made", planned.result
    assert Job.objects.get().scenes.count() == 3


def test_dead_end_a_paid_plan_the_validator_rejects_three_times_is_a_failed_answer_said_in_red(
    api: APIClient,
    session_id: str,
    producer: ScriptedProducer,
    fake_model: FakeModel,
    page_read: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    names_no_photo = {**PLAN, "plan": {**PLAN["plan"], "colour_photos": [9]}}
    fake_model.respond("plan_ad", names_no_photo, names_no_photo, names_no_photo, PLAN)
    red_before = len(red_in_the_chat(api, session_id))
    recorded_before = len(recorded(api, session_id))

    producer.told("Plan it", ("plan_ad", {}), ("plan_ad", {}), "Here's the plan.")

    # Asked again twice with why, the planner broke the rule each time: the tool answers the
    # can't with the rule, it is recorded in red for the developer (spec #272), and the
    # producer's next call runs.
    failed, planned = ToolCall.objects.filter(tool="plan_ad").order_by("id")
    assert failed.answer == "cant"
    assert failed.result.startswith("Failed: a model's answer couldn't be used")
    assert NO_PICTURE_9 in failed.result
    # Said in plain words, never as the answer's validation error, with its warning line.
    assert red_in_the_chat(api, session_id)[red_before:] == []
    reds = [
        text for level, text in recorded(api, session_id)[recorded_before:] if level == "problem"
    ]
    # The warning names the rule the answer broke, so a developer reading it knows why.
    assert reds == [
        "An AI model's answer couldn't be used, even after it was told why and asked again, "
        "so part of making your ad didn't work this time. The producer was told and carries on. "
        f"The plan_ad answer broke: {NO_PICTURE_9.rstrip('.')}."
    ]
    assert reds[0] in [warning["text"] for warning in Job.objects.get().warnings]
    # The server log has the rule it broke.
    assert any(
        reds[0] in record.message and NO_PICTURE_9 in record.message
        for record in caplog.records
        if record.levelname == "WARNING"
    )
    assert planned.answer == "made", planned.result
    assert Job.objects.get().scenes.count() == 3


def test_dead_end_the_planner_has_three_tries_each_told_the_rule_its_last_plan_broke(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    page_read: str,
) -> None:
    def names_no_photo(number: int) -> dict[str, Any]:
        return {**PLAN, "plan": {**PLAN["plan"], "colour_photos": [number]}}

    fake_model.respond("plan_ad", names_no_photo(9), names_no_photo(10), PLAN)

    producer.told("Plan it", ("plan_ad", {}), "Here's the plan.")

    # Its third plan is used, and the producer's one call made it.
    asks = [asked.instructions for asked in fake_model.asked if asked.purpose == "plan_ad"]
    no_picture_10 = "There's no picture 10 in the library."
    assert [(NO_PICTURE_9 in told, no_picture_10 in told) for told in asks] == [
        (False, False),
        (True, False),
        (False, True),
    ]
    planned = ToolCall.objects.get(tool="plan_ad")
    assert planned.answer == "made", planned.result
    assert Job.objects.get().scenes.count() == 3


@pytest.mark.parametrize(
    ("second", "warned"),
    [
        pytest.param(9, [9], id="the same rule broken again is warned once"),
        pytest.param(10, [9, 10], id="a different rule broken is warned too"),
    ],
)
def test_dead_end_each_rule_a_paid_answer_breaks_twice_is_warned_once_naming_it(
    api: APIClient,
    session_id: str,
    producer: ScriptedProducer,
    fake_model: FakeModel,
    page_read: str,
    second: int,
    warned: list[int],
) -> None:
    def names_no_photo(number: int) -> dict[str, Any]:
        return {**PLAN, "plan": {**PLAN["plan"], "colour_photos": [number]}}

    first, again = names_no_photo(9), names_no_photo(second)
    fake_model.respond("plan_ad", first, first, first, again, again, again, PLAN)
    recorded_before = len(recorded(api, session_id))

    producer.told("Plan it", ("plan_ad", {}), ("plan_ad", {}), ("plan_ad", {}), "Here's the plan.")

    reds = [
        text for level, text in recorded(api, session_id)[recorded_before:] if level == "problem"
    ]
    assert reds == [
        "An AI model's answer couldn't be used, even after it was told why and asked again, "
        "so part of making your ad didn't work this time. The producer was told and carries on. "
        f"The plan_ad answer broke: There's no picture {number} in the library."
        for number in warned
    ]
    assert Job.objects.get().scenes.count() == 3


def test_a_paid_answer_the_model_refused_is_not_asked_again_nor_said_in_red(
    api: APIClient,
    session_id: str,
    producer: ScriptedProducer,
    fake_model: FakeModel,
    page_read: str,
) -> None:
    # A refusal broke no rule of the answer: telling the model a rule wouldn't help.
    refused = UnusableReply("the model refused", input_tokens=1_000, output_tokens=100)
    fake_model.respond("plan_ad", refused, PLAN)
    red_before = len(red_in_the_chat(api, session_id))

    producer.told("Plan it", ("plan_ad", {}), ("plan_ad", {}), "Here's the plan.")

    failed, planned = ToolCall.objects.filter(tool="plan_ad").order_by("id")
    assert failed.result == "Failed: a model's answer couldn't be used (the model refused)."
    assert red_in_the_chat(api, session_id)[red_before:] == []
    assert planned.answer == "made", planned.result


def test_a_scene_step_a_producer_tool_runs_at_once_never_asks_its_models_again(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made_ready(fake_model, steps, say, (2,))
    # The eager worker runs the scene agent inside the producer tool; its judge must keep
    # its own retry policy even though the producer call is running again.
    monkeypatch.setattr(tasks.run_scene_step, "delay", tasks.run_scene_step)
    fake_model.respond("check_clip_references", {**REFERENCES_ENOUGH, "verdict": "maybe"})
    # However often the finished step wakes the producer, it has nothing more to do.
    fake_model.answer_unscripted("produce", lambda _: turn(says="Scene 2's clip is made."))

    script_scene_agents(fake_model)
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 2,
                "instruction": "Show the planned action.",
                "quotes": ["Go on"],
            },
        ),
    )

    judged = [asked for asked in fake_model.asked if asked.purpose == "check_clip_references"]
    # Asked twice, by the judge's own ask-again, and never told the rule its answer broke.
    assert len(judged) == 2
    first, again = judged
    assert again.instructions == first.instructions


def test_a_paid_answer_rejected_twice_again_is_recorded_once_until_something_else_is(
    api: APIClient,
    session_id: str,
    producer: ScriptedProducer,
    fake_model: FakeModel,
    page_read: str,
) -> None:
    names_no_photo = {**PLAN, "plan": {**PLAN["plan"], "colour_photos": [9]}}
    fake_model.respond("plan_ad", *[names_no_photo] * 6)
    # Nothing has fallen back for this ad yet, as when the page was read with Firecrawl.
    Job.objects.update(warnings=[])

    def reds() -> list[str]:
        return [text for level, text in recorded(api, session_id) if level == "problem"]

    producer.told("Plan it", ("plan_ad", {}), ("plan_ad", {}), "The planner keeps failing.")

    # The same line twice in a row is recorded once, and never shown in the chat (spec #272).
    first = reds()
    assert len(first) == 1
    assert first[0] not in red_in_the_chat(api, session_id)
    # Once something else was recorded in red, it is recorded again.
    post_notice(
        Job.objects.get(), notices.STEP_NOT_MADE, "Something else failed.", Message.Level.PROBLEM
    )
    fake_model.respond("plan_ad", names_no_photo, names_no_photo, names_no_photo)
    producer.told("Try again", ("plan_ad", {}), "It failed again.")
    assert reds() == [first[0], "Something else failed.", first[0]]
