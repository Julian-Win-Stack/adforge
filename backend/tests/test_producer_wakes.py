"""Only a failed scene or the last running scene wakes the producer."""

from collections.abc import Callable

import pytest

from adforge.retry import OutsideServiceDown
from gateway.fake import FakeModel, meanwhile, turn
from jobs.models import SceneStep

from .conftest import HeldSteps, given_to_the_producer, producer_turns
from .scene_script import script_scene_agents

pytestmark = pytest.mark.django_db(transaction=True)


def step_results(number: int) -> list[str]:
    return [
        each["text"] for each in given_to_the_producer(number) if each["kind"] == "step_finished"
    ]


@pytest.fixture
def two_scenes_running(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> int:
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "start_scene_agent",
                    {
                        "scene": n,
                        "instruction": "Film the planned action.",
                        "quotes": ["Make both scenes"],
                    },
                )
                for n in (1, 2)
            ]
        ),
        turn(says="I've started both scenes."),
    )
    say("Make both scenes")
    assert SceneStep.objects.filter(kind="scene_agent", status="running").count() == 2
    return producer_turns()


def test_a_step_finishing_while_another_runs_doesnt_wake_the_producer(
    fake_model: FakeModel, two_scenes_running: int, steps: HeldSteps
) -> None:
    script_scene_agents(fake_model)
    steps.run_next()
    assert SceneStep.objects.get(kind="scene_agent", scene__number=1).status == "finished"
    assert producer_turns() == two_scenes_running


def test_the_last_running_step_finishing_wakes_the_producer_with_every_result(
    fake_model: FakeModel, two_scenes_running: int, steps: HeldSteps
) -> None:
    script_scene_agents(fake_model, scenes=2)
    fake_model.respond("produce", turn(says="Both scenes are ready."))
    steps.run_held()
    assert producer_turns() == two_scenes_running + 1
    told = step_results(producer_turns())
    assert len(told) == 2
    assert all("agent passed" in result for result in told)


def test_a_step_failing_while_another_runs_wakes_the_producer(
    fake_model: FakeModel, two_scenes_running: int, steps: HeldSteps
) -> None:
    fake_model.respond("scene_agent", *[OutsideServiceDown("503 from scene model")] * 3)
    fake_model.respond("produce", turn(says="One scene couldn't be made yet."))
    steps.run_next()
    assert SceneStep.objects.get(kind="scene_agent", scene__number=2).status == "running"
    assert producer_turns() == two_scenes_running + 1
    assert step_results(producer_turns())[-1].startswith("Background step failed")


def test_a_working_producer_takes_no_turn_more_for_a_step_that_finished_while_another_runs(
    fake_model: FakeModel, two_scenes_running: int, steps: HeldSteps, say: Callable[..., None]
) -> None:
    script_scene_agents(fake_model, scenes=2)
    fake_model.respond("produce", meanwhile(steps.run_next, turn(says="It's on its way.")))
    say("How is it going?")
    assert producer_turns() == two_scenes_running + 1
    assert SceneStep.objects.get(kind="scene_agent", scene__number=2).status == "running"
    fake_model.respond("produce", turn(says="Both scenes are ready."))
    steps.run_held()
    assert producer_turns() == two_scenes_running + 2
    assert len(step_results(producer_turns())) == 2
