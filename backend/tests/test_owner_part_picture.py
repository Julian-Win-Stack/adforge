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
from chat.models import Message
from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs import owner_parts
from jobs.library import LibraryEntry, OwnerPart, library_of
from jobs.models import Job, ReferencePhoto, Scene

from .conftest import HeldSteps, ScriptedProducer, photo, results_of
from .test_producer_broll import (
    DOES_A_JOB,
    checked,  # noqa: F401 (a fixture)
    instructed,  # noqa: F401 (a fixture)
    made_ready,
)
from .test_recolour import RECOLOUR_OK

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
    with_owner_part()
    made_ready(fake_model, steps, say, (2,))
    scene = approved()
    before = library_of(scene.job)
    labelled = ModelCall.objects.filter(purpose="label_photo").count()

    owner_parts.photo_correction(scene, owner_photo())

    # The part's picture enters the library as the part's (#234), not as an owner photo.
    assert library_of(scene.job) == before
    assert ModelCall.objects.filter(purpose="label_photo").count() == labelled


# --- Whole-build check C (spec #247 Testing Decisions, ticket #266) --------------------------
# The yes comes in words: the chat has no button for it (spec #247: no yes buttons; #260).


@pytest.fixture
def lid_shown(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    """Every scene has its audio, heard, and its pictures; scene 2 shows the lid, whose own
    picture is shown to the shop owner. A picture its clip is sent recoloured passes its
    check."""
    with_owner_part(**DOES_A_JOB)
    fake_model.answer_unscripted("check_recolour", lambda _: RECOLOUR_OK)
    made_ready(fake_model, steps, say, (1, 2, 3))
    owner_parts.draw_part_picture(Scene.objects.get(number=2))


def clips_of(scene: int) -> int:
    return Job.objects.get().produced.filter(kind="clip", scene__number=scene).count()


def lid_item() -> LibraryEntry:
    (item,) = [each for each in library_of(Job.objects.get()) if isinstance(each.source, OwnerPart)]
    return item


def every_clip() -> list[tuple[str, dict[str, Any]]]:
    return [("make_clip", {"scene": scene}) for scene in (1, 2, 3)]


def test_the_owner_parts_yes_in_words_after_the_first_clip_opens_the_gate_and_a_correction_redraws(
    producer: ScriptedProducer, lid_shown: None
) -> None:
    # The ad's first clips are made; scene 2's waits for the shop owner's yes on the lid.
    producer.told("Make the clips", every_clip(), "Scenes 1 and 3 are being made.")
    assert results_of("make_clip")[1].startswith("Refused: scene 2 shows the lid")
    producer.steps_run()
    assert (clips_of(1), clips_of(2), clips_of(3)) == (1, 0, 1)

    # The shop owner says yes in words: the producer approves it, and the clip is made.
    producer.told(
        "Yes, that lid looks right.",
        [("approve_owner_part", {"scene": 2}), ("make_clip", {"scene": 2})],
        "Making scene 2.",
    )
    said_yes = Message.objects.get(role=Message.Role.USER, text="Yes, that lid looks right.")
    assert Scene.objects.get(number=2).owner_part_approved_by == said_yes
    producer.steps_run()
    assert clips_of(2) == 1

    # A correction in words redraws the lid and closes the gate until a new yes.
    before = lid_item()
    producer.told(
        "Make the lid pink.",
        [
            ("correct_owner_part", {"scene": 2, "words": NEW_LOOKS, "photo": None}),
            ("make_clip", {"scene": 2}),
        ],
        "Here's the lid redrawn. Does it look right?",
    )
    scene = Scene.objects.get(number=2)
    assert owner_parts.owner_part(scene).looks == NEW_LOOKS  # type: ignore[union-attr]
    after = lid_item()
    assert after.number == before.number and after.file != before.file
    assert not owner_parts.is_approved(scene) and scene.owner_part_approved_by is None
    assert "until they say yes to it" in results_of("correct_owner_part")[-1]
    assert results_of("make_clip")[-1].startswith("Refused:")
    assert clips_of(2) == 1

    # The new yes, in words, lets the clip of the new picture be made.
    producer.told(
        "Yes, pink is right.",
        [("approve_owner_part", {"scene": 2}), ("make_clip", {"scene": 2})],
        "Making scene 2 with the pink lid.",
    )
    producer.steps_run()
    assert clips_of(2) == 2
    assert producer.said()[-1] == "Done."
