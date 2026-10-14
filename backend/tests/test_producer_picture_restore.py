"""Going back to an earlier starting picture, driven through the chat (#143): when the shop
owner asks for a picture's note again after a newer picture was made, the earlier picture is
brought back as the scene's newest version, with nothing paid again (Julian 2026-10-11, M1 of
the #132 review: "same inputs, never pay twice"). The scene is un-finished, as for any new
picture (#133), and its clip is brought back too, or made if it never was. The producer's model
and the scene models are faked at the gateway, but the clips are real tiny videos and ffmpeg
runs for real."""

from collections.abc import Callable

import pytest
from django.db.models.signals import pre_save

from agents.models import ToolCall
from agents.tasks import restart_dead_producers
from chat.models import Attachment
from gateway.fake import FakeModel, turn
from jobs.models import ProducedItem

from .conftest import HeldSteps, paid_for, results_of, said_yes_to_a_fix
from .test_producer_broll import calling, checked, run  # noqa: F401 (a fixture)
from .test_producer_picture_then_clip import (
    HIGHER,
    ads,
    assemble,
    assembled,  # noqa: F401 (a fixture)
    clip,
    new_picture,
    picture,
    status,
)
from .test_producer_restarts import WorkerStopped, the_producer_died, the_worker_stops

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("checked")]

SLOWER = "Pour the tea slower."


def version(kind: str, number: int) -> ProducedItem:
    """Scene 2's `kind` at version `number`."""
    return ProducedItem.objects.get(kind=kind, scene__number=2, version=number)


def versions(kind: str) -> list[int]:
    return list(
        ProducedItem.objects.filter(kind=kind, scene__number=2)
        .order_by("version")
        .values_list("version", flat=True)
    )


def clip_of_scene_2(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    run(fake_model, steps)


def back_to(fake_model: FakeModel, say: Callable[..., None], note: str) -> str:
    """Ask for scene 2's starting picture with `note` again, once the shop owner said yes to
    it restated as the scene's fix, and give what the tool handed back."""
    said_yes_to_a_fix(2, note, "picture")
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": note}))
    return results_of("make_starting_picture")[-1]


@pytest.fixture
def higher_then_slower(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    assembled: ProducedItem,  # noqa: F811
) -> None:
    """Scene 2 got a picture for "hold it higher" and its clip, then a picture for "pour the
    tea slower" and its clip: pictures and clips 2 and 3."""
    new_picture(fake_model, steps, say, 2, HIGHER)
    clip_of_scene_2(fake_model, steps, say)
    new_picture(fake_model, steps, say, 2, SLOWER)
    clip_of_scene_2(fake_model, steps, say)
    assert (versions("starting_picture"), versions("clip")) == ([1, 2, 3], [1, 2, 3])
    assert status(2) == "finished"


def test_an_earlier_note_brings_back_its_picture_and_clip_without_paying_again(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], higher_then_slower: None
) -> None:
    paid = paid_for()
    earlier = version("starting_picture", 2)

    said = back_to(fake_model, say, HIGHER)

    assert paid_for() == paid
    assert steps.held == []
    back = picture(2)
    assert back.version == 4
    assert (back.file, back.step) == (earlier.file, earlier.step)
    assert said.startswith(
        "Scene 2's starting picture was already made for this line with this note (version "
        "2), so it is the scene's starting picture again, as version 4, and nothing was made "
        "or paid for again. Making it cost "
    )
    assert said.endswith("Scene 2 is no longer finished: make its clip from this picture.")
    assert status(2) == "planned"
    # The chat shows it again, so the scene's card shows the picture the ad now uses.
    assert Attachment.objects.filter(file=back.file, scene=2).count() == 2

    clip_of_scene_2(fake_model, steps, say)

    assert paid_for() == paid
    assert versions("clip") == [1, 2, 3, 4]
    assert (clip(2).file, clip(2).step) == (version("clip", 2).file, version("clip", 2).step)
    assert results_of("make_clip")[-1].startswith(
        "Scene 2's clip was already made from this starting picture and audio (version 2), so "
        "nothing was made or paid for again. It is the scene's clip again, as version 4."
    )
    assert status(2) == "finished"
    assert Attachment.objects.filter(file=clip(2).file, scene=2).count() == 2
    assemble(fake_model, say)
    scene_2 = next(cut for cut in ads()[-1].cuts if cut["scene"] == 2)
    assert scene_2["clip"] == clip(2).pk


def test_a_brought_back_clip_counts_as_its_fix_made_when_another_scenes_fix_lands(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], higher_then_slower: None
) -> None:
    back_to(fake_model, say, HIGHER)
    clip_of_scene_2(fake_model, steps, say)
    before = len(ads())

    new_picture(fake_model, steps, say, 1, "Smile more.")
    calling(fake_model, say, ("make_clip", {"scene": 1}))
    run(fake_model, steps)

    # Scene 2's fix was made by bringing its clip back, so the ad doesn't wait for it.
    assert len(ads()) == before + 1
    scene_2 = next(cut for cut in ads()[-1].cuts if cut["scene"] == 2)
    assert scene_2["clip"] == clip(2).pk


def test_the_newest_pictures_note_again_is_already_made_and_the_scene_stays_finished(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    assembled: ProducedItem,  # noqa: F811
) -> None:
    new_picture(fake_model, steps, say, 2, HIGHER)
    clip_of_scene_2(fake_model, steps, say)
    paid = paid_for()

    said = back_to(fake_model, say, HIGHER)

    assert paid_for() == paid
    assert said.startswith(
        "Scene 2's starting picture was already made for this line with this note (version "
        "2), and the shop owner has seen it, so nothing was made or paid for again."
    )
    assert versions("starting_picture") == [1, 2]
    assert status(2) == "finished"


def test_a_brought_back_picture_whose_clip_was_never_made_gets_a_new_clip_for_its_fix(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    assembled: ProducedItem,  # noqa: F811
) -> None:
    new_picture(fake_model, steps, say, 2, HIGHER)
    new_picture(fake_model, steps, say, 2, SLOWER)
    clip_of_scene_2(fake_model, steps, say)
    paid = paid_for()

    back_to(fake_model, say, HIGHER)
    assert paid_for() == paid
    assert picture(2).file == version("starting_picture", 2).file
    clip_of_scene_2(fake_model, steps, say)

    assert paid_for().count("make_broll_clip") == paid.count("make_broll_clip") + 1
    made = clip(2)
    assert made.version == 3
    assert made.picture == picture(2)
    assert made.step is not None and made.step.fix is not None
    assert made.step.fix.fix == HIGHER
    assert status(2) == "finished"
    # Made for the shop owner's fix, so code assembled the ad with it as it landed (#135).
    scene_2 = next(cut for cut in ads()[-1].cuts if cut["scene"] == 2)
    assert scene_2["clip"] == made.pk


def test_bringing_back_an_earlier_picture_still_needs_the_shop_owners_yes(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], higher_then_slower: None
) -> None:
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": HIGHER}))

    assert results_of("make_starting_picture")[-1].startswith(
        "Refused: the note isn't scene 2's fix as it was restated to the shop owner"
    )
    assert versions("starting_picture") == [1, 2, 3]
    assert status(2) == "finished"


def test_going_back_to_a_picture_with_no_note_on_a_scene_with_a_clip_needs_a_fix(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], higher_then_slower: None
) -> None:
    # Bringing a picture back changes the ad, so it is a fix like any new picture (#135).
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": None}))

    assert results_of("make_starting_picture")[-1] == (
        "Refused: scene 2 already has a clip, so changing it is a fix. Call restate_fix "
        "first, wait for the shop owner's answer, then pass the fix word for word as the "
        "note. Nothing was done."
    )
    assert versions("starting_picture") == [1, 2, 3]
    assert status(2) == "finished"


def test_no_picture_is_brought_back_while_the_scenes_clip_is_being_made(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    assembled: ProducedItem,  # noqa: F811
) -> None:
    # So a clip of a newer picture can never finish the scene after the earlier one is back.
    new_picture(fake_model, steps, say, 2, HIGHER)
    clip_of_scene_2(fake_model, steps, say)
    new_picture(fake_model, steps, say, 2, SLOWER)
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    assert len(steps.held) == 1

    said = back_to(fake_model, say, HIGHER)

    assert said == (
        "Refused: scene 2's clip is still being made. You'll be told when it's ready; make its "
        "starting picture again then. Nothing was done."
    )
    assert versions("starting_picture") == [1, 2, 3]


def test_a_worker_stopping_as_a_picture_is_brought_back_leaves_one_new_version(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
    higher_then_slower: None,
) -> None:
    paid = paid_for()
    said_yes_to_a_fix(2, HIGHER, "picture")
    fake_model.respond(
        "produce",
        turn(calls=[("make_starting_picture", {"scene": 2, "note": HIGHER})]),
    )
    # The picture is back, but the worker stops before the tool's answer is kept.
    answer_kept = the_worker_stops(
        pre_save,
        ToolCall,
        when=lambda call: call.tool == "make_starting_picture" and call.finished_at is not None,
    )
    with answer_kept, pytest.raises(WorkerStopped):
        say("Go back to the higher one")
    assert versions("starting_picture") == [1, 2, 3, 4]
    the_producer_died(session_id)
    fake_model.respond("produce", turn(says="Ok."))

    restart_dead_producers()

    assert versions("starting_picture") == [1, 2, 3, 4]
    assert paid_for() == paid
    assert results_of("make_starting_picture")[-1].startswith(
        "Scene 2's starting picture was already made for this line with this note (version 4)"
    )
    assert status(2) == "planned"
