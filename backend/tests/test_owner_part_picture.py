"""A B-roll scene with an owner part, driven through the chat (owner-part approval, T2): no
photo shows the part, so its starting picture is drawn from the one sentence the shop owner
gave, or the planner wrote for them to approve. The model writing the prompts is told to draw
the part exactly as that sentence says, the picture model is told it before the prompt, and
the picture check is given the sentence where it is given a shop photo for the product. A
correction draws the picture again: the owner's words replace the sentence, and a photo of the
part goes down the reference-photo path, winning over the sentence. Either clears the owner's
yes. The producer's model and the scene models are faked at the gateway."""

from collections.abc import Callable
from typing import Any

import pytest
from django.utils import timezone

from adforge import file_store
from gateway.fake import FakeModel
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter
from jobs import owner_parts
from jobs.models import Job, ReferencePhoto, Scene
from jobs.picture_check import STARTING_PICTURE_CHECK
from jobs.reference_photos import ReferencePhotoUnusable

from .conftest import PICTURE_OK, HeldSteps, handoffs, photo
from .test_producer_broll import (
    BROLL_CHOICE,
    checked,  # noqa: F401 (a fixture)
    instructed,  # noqa: F401 (a fixture)
    picture_of_scene_2,
)
from .test_producer_broll_picture_check import FIXED_CHOICE, FUSED_HANDLE, impossible

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


def pictures_prompts() -> list[str]:
    return [asked["prompt"] for asked in handoffs("make_starting_picture")]


# --- The picture drawn from the sentence ------------------------------------------------------


def test_the_prompt_writer_is_told_to_draw_the_part_as_the_sentence_says(
    fake_model: FakeModel,
    instructed: dict[str, list[str]],  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    with_owner_part()

    step = picture_of_scene_2(fake_model, steps, say)

    assert step.status == "finished"
    (told,) = instructed["choose_broll_picture"]
    assert told.endswith(owner_parts.writer_rule(owner_parts.OwnerPart(**LID)))
    assert f'"{LID["what"]}"' in told
    assert LID["looks"] in told


def test_the_picture_model_is_told_the_parts_looks_before_the_prompt(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    with_owner_part()

    picture_of_scene_2(fake_model, steps, say)

    (prompt,) = pictures_prompts()
    label = owner_parts.picture_label(owner_parts.OwnerPart(**LID))
    assert prompt == f"{label} {BROLL_CHOICE['prompt']}"
    assert LID["looks"] in label


def test_the_check_is_given_the_sentence_where_it_has_no_photo_of_the_part(
    fake_model: FakeModel,
    instructed: dict[str, list[str]],  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    with_owner_part()

    picture_of_scene_2(fake_model, steps, say)

    checks = handoffs("check_starting_picture")
    assert checks
    assert all(
        check["owner_part"] == {"what": LID["what"], "looks": LID["looks"]} for check in checks
    )
    # The check as tested, with only the owner part's rule after it.
    assert all(
        told == f"{STARTING_PICTURE_CHECK}\n\n{owner_parts.CHECK_RULE}"
        for told in instructed["check_starting_picture"]
    )


def test_a_scene_without_an_owner_part_is_drawn_and_checked_as_before(
    fake_model: FakeModel,
    instructed: dict[str, list[str]],  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    picture_of_scene_2(fake_model, steps, say)

    assert pictures_prompts() == [BROLL_CHOICE["prompt"]]
    assert all("owner_part" not in check for check in handoffs("check_starting_picture"))
    assert set(instructed["check_starting_picture"]) == {STARTING_PICTURE_CHECK}
    assert "none of the pictures sent" not in instructed["choose_broll_picture"][0]


def test_a_picture_drawn_again_after_its_check_failed_keeps_the_sentence(
    fake_model: FakeModel,
    instructed: dict[str, list[str]],  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    with_owner_part()
    fake_model.respond(
        "check_starting_picture",
        impossible(FUSED_HANDLE),
        impossible(FUSED_HANDLE),
        PICTURE_OK,
        PICTURE_OK,
    )

    step = picture_of_scene_2(fake_model, steps, say, BROLL_CHOICE, FIXED_CHOICE)

    assert step.status == "finished"
    rule = owner_parts.writer_rule(owner_parts.OwnerPart(**LID))
    assert len(instructed["choose_broll_picture"]) == 2
    assert all(rule in told for told in instructed["choose_broll_picture"])
    label = owner_parts.picture_label(owner_parts.OwnerPart(**LID))
    assert pictures_prompts() == [
        f"{label} {BROLL_CHOICE['prompt']}",
        f"{label} {FIXED_CHOICE['prompt']}",
    ]
    assert all(
        check["owner_part"]["looks"] == LID["looks"] for check in handoffs("check_starting_picture")
    )


# --- Corrections ------------------------------------------------------------------------------


def test_the_owners_words_replace_the_parts_looks_and_clear_their_yes(
    fake_model: FakeModel,
    instructed: dict[str, list[str]],  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    with_owner_part()
    picture_of_scene_2(fake_model, steps, say)
    scene = approved()

    owner_parts.words_correction(scene, NEW_LOOKS)

    scene.refresh_from_db()
    assert scene.owner_part == {**LID, "looks": NEW_LOOKS}
    assert scene.owner_part_approved_at is None
    # The picture drawn from the old sentence is no longer the scene's.
    assert scene.current_picture() is None
    picture_of_scene_2(fake_model, steps, say)
    assert NEW_LOOKS in pictures_prompts()[-1]
    assert NEW_LOOKS in instructed["choose_broll_picture"][-1]
    assert LID["looks"] not in instructed["choose_broll_picture"][-1]


def test_a_photo_of_the_part_is_kept_as_the_owners_reference_photo_and_drawn_with_first(
    fake_model: FakeModel,
    instructed: dict[str, list[str]],  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    with_owner_part()
    picture_of_scene_2(fake_model, steps, say)
    scene = approved()
    file = owner_photo()

    owner_parts.photo_correction(scene, file)

    row = ReferencePhoto.objects.get()
    assert (row.scene, row.file, row.thing, row.use) == (scene, file, "lid", "picture")
    assert row.added_by is None
    (clean,) = [
        call.output["file"]
        for call in ModelCall.objects.filter(purpose="make_reference_photo_picture")
        if call.output
    ]
    assert row.drawn == clean
    scene.refresh_from_db()
    assert scene.owner_part == LID
    assert scene.owner_part_approved_at is None

    picture_of_scene_2(fake_model, steps, say)

    assert handoffs("make_starting_picture")[-1]["pictures"][0] == clean
    # The photo wins: the part is no longer drawn from the sentence.
    told = instructed["choose_broll_picture"][-1]
    assert "Image 1: their lid" in told
    assert owner_parts.writer_rule(owner_parts.OwnerPart(**LID)) not in told
    assert LID["looks"] not in pictures_prompts()[-1]
    assert "owner_part" not in handoffs("check_starting_picture")[-1]


def test_a_photo_with_words_replaces_the_looks_too(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    with_owner_part()
    picture_of_scene_2(fake_model, steps, say)
    scene = approved()

    owner_parts.photo_correction(scene, owner_photo(), words="Take its shape, make it pink.")

    scene.refresh_from_db()
    assert scene.owner_part == {**LID, "looks": "Take its shape, make it pink."}
    assert scene.owner_part_approved_at is None
    assert ReferencePhoto.objects.get().drawn


def test_a_newer_photo_of_the_part_takes_the_older_ones_place(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    scene = with_owner_part()
    first = owner_photo()
    second = file_store.save("owner-lid-2.png", photo(10))

    owner_parts.photo_correction(scene, first)
    owner_parts.photo_correction(scene, second)

    assert [row.file for row in scene.photos_in_use()] == [second]
    assert ReferencePhoto.objects.count() == 2


def test_a_photo_the_part_cant_be_copied_from_keeps_nothing(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    with_owner_part()
    scene = approved()
    approved_at = scene.owner_part_approved_at

    def refusing(**asked: Any) -> Any:
        raise BlockedBySafetyFilter("refused")

    fake_model.edit = refusing  # type: ignore[method-assign]

    with pytest.raises(ReferencePhotoUnusable) as unusable:
        owner_parts.photo_correction(scene, owner_photo(), words="Make it pink.")

    assert unusable.value.thing == "lid"
    assert not ReferencePhoto.objects.exists()
    scene.refresh_from_db()
    assert scene.owner_part == LID
    assert scene.owner_part_approved_at == approved_at


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
    picture_of_scene_2(fake_model, steps, say)
    scene = approved()
    before = library_of(scene.job)
    labelled = ModelCall.objects.filter(purpose="label_photo").count()

    owner_parts.photo_correction(scene, owner_photo())

    # The part's picture enters the library as the part's (#234), not as an owner photo.
    assert library_of(scene.job) == before
    assert ModelCall.objects.filter(purpose="label_photo").count() == labelled
