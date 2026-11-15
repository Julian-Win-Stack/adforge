"""Failed scenes finish through one producer idea and a bounded talking fallback."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from gateway.types import Turn
from jobs.models import AgentFix, Job, Scene, SceneAgentRun

from .conftest import HeldSteps, recorded, results_of
from .scene_script import make_scene_clip, script_scene_agents
from .test_producer_auto_fix import failing
from .test_producer_broll import calling, checked  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM

pytestmark = pytest.mark.django_db(transaction=True)
RAW_REASON = "Please send real product-use footage so I can film this accurately."


def give_up() -> Turn:
    return turn(calls=[("give_up", {"reason": RAW_REASON})])


NOTE = (
    "pass_note",
    {
        "scenes": [
            {
                "scene": 2,
                "instruction": "Show the result with a simpler angle instead of the process.",
                "quotes": ["Go on"],
                "owner_asked": False,
            }
        ]
    },
)


def other_scenes(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    make_scene_clip(fake_model, steps, say, scene=1)
    make_scene_clip(fake_model, steps, say, scene=3)
    calling(fake_model, say, ("create_music", {"mood": "light upbeat"}))


def start_and_recover(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "start_scene_agent",
                    {"scene": 2, "instruction": "Film the planned use.", "quotes": ["Go on"]},
                )
            ]
        ),
        turn(says="Making the planned scene."),
        turn(calls=[NOTE]),
        turn(says="I am trying a simpler shot."),
        turn(calls=[("assemble_ad", {})]),
        turn(says="The ad is ready with the revised scene."),
    )
    say("Go on")
    steps.run_held()


def test_one_producer_idea_then_keeps_the_failed_clip_and_assembles_without_questions(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    other_scenes(fake_model, steps, say)
    fake_model.answer_unscripted("check_clip_product", lambda _: failing("Its handle changes."))
    for _ in range(2):
        fake_model.respond(
            "scene_agent",
            turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
            turn(calls=[("film", FILM)]),
            give_up(),
        )
    start_and_recover(fake_model, steps, say)

    assert Job.objects.get().produced.filter(kind="finished_ad").exists()
    assert Scene.objects.get(number=2).status == "finished"
    runs = list(SceneAgentRun.objects.filter(agent_fix__step__scene__number=2))
    assert len(runs) == 2
    assert sum(any("producer_recovery" in quote for quote in run.quotes) for run in runs) == 1
    assert AgentFix.objects.filter(step__scene__number=2, kept__isnull=False).count() == 2
    assert not any("?" in text for _, text in recorded(api, session_id))
    assert RAW_REASON not in str(api.get(f"/api/sessions/{session_id}/messages/").json())


@pytest.mark.parametrize("talking_succeeds", [True, False], ids=["talking-clip", "remove-scene"])
def test_no_clip_after_both_runs_becomes_talking_then_finishes_or_leaves_the_ad(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    talking_succeeds: bool,
) -> None:
    other_scenes(fake_model, steps, say)
    fake_model.respond("scene_agent", give_up(), give_up())
    if talking_succeeds:
        script_scene_agents(fake_model)
    else:
        fake_model.respond("scene_agent", give_up())
    start_and_recover(fake_model, steps, say)

    assert Job.objects.get().produced.filter(kind="finished_ad").exists()
    assert Scene.objects.count() == (3 if talking_succeeds else 2)
    if talking_succeeds:
        scene = Scene.objects.get(number=2)
        assert scene.kind == "talking" and scene.status == "finished"
        assert scene.line == (
            "Hand-thrown, holds 350 ml, and dishwasher safe too. At home, every day."
        )
    assert not any("?" in text for _, text in recorded(api, session_id))
    assert RAW_REASON not in str(api.get(f"/api/sessions/{session_id}/messages/").json())
    assert len(results_of("pass_note")) == 1


def test_a_second_producer_idea_for_the_same_owner_message_is_refused(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    # Start a first run, then ask for the producer's one idea twice in the same turn.
    fake_model.respond("scene_agent", give_up())
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Film it.", "quotes": ["Go on"]}),
    )
    fake_model.respond("produce", turn(calls=[NOTE]), turn(calls=[NOTE]), turn(says="Finishing."))
    steps.run_next()
    first, second = results_of("pass_note")
    assert "opens a new run" in first
    assert "already tried" in second
    assert len(steps.held) == 1
