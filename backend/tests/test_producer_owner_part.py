"""Drawn parts and owner corrections keep filming without an approval round."""

from collections.abc import Callable
from typing import Any

import pytest

from agents.models import ToolCall
from chat.models import Message
from gateway.fake import FakeModel, turn
from jobs import owner_parts, reference_photos, scene_references
from jobs.library import OwnerPart, library_of
from jobs.models import Job, ReferencePhoto, Scene
from jobs.reference_photos import ReferencePhotoUnusable

from .conftest import (
    HeldSteps,
    handoffs,
    photo,
    results_of,
)
from .part_scene import part_scene_agent, script_part_picture  # noqa: F401
from .test_producer_broll import (
    DOES_A_JOB,
    calling,
    checked,  # noqa: F401 (a fixture)
    made_ready,
    run,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
# Every test starts from a chat whose ad is planned with scene 2 showing the mug, has its
# person, and whose lines passed the fact check; scene 2 shows an owner part.
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("checked"),
]

PART = {
    "what": "the lid",
    "source": "owner's words",
    "looks": "A round wooden lid with a small knob on top.",
}


@pytest.fixture(autouse=True)
def with_an_owner_part(checked: None) -> None:  # noqa: F811
    Scene.objects.filter(number=2).update(owner_part=PART, **DOES_A_JOB)


@pytest.fixture
def pictured(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    """Scene 2 has its audio, heard, and its part its own picture, shown to the shop owner (it
    has no starting picture: spec #210 point 19). A picture its clip is sent recoloured passes
    its check."""
    made_ready(fake_model, steps, say, (2,))
    owner_parts.draw_part_picture(scene_2())


def scene_2() -> Scene:
    return Scene.objects.get(number=2)


def clips_of(scene: int) -> int:
    return Job.objects.get().produced.filter(kind="clip", scene__number=scene).count()


def pictures_of_scene_2() -> int:
    return Job.objects.get().produced.filter(kind="starting_picture", scene__number=2).count()


def part_item() -> Any:
    (item,) = [each for each in library_of(Job.objects.get()) if isinstance(each.source, OwnerPart)]
    return item


def owner_says(
    fake_model: FakeModel, say: Callable[..., None], text: str, *calls: tuple[str, dict[str, Any]]
) -> None:
    """The shop owner sends `text`, and the producer makes `calls`, then replies."""
    fake_model.respond("produce", turn(calls=list(calls)), turn(says="On it."))
    say(text)


def corrected(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    words: str | None,
    photo_number: int | None = None,
) -> None:
    """The shop owner corrects the part, and the producer has its picture drawn again."""
    owner_says(
        fake_model,
        say,
        "Not quite.",
        ("correct_owner_part", {"scene": 2, "words": words, "photo": photo_number}),
    )


# --- Corrections -------------------------------------------------------------------------------


@pytest.mark.usefixtures("pictured")
def test_the_shop_owners_words_draw_the_parts_picture_again_and_show_it_again(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    looks = "A round pink lid with a small knob on top."
    before = part_item()

    corrected(fake_model, steps, say, looks)

    assert owner_parts.owner_part(scene_2()).looks == looks  # type: ignore[union-attr]
    after = part_item()
    assert (after.number, after.file != before.file) == (before.number, True)
    assert looks in handoffs("make_part_picture")[-1]["prompt"]
    (result,) = results_of("correct_owner_part")
    assert f"library picture {after.number}" in result
    assert "say yes" not in result
    assert "wait for" not in result
    # Nothing else is drawn: a B-roll scene has no starting picture (spec #210 point 19).
    assert pictures_of_scene_2() == 0


@pytest.mark.usefixtures("pictured")
def test_the_shop_owners_photo_draws_the_part_from_it(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("correct_owner_part", {"scene": 2, "words": None, "photo": 1})]),
        turn(says="On it."),
    )
    say("Here is the lid.", ("lid.png", photo(9)))

    (row,) = ReferencePhoto.objects.filter(scene=scene_2())
    assert reference_photos.same_thing(row.thing, "the lid")
    assert row.use == "picture"
    # The photo cleaned is the part's picture: nothing is drawn from the sentence.
    assert part_item().file == row.drawn
    assert len(handoffs("make_part_picture")) == 1
    assert Message.objects.filter(role="notice", text__contains="cleaned from your photo").exists()
    assert owner_parts.part_picture(scene_2()) == part_item()


@pytest.mark.usefixtures("pictured")
def test_the_shop_owners_photo_and_words_both_go_into_the_part(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    looks = "The lid from the photo, in pink."
    fake_model.respond(
        "produce",
        turn(calls=[("correct_owner_part", {"scene": 2, "words": looks, "photo": 1})]),
        turn(says="On it."),
    )
    say("Take the lid from this, make it pink.", ("lid.png", photo(9)))

    (row,) = ReferencePhoto.objects.filter(scene=scene_2())
    assert reference_photos.same_thing(row.thing, "the lid")
    assert owner_parts.owner_part(scene_2()).looks == looks  # type: ignore[union-attr]
    # The photo's clean picture, changed as they said.
    changed = handoffs("make_part_picture")[-1]
    assert changed["pictures"] == [row.drawn]
    assert looks in changed["prompt"]


@pytest.mark.usefixtures("pictured")
def test_a_photo_the_part_cant_be_drawn_from_reports_its_reason(
    fake_model: FakeModel, say: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    def unusable(scene: Scene, file: str, words: str = "") -> None:
        raise ReferencePhotoUnusable(
            "the lid couldn't be drawn from the shop owner's photo.",
            thing="the lid",
        )

    monkeypatch.setattr(owner_parts, "photo_correction", unusable)
    fake_model.respond(
        "produce",
        turn(calls=[("correct_owner_part", {"scene": 2, "words": None, "photo": 1})]),
        turn(says="The photo could not be used; I kept the existing lid."),
    )
    say("Here is the lid.", ("lid.png", photo(9)))

    (result,) = results_of("correct_owner_part")
    assert result.startswith("Refused: the lid couldn't be drawn")
    assert "another photo" not in result
    assert pictures_of_scene_2() == 0


@pytest.mark.usefixtures("pictured")
def test_a_correction_needs_words_or_a_photo(
    fake_model: FakeModel, say: Callable[..., None]
) -> None:
    owner_says(
        fake_model, say, "Hmm.", ("correct_owner_part", {"scene": 2, "words": None, "photo": None})
    )

    (result,) = results_of("correct_owner_part")
    assert result.startswith("Refused:")
    assert pictures_of_scene_2() == 0


@pytest.mark.usefixtures("pictured")
def test_three_corrections_use_the_latest_part_without_a_yes(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    number = part_item().number
    for colour in ["pink", "blue", "green"]:
        corrected(fake_model, steps, say, f"A round {colour} lid with a small knob on top.")
    script_part_picture(fake_model, part_item().number)
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 2, "instruction": "Show the planned action.", "quotes": ["Go on"]},
        ),
    )
    run(fake_model, steps)
    assert part_item().number == number
    assert len(handoffs("make_part_picture")) == 4
    assert clips_of(2) == 1
    clip = Job.objects.get().produced.get(kind="clip", scene__number=2)
    assert clip.picture is not None and clip.sent is not None
    sent = [picture["library_number"] for picture in clip.sent["picture_photos"]]
    job = Job.objects.get()
    originals = [scene_references.original_number(job, n) for n in sent if n is not None]
    assert part_item().number in originals


# --- The rest of the ad --------------------------------------------------------------------------


def test_a_scene_with_no_owner_part_is_made_as_before(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    Scene.objects.filter(number=2).update(owner_part=None)
    made_ready(fake_model, steps, say, (2,))

    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 2, "instruction": "Show the planned action.", "quotes": ["Go on"]},
        ),
    )
    run(fake_model, steps)

    assert clips_of(2) == 1
    assert not Message.objects.filter(role="notice", text__contains="Say yes").exists()


# --- What the producer is refused -------------------------------------------------------------


@pytest.mark.usefixtures("pictured")
def test_a_correction_naming_a_photo_the_chat_hasnt_got_is_refused(
    fake_model: FakeModel, say: Callable[..., None]
) -> None:
    owner_says(
        fake_model,
        say,
        "Like this.",
        ("correct_owner_part", {"scene": 2, "words": None, "photo": 3}),
    )

    (result,) = results_of("correct_owner_part")
    assert result.startswith("Refused: the chat has ")
    assert pictures_of_scene_2() == 0


@pytest.mark.usefixtures("pictured")
def test_a_drawn_part_films_without_an_owner_approval_message(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    script_part_picture(fake_model, part_item().number)
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 2, "instruction": "Show the planned action.", "quotes": ["Go on"]},
        ),
    )
    assert not results_of("start_scene_agent")[-1].startswith("Refused")
    run(fake_model, steps)
    assert clips_of(2) == 1
    assert not ToolCall.objects.filter(tool="approve_owner_part").exists()
    assert not Message.objects.filter(role="notice", text__contains="Say yes").exists()
