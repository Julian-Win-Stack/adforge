"""One fresh take of a line's audio when it was heard saying something other than the line
(round 7 unlisted, item 3), driven through the chat with the producer's model faked at the
gateway and the steps held until a test runs them."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel, turn
from jobs.models import ProducedItem

from .conftest import HeldSteps, results_of
from .test_producer_line_audio import LINE, calling, made, spoken  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

MISHEARD = "Meet the Stoneware Mug from Kill and Co."


def transcribed(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], heard: str
) -> None:
    """Scene 1's audio transcribed, heard as `heard`."""
    fake_model.respond("transcribe_line", {"text": heard})
    calling(fake_model, "transcribe_line_audio")
    say("Transcribe scene 1's audio")
    fake_model.respond("produce", turn(says="Transcribed."))
    steps.run_held()


def test_audio_heard_as_another_line_gets_one_fresh_take(
    fake_model: FakeModel,
    spoken: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    transcribed(fake_model, steps, say, MISHEARD)
    calling(fake_model, "make_line_audio")

    say("Record scene 1 again")

    assert "fresh take" in results_of("make_line_audio")[-1]
    fake_model.respond("produce", turn(says="The new take is ready."))
    steps.run_held()
    assert made("line_audio") == [(1, 1), (1, 2)]
    # The new take is the line's audio now: the transcript is of it.
    transcribed(fake_model, steps, say, MISHEARD)
    newest = ProducedItem.objects.filter(kind="transcript").order_by("version").last()
    assert newest is not None and newest.made_from is not None
    assert newest.made_from.version == 2

    # Only one: a line heard wrong again keeps its takes.
    calling(fake_model, "make_line_audio")
    say("Record scene 1 again")

    assert made("line_audio") == [(1, 1), (1, 2)]
    assert "already had its one fresh take" in results_of("make_line_audio")[-1]


def test_audio_heard_as_its_line_isnt_made_again(
    fake_model: FakeModel,
    spoken: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    # Heard with other spacing, case and punctuation, as a transcriber writes it.
    transcribed(fake_model, steps, say, LINE.upper().replace("Kiln", "Kil n").rstrip("."))
    calling(fake_model, "make_line_audio")

    say("Record scene 1 again")

    assert made("line_audio") == [(1, 1)]
    assert "already made for this line" in results_of("make_line_audio")[-1]
