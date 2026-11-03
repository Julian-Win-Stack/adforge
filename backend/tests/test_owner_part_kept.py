"""The owner part kept for the whole ad (owner-part approval, T4): once the shop owner says
yes to a scene's owner part, every other scene whose plan shows the same part gets it,
approved, with the shop owner's photos of it, and every later fix draws with it without
asking again. Going back to a version made before a photo of it sets the photo aside, as for
any reference photo. The first ad is made through the chat, its owner parts put on the scenes
as the planner and the shop owner's answers would; fixes are plans run by code
(tests/fixing.py). The models are faked at the gateway."""

from collections.abc import Callable

import pytest

from agents import producer
from gateway.fake import FakeModel
from jobs import owner_parts
from jobs.library import OwnerPart, library_of
from jobs.models import ReferencePhoto, Scene

from .conftest import HeldSteps, handoffs
from .part_scene import part_scene_agent, script_part_picture  # noqa: F401
from .test_producer_broll import calling, instructed, run  # noqa: F401
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
    for scene 2, whose picture and the part's own were drawn with it, and said yes to it.
    Scene 3's picture was drawn before the yes, without the photo."""
    photos_sent(fake_model, say, OWNER_MUG)
    Scene.objects.filter(number__in=(2, 3)).update(owner_part=HANDLE)
    ReferencePhoto.objects.create(
        scene=scene(2), file=owner_file(1), thing="handle", use=ReferencePhoto.Use.PICTURE
    )
    calling(fake_model, say, ("create_music", {"mood": "light upbeat lo-fi"}))
    made_ready(fake_model, steps, say)
    owner_parts.draw_part_picture(scene(2))

    owner_parts.approve(scene(2))


# The approved part stays in the library for the scene agent to pick when it draws.


def test_a_scene_planned_with_the_approved_part_sends_its_clip_the_parts_picture(
    fake_model: FakeModel, approved: None, steps: HeldSteps, say: Say
) -> None:
    (part,) = [item for item in library_of(scene(3).job) if isinstance(item.source, OwnerPart)]
    script_part_picture(fake_model, part.number)
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 3, "instruction": "Show the approved handle.", "quotes": ["Go on"]},
        ),
    )
    run(fake_model, steps)

    # The agent selected the approved part; its recoloured version draws the picture.
    (recoloured,) = handoffs("recolour_picture")
    assert recoloured["pictures"][0] == part.file
    assert "recolour_picture.png" in handoffs("draw_scene_picture")[-1]["pictures"]
    video = handoffs("make_broll_clip")[-1]
    assert video["starting_picture"]
    assert video["example_pictures"] == []
    assert owner_parts.is_approved(scene(3))


def test_the_producer_never_asks_again_about_an_approved_part() -> None:
    assert "never ask them for a photo of it or how it looks" in " ".join(
        producer.INSTRUCTIONS.split()
    )
