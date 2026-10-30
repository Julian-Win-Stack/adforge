"""When a scene step's end wakes the producer (round 7 unlisted, item 13): only when a step
fails, or when the last step still running finishes, so it never takes a turn just to hear
one of several steps is done. Driven through the chat, with the producer's model faked at the
gateway and the steps held until a test runs them."""

from collections.abc import Callable

import pytest

from adforge.retry import OutsideServiceDown
from gateway.fake import FakeModel, meanwhile, turn
from jobs.models import SceneStep

from .conftest import HeldSteps, given_to_the_producer, producer_turns
from .test_producer_line_audio import CHOICE

pytestmark = pytest.mark.django_db(transaction=True)


def step_results(number: int) -> list[str]:
    """The step results the producer was given on its `number`th turn."""
    return [
        each["text"] for each in given_to_the_producer(number) if each["kind"] == "step_finished"
    ]


@pytest.fixture
def picture_and_audio_running(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> int:
    """Scene 1's starting picture and its audio both started, neither finished. Gives how
    many turns the producer took so far."""
    fake_model.respond(
        "produce",
        turn(
            calls=[
                ("make_starting_picture", {"scene": 1, "note": None}),
                ("make_line_audio", {"scene": 1}),
            ]
        ),
        turn(says="I've started scene 1's picture and audio."),
    )
    say("Make scene 1")
    assert SceneStep.objects.filter(status="running").count() == 2
    return producer_turns()


def test_a_step_finishing_while_another_runs_doesnt_wake_the_producer(
    fake_model: FakeModel, picture_and_audio_running: int, steps: HeldSteps
) -> None:
    fake_model.respond("choose_starting_picture", CHOICE)

    steps.run_next()

    assert SceneStep.objects.get(kind="starting_picture").status == "finished"
    assert producer_turns() == picture_and_audio_running


def test_the_last_running_step_finishing_wakes_the_producer_with_every_result(
    fake_model: FakeModel, picture_and_audio_running: int, steps: HeldSteps
) -> None:
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Scene 1's picture and audio are ready."))

    steps.run_held()

    assert producer_turns() == picture_and_audio_running + 1
    told = step_results(producer_turns())
    assert [text.split(":")[0] for text in told[-2:]] == ["Background step finished"] * 2
    assert "starting picture" in told[-2] and "audio is ready" in told[-1]


def test_a_step_failing_while_another_runs_wakes_the_producer(
    fake_model: FakeModel, picture_and_audio_running: int, steps: HeldSteps
) -> None:
    fake_model.respond("speak_line", *[OutsideServiceDown("503 from the voice service")] * 3)
    fake_model.respond("produce", turn(says="Scene 1's audio couldn't be made yet."))

    # The audio first: the picture is still running when it fails.
    steps.held.reverse()
    steps.run_next()

    assert SceneStep.objects.get(kind="starting_picture").status == "running"
    assert producer_turns() == picture_and_audio_running + 1
    assert step_results(producer_turns())[-1].startswith("Background step failed")


def test_a_working_producer_takes_no_turn_more_for_a_step_that_finished_while_another_runs(
    fake_model: FakeModel,
    picture_and_audio_running: int,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond(
        "produce",
        # The picture is made while the producer answers the shop owner; the audio runs on.
        meanwhile(steps.run_next, turn(says="It's on its way.")),
    )

    say("How is it going?")

    assert producer_turns() == picture_and_audio_running + 1
    assert SceneStep.objects.get(kind="line_audio").status == "running"

    fake_model.respond("produce", turn(says="Scene 1's picture and audio are ready."))
    steps.run_held()

    assert producer_turns() == picture_and_audio_running + 2
    told = step_results(producer_turns())
    assert "starting picture" in told[-2] and "audio is ready" in told[-1]
