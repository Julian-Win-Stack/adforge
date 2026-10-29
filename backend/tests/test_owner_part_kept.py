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
from jobs.models import ReferencePhoto, Scene

from .conftest import HeldSteps, handoffs
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
from .test_recolour import RECOLOUR_OK

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


# Spec #210 point 19: a scene that shows the product gets no starting picture, so none is
# drawn again with the approved part. The tests of drawing it again before the clip, of a later
# fix drawing it with the part (and the part's looks for the prompt writer), and of going back
# to a picture drawn before the photo were deleted: the part reaches the clip on its sent list.


def test_a_scene_planned_with_the_approved_part_sends_its_clip_the_parts_picture(
    fake_model: FakeModel, approved: None, steps: HeldSteps, say: Say
) -> None:
    fake_model.answer_unscripted("check_recolour", lambda _: RECOLOUR_OK)
    calling(fake_model, say, ("make_clip", {"scene": 3}))
    run(fake_model, steps)

    # The part's clean picture is sent, recoloured to the clip's colour.
    (clean,) = clean_pictures()
    (recoloured,) = handoffs("recolour_picture")
    assert recoloured["pictures"][0] == clean
    assert "recolour_picture.png" in handoffs("make_broll_clip")[-1]["example_pictures"]
    assert handoffs("make_broll_clip")[-1]["starting_picture"] is None
    assert owner_parts.is_approved(scene(3))


def test_the_producer_never_asks_again_about_an_approved_part() -> None:
    assert "never ask them for a photo of it or how it looks" in " ".join(
        producer.INSTRUCTIONS.split()
    )
