"""Going back with the shop owner's photos (fix redesign, decision 8): a photo "drop" is a
plain go-back to a version made without it. The scene then uses the photos the version gone
back to was made with, so a later fix is sent without the photo, and going back to a version
made with it uses it again. Scene 2 shows the product, so its versions are its clips, each
made from its sent list (spec #210 point 19). The first ad is made through the chat and its
fixes are plans run by code (tests/fixing.py); the go-back is called as code calls it. The
models are faked at the gateway."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs import work
from jobs.models import Fix, ProducedItem, Scene

from . import fixing
from .conftest import HeldSteps
from .test_producer_owner_photos import (  # noqa: F401 (fixtures)
    MUG_FIX,
    MUG_NOTE,
    OWNER_MUG,
    REWRITE,
    checked,
    last_step,
    photo,
    photos_sent,
    recolours_pass,
    shown,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("mug_clip")]

Say = Callable[..., None]


@pytest.fixture
def mug_clip(fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say) -> Fix:  # noqa: F811
    """Scene 2's clip made again with the shop owner's black mug sent with it."""
    photos_sent(fake_model, say, OWNER_MUG)
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    fixing.fixed(2, "new_clip", note=MUG_NOTE, change=MUG_FIX, photos=[photo(1, "black mug")])
    steps.run_held()
    return Fix.objects.get()


def scene_2() -> Scene:
    return Scene.objects.get(number=2)


def things_in_use() -> list[str]:
    return [row.thing for row in scene_2().photos_in_use()]


def item(kind: str, number: int) -> ProducedItem:
    return ProducedItem.objects.get(kind=kind, scene__number=2, version=number)


def slower(fake_model: FakeModel, steps: HeldSteps) -> list[str]:
    """A later fix of scene 2's clip, with no photo, said yes to and made: the things its
    step was sent."""
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    fixing.fixed(2, "new_clip", note="Pour it slower.", change="The tea pours slower.")
    steps.run_held()
    return [row.thing for row in last_step("clip").reference_photos.all()]


def test_dropping_the_photo_is_going_back_to_the_version_before_it_for_nothing() -> None:
    assert [version.label for version in work.versions(scene_2())] == [
        "v1 first version",
        f"v2 {Fix.objects.get().fix}",
    ]
    calls = ModelCall.objects.count()

    gone = work.go_back(scene_2(), 1)

    assert ModelCall.objects.count() == calls
    assert not gone.clip_to_make
    assert gone.version.picture is None and gone.version.clip is not None
    assert gone.version.clip.file == item("clip", 1).file
    assert things_in_use() == []


def test_after_going_back_a_later_fix_is_made_without_the_photo(
    fake_model: FakeModel, steps: HeldSteps
) -> None:
    work.go_back(scene_2(), 1)

    assert slower(fake_model, steps) == []


def test_going_back_to_the_version_with_the_photo_uses_it_again(
    fake_model: FakeModel, steps: HeldSteps
) -> None:
    work.go_back(scene_2(), 1)
    assert things_in_use() == []

    gone = work.go_back(scene_2(), 2)

    assert gone.version.label == "v4 back to v2"
    assert things_in_use() == ["black mug"]
    assert slower(fake_model, steps) == ["black mug"]


def test_going_back_to_the_version_with_the_photo_uses_it_again_after_a_fix_dropped_it() -> None:
    # A fix the shop owner said yes to dropped the photo: going back to the version made
    # with it is the owner choosing it again.
    scene_2().reference_photos.update(dropped_by=Fix.objects.get())
    assert things_in_use() == []

    work.go_back(scene_2(), 2)

    assert things_in_use() == ["black mug"]
