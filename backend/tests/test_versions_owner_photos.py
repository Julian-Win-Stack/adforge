"""Going back with the shop owner's photos (fix redesign, decision 8): a photo "drop" is a
plain go-back to a version made without it. The scene then uses the photos the version gone
back to was made with, so a later fix draws it without the photo, and going back to a version
made with it uses it again. Driven through the chat up to the go-back, which code calls; the
models are faked at the gateway."""

import pytest

from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs import work
from jobs.models import Fix, ProducedItem, Scene

from .conftest import HeldSteps, handoffs
from .test_producer_broll import BROLL_CHOICE
from .test_producer_owner_photos import (  # noqa: F401 (fixtures)
    Say,
    checked,
    clean_pictures,
    fix_asked,
    last_step,
    mug_fixed,
    restates,
    routes,
    says_yes,
    shown,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("mug_fixed")]

BRIGHTER = "In scene 2, the workshop is brighter."


def scene_2() -> Scene:
    return Scene.objects.get(number=2)


def things_in_use() -> list[str]:
    return [row.thing for row in scene_2().photos_in_use()]


def item(kind: str, number: int) -> ProducedItem:
    return ProducedItem.objects.get(kind=kind, scene__number=2, version=number)


def brighter(fake_model: FakeModel, steps: HeldSteps, say: Say) -> None:
    """A later fix of scene 2's starting picture, said yes to and made."""
    restates(fake_model, say, "Make it brighter.", fix_asked(2, BRIGHTER, "picture"))
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    says_yes(fake_model, steps, say, ("make_starting_picture", {"scene": 2, "note": BRIGHTER}))


def test_dropping_the_photo_is_going_back_to_the_version_before_it_for_nothing() -> None:
    assert [version.label for version in work.versions(scene_2())] == [
        "v1 first version",
        f"v2 {Fix.objects.get().fix}",
    ]
    calls = ModelCall.objects.count()

    gone = work.go_back(scene_2(), 1)

    assert ModelCall.objects.count() == calls
    assert not gone.clip_to_make
    assert gone.version.picture is not None and gone.version.clip is not None
    assert gone.version.picture.file == item("starting_picture", 1).file
    assert gone.version.clip.file == item("clip", 1).file
    assert things_in_use() == []


def test_after_going_back_a_later_fix_is_made_without_the_photo(
    fake_model: FakeModel, steps: HeldSteps, say: Say
) -> None:
    work.go_back(scene_2(), 1)

    brighter(fake_model, steps, say)

    assert list(last_step("starting_picture").reference_photos.all()) == []
    (clean,) = clean_pictures()
    assert clean not in handoffs("make_starting_picture")[-1]["pictures"]


def test_going_back_to_the_version_with_the_photo_uses_it_again(
    fake_model: FakeModel, steps: HeldSteps, say: Say
) -> None:
    work.go_back(scene_2(), 1)
    assert things_in_use() == []

    gone = work.go_back(scene_2(), 2)

    assert gone.version.label == "v4 back to v2"
    assert things_in_use() == ["black mug"]
    brighter(fake_model, steps, say)
    (clean,) = clean_pictures()
    assert handoffs("make_starting_picture")[-1]["pictures"][0] == clean
