"""Owner-part corrections redraw the library picture and film without waiting for a yes."""

from collections.abc import Callable
from typing import Any

import pytest

from adforge import file_store
from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs import owner_parts
from jobs.library import LibraryEntry, OwnerPart, library_of
from jobs.models import Job, ReferencePhoto, Scene

from .conftest import HeldSteps, ScriptedProducer, handoffs, paid_for, photo, results_of
from .part_scene import part_scene_agent, script_part_picture  # noqa: F401
from .test_producer_broll import (
    DOES_A_JOB,
    checked,  # noqa: F401 (a fixture)
    instructed,  # noqa: F401 (a fixture)
    made_ready,
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("checked"),
]

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
    scene = with_owner_part()
    before = library_of(scene.job)
    labelled = ModelCall.objects.filter(purpose="label_photo").count()

    owner_parts.photo_correction(scene, owner_photo())

    # The part's picture enters the library as the part's (#234), not as an owner photo.
    assert library_of(scene.job) == before
    assert ModelCall.objects.filter(purpose="label_photo").count() == labelled


# --- Drawn parts film and can be corrected through the chat -------------------------------


@pytest.fixture
def lid_shown(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    """The checked plan and portrait are ready; scene 2's lid picture has been drawn."""
    with_owner_part(**DOES_A_JOB)
    made_ready(fake_model, steps, say, (1, 2, 3))
    owner_parts.draw_part_picture(Scene.objects.get(number=2))


def clips_of(scene: int) -> int:
    return Job.objects.get().produced.filter(kind="clip", scene__number=scene).count()


def lid_item() -> LibraryEntry:
    (item,) = [each for each in library_of(Job.objects.get()) if isinstance(each.source, OwnerPart)]
    return item


def every_clip(quote: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "start_scene_agent",
            {
                "scene": scene,
                "instruction": "Show the lid." if scene == 2 else "Film the planned action.",
                "quotes": [quote],
            },
        )
        for scene in (1, 2, 3)
    ]


def test_owner_parts_film_with_the_ad_and_a_correction_films_the_redrawn_part(
    producer: ScriptedProducer, fake_model: FakeModel, lid_shown: None
) -> None:
    producer.told("Make the clips", every_clip("Make the clips"), "Every scene is being made.")
    assert all(not result.startswith("Refused") for result in results_of("start_scene_agent"))
    producer.steps_run()
    assert (clips_of(1), clips_of(2), clips_of(3)) == (1, 1, 1)
    assert paid_for().count("make_part_picture") == 1

    before = lid_item()
    producer.told(
        "Make the lid pink.",
        [("correct_owner_part", {"scene": 2, "words": NEW_LOOKS, "photo": None})],
        "I redrew the lid with your correction.",
    )
    scene = Scene.objects.get(number=2)
    assert owner_parts.owner_part(scene).looks == NEW_LOOKS  # type: ignore[union-attr]
    after = lid_item()
    assert after.number == before.number and after.file != before.file
    assert "say yes" not in results_of("correct_owner_part")[-1]
    script_part_picture(fake_model, after.number)
    producer.told(
        "Use the new lid.",
        [
            (
                "start_scene_agent",
                {
                    "scene": 2,
                    "instruction": "Show the corrected lid.",
                    "quotes": ["Use the new lid."],
                },
            )
        ],
        "Making scene 2 with the new lid.",
    )
    assert not results_of("start_scene_agent")[-1].startswith("Refused")
    producer.steps_run()
    assert clips_of(2) == 2
    assert handoffs("recolour_picture")[-1]["pictures"][0] == after.file
    assert paid_for().count("make_part_picture") == 2
