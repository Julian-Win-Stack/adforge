"""A B-roll scene's owner part corrected (owner-part approval, T2): a scene with no owner part
can't be corrected, and a photo of the part, cleaned, enters the library as the part's own
picture, never as the shop owner's photo (#234). The part's picture is no longer drawn into a
starting picture: a B-roll scene has none (spec #210 point 19), so the tests of the part drawn
into it, its prompts and its picture check are gone; tests/test_producer_owner_part.py covers
the part's picture and its corrections. The scene models are faked at the gateway."""

from collections.abc import Callable
from typing import Any

import pytest
from django.utils import timezone

from adforge import file_store
from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs import owner_parts
from jobs.models import Job, ReferencePhoto, Scene

from .conftest import HeldSteps, photo
from .test_producer_broll import (
    checked,  # noqa: F401 (a fixture)
    instructed,  # noqa: F401 (a fixture)
    made_ready,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("checked")]

# Made up, as every example in a test: the mug's lid, which no photo of the job shows.
LID = {
    "what": "the lid",
    "source": "owner's words",
    "looks": "A flat round lid of pale green silicone with a small round sip hole.",
}
NEW_LOOKS = "A domed lid of clear plastic with a slide over the sip hole."


def with_owner_part(part: dict[str, Any] | None = LID, **fields: Any) -> Scene:
    Scene.objects.filter(number=2).update(owner_part=part, **fields)
    return Scene.objects.get(number=2)


def approved() -> Scene:
    return with_owner_part(owner_part_approved_at=timezone.now())


def owner_photo() -> str:
    return file_store.save("owner-lid.png", photo(9))


def test_a_scene_with_no_owner_part_cant_be_corrected() -> None:
    scene = Scene.objects.get(number=2)

    with pytest.raises(ValueError):
        owner_parts.words_correction(scene, NEW_LOOKS)
    with pytest.raises(ValueError):
        owner_parts.photo_correction(scene, owner_photo())
    assert not ReferencePhoto.objects.exists()
    assert Job.objects.get().scenes.get(number=2).owner_part is None


def test_a_photo_of_the_part_cleaned_doesnt_enter_the_library_as_the_owners_photo(
    fake_model: FakeModel,
    instructed: dict[str, list[str]],  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from jobs.library import library_of

    with_owner_part()
    made_ready(fake_model, steps, say, (2,))
    scene = approved()
    before = library_of(scene.job)
    labelled = ModelCall.objects.filter(purpose="label_photo").count()

    owner_parts.photo_correction(scene, owner_photo())

    # The part's picture enters the library as the part's (#234), not as an owner photo.
    assert library_of(scene.job) == before
    assert ModelCall.objects.filter(purpose="label_photo").count() == labelled
