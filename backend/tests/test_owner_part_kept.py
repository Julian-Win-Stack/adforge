"""The owner part kept for the whole ad (owner-part approval, T4): once the shop owner says
yes to a scene's owner part, every other scene whose plan shows the same part gets it,
approved, with the shop owner's photos of it, and every later fix draws with it without
asking again. Going back to a version made before a photo of it sets the photo aside, as for
any reference photo. The first ad is made through the chat, its owner parts put on the scenes
as the planner and the shop owner's answers would; fixes are plans run by code
(tests/fixing.py). The models are faked at the gateway."""

from collections.abc import Callable

import pytest

from agents import decider, producer
from gateway.fake import FakeModel
from jobs import owner_parts, work
from jobs.models import ReferencePhoto, Scene

from . import fixing
from .conftest import HeldSteps, handoffs, results_of
from .test_producer_broll import BROLL_CHOICE, calling, instructed, run  # noqa: F401
from .test_producer_owner_photos import (  # noqa: F401 (fixtures)
    OWNER_MUG,
    checked,
    clean_pictures,
    in_use,
    last_step,
    made_ready,
    owner_file,
    photos_sent,
)

pytestmark = pytest.mark.django_db(transaction=True)

Say = Callable[..., None]

HANDLE = {
    "what": "the handle",
    "source": "owner's words",
    "looks": "A thick matte loop, the same colour as the mug.",
}


def scene(number: int) -> Scene:
    return Scene.objects.get(number=number)


@pytest.fixture
def approved(
    fake_model: FakeModel,
    checked: None,  # noqa: F811 (the fixture)
    steps: HeldSteps,
    say: Say,
) -> None:
    """Scenes 2 and 3 planned with the same owner part; the shop owner sent a photo of it
    for scene 2, whose picture was drawn with it, and said yes to it. Scene 3's picture was
    drawn before the yes, without the photo."""
    photos_sent(fake_model, say, OWNER_MUG)
    Scene.objects.filter(number__in=(2, 3)).update(owner_part=HANDLE)
    ReferencePhoto.objects.create(
        scene=scene(2), file=owner_file(1), thing="handle", use=ReferencePhoto.Use.PICTURE
    )
    calling(fake_model, say, ("create_music", {"mood": "light upbeat lo-fi"}))
    made_ready(fake_model, steps, say)

    owner_parts.approve(scene(2))


def drawn_again(fake_model: FakeModel, steps: HeldSteps, say: Say, number: int) -> None:
    calling(fake_model, say, ("make_starting_picture", {"scene": number, "note": None}))
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    run(fake_model, steps)


@pytest.fixture
def shown(fake_model: FakeModel, approved: None, steps: HeldSteps, say: Say) -> None:
    """The first ad, made and shown, scene 3 drawn again with the part's photo first."""
    drawn_again(fake_model, steps, say, 3)
    calling(fake_model, say, *[("make_clip", {"scene": number}) for number in (1, 2, 3)])
    run(fake_model, steps)
    calling(fake_model, say, ("assemble_ad", {}))


def test_the_yes_gives_the_part_and_its_photo_to_every_scene_that_shows_it(
    approved: None,
) -> None:
    assert scene(3).owner_part == HANDLE
    assert owner_parts.is_approved(scene(3))
    assert in_use(3) == [("handle", "picture")]


def test_a_scene_drawn_before_the_photo_was_kept_is_drawn_again_before_its_clip(
    fake_model: FakeModel, approved: None, steps: HeldSteps, say: Say
) -> None:
    calling(fake_model, say, ("make_clip", {"scene": 3}))

    assert results_of("make_clip") == [
        "Refused: scene 3's starting picture was drawn before the shop owner's photo of the "
        "handle was kept for it. Make its starting picture again first. Nothing was done."
    ]

    drawn_again(fake_model, steps, say, 3)

    # The clean picture drawn for scene 2 is sent first, not drawn again.
    (clean,) = clean_pictures()
    assert handoffs("make_starting_picture")[-1]["pictures"][0] == clean
    assert owner_parts.is_approved(scene(3))


def test_a_later_fix_of_the_scene_draws_it_with_the_approved_part(
    fake_model: FakeModel, shown: None, steps: HeldSteps
) -> None:
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    fixing.fixed(
        3, "new_picture", note="Make it brighter.", change="In scene 3, the shelf is brighter."
    )
    steps.run_held()

    step = last_step("starting_picture", 3)
    assert step.fix is not None
    assert step.owner_part == HANDLE
    assert [row.thing for row in step.reference_photos.all()] == ["handle"]
    (clean,) = clean_pictures()
    assert handoffs("make_starting_picture")[-1]["pictures"][0] == clean
    # The fix's picture is of the part the shop owner said yes to: no new yes is needed.
    assert owner_parts.is_approved(scene(3))


def test_going_back_before_the_photo_sets_it_aside_and_forward_uses_it_again(
    shown: None,
) -> None:
    labels = [version.label for version in work.versions(scene(3))]
    assert labels[:2] == ["v1 first version", "v2 first version"]

    work.go_back(scene(3), 1)

    assert in_use(3) == []

    work.go_back(scene(3), 2)

    assert in_use(3) == [("handle", "picture")]
    assert owner_parts.is_approved(scene(3))


def test_the_producer_and_the_decider_never_ask_again_about_an_approved_part() -> None:
    for instructions in (producer.INSTRUCTIONS, decider.INSTRUCTIONS):
        assert "never ask them for a photo of it or how it looks" in " ".join(instructions.split())
