"""The owner part's yes, driven through the chat (owner-part approval, T3; spec #210 point
20): a B-roll scene whose plan shows a part of the product no photo shows has its clip made
only once the shop owner says yes to the part's own picture in the library. Until then they
may correct it with words or a photo, as many times as they like, its picture each time
drawn again and shown again; every other scene goes on meanwhile, and once only it waits,
they are told once. The producer's model and the scene models are faked at the gateway."""

from collections.abc import Callable
from typing import Any

import pytest
from django.db.models.signals import pre_save

from agents import tasks
from agents.models import ToolCall
from agents.producer import INSTRUCTIONS
from agents.tasks import restart_dead_producers
from chat.models import Message
from gateway.fake import FakeModel, turn
from jobs import owner_parts, reference_photos
from jobs.library import OwnerPart, library_of
from jobs.models import Job, ReferencePhoto, Scene, SceneStep
from jobs.reference_photos import ReferencePhotoUnusable

from .conftest import (
    HeldSteps,
    chat,
    given_to_the_producer,
    handoffs,
    photo,
    producer_turns,
    results_of,
)
from .test_producer_broll import (
    BROLL_CHOICE,
    DOES_A_JOB,
    calling,
    checked,  # noqa: F401 (a fixture)
    made_ready,
    run,
)
from .test_producer_restarts import WorkerStopped, the_producer_died, the_worker_stops

# Each request commits on its own, as on the real server, and so does the producer's work.
# Every test starts from a chat whose ad is planned with scene 2 showing the mug, has its
# person, and whose lines passed the fact check; scene 2 shows an owner part.
pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("checked")]

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
    """Scene 2 has its starting picture and its audio, heard, and its part its own picture,
    shown to the shop owner."""
    made_ready(fake_model, steps, say, (2,))
    owner_parts.draw_part_picture(scene_2())


def scene_2() -> Scene:
    return Scene.objects.get(number=2)


def step_results() -> list[str]:
    """Every step result the producer was given, as of its last turn."""
    return [
        each["text"]
        for each in given_to_the_producer(producer_turns())
        if each["kind"] == "step_finished"
    ]


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


def yes(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    """The shop owner says yes to the picture, and the producer approves it and makes the
    clip."""
    owner_says(
        fake_model,
        say,
        "Yes, that lid looks right.",
        ("approve_owner_part", {"scene": 2}),
        ("make_clip", {"scene": 2}),
    )
    run(fake_model, steps)


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


def drawn_again(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    """Scene 2's starting picture drawn again, for the part as it stands now."""
    fake_model.respond(
        "produce",
        turn(calls=[("make_starting_picture", {"scene": 2, "note": None})]),
        turn(says="On it."),
    )
    say("Go on")
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    run(fake_model, steps)


# --- The picture shown, and the clip held ------------------------------------------------------


@pytest.mark.usefixtures("pictured")
def test_the_producer_is_told_the_yes_is_on_the_parts_picture_not_the_starting_picture() -> None:
    (picture_ready,) = [text for text in step_results() if "starting picture is ready" in text]

    assert "waits for the shop owner's yes on the picture of the lid itself" in picture_ready
    assert "don't ask them about this picture" in picture_ready


@pytest.mark.usefixtures("pictured")
def test_the_clip_waits_for_the_shop_owners_yes(
    fake_model: FakeModel, say: Callable[..., None]
) -> None:
    calling(fake_model, say, ("make_clip", {"scene": 2}))

    (result,) = results_of("make_clip")
    assert result.startswith("Refused: scene 2 shows the lid")
    assert f"library picture {part_item().number}" in result
    assert "wait for their answer" in result
    assert ToolCall.objects.get(tool="make_clip").asked_about == "the owner part"
    assert not SceneStep.objects.filter(kind="clip").exists()


@pytest.mark.usefixtures("pictured")
def test_the_shop_owners_yes_lets_the_clip_be_made(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    yes(fake_model, steps, say)

    (approved,) = results_of("approve_owner_part")
    assert not approved.startswith("Refused")
    assert owner_parts.is_approved(scene_2())
    assert clips_of(2) == 1


@pytest.mark.usefixtures("pictured")
def test_asked_in_the_chat_the_shop_owners_yes_in_words_is_kept_against_their_message(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], api: Any, session_id: str
) -> None:
    question = "Does the lid in this picture look right?"
    # Asking ends the producer's turn: it takes no other.
    fake_model.respond("produce", turn(calls=[("ask_owner", {"question": question})]))
    say("Go on")
    assert chat(api, session_id)[-1] == ("agent", question)

    yes(fake_model, steps, say)

    approving = Message.objects.get(role=Message.Role.USER, text="Yes, that lid looks right.")
    assert scene_2().owner_part_approved_by == approving
    assert clips_of(2) == 1


@pytest.mark.usefixtures("pictured")
def test_the_yes_is_kept_against_the_message_before_the_call_not_one_written_since(
    fake_model: FakeModel, say: Callable[..., None], session_id: str
) -> None:
    fake_model.respond("produce", turn(calls=[("approve_owner_part", {"scene": 2})]))
    # The worker stops as the call starts its work, before it reads the chat.
    approving = the_worker_stops(
        pre_save,
        ToolCall,
        when=lambda call: call.tool == "approve_owner_part" and call.job_id is not None,
    )
    with approving, pytest.raises(WorkerStopped):
        say("Yes, that lid looks right.")
    # Written once the call was made: the producer's turn never read it.
    say("Also, calmer music please.")
    the_producer_died(session_id)
    fake_model.respond("produce", turn(says="On it."))

    restart_dead_producers()

    assert owner_parts.is_approved(scene_2())
    assert scene_2().owner_part_approved_by == Message.objects.get(
        text="Yes, that lid looks right."
    )


@pytest.mark.usefixtures("pictured")
def test_a_correction_after_a_yes_drops_the_message_the_yes_was_kept_against(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    owner_says(fake_model, say, "Yes, that lid looks right.", ("approve_owner_part", {"scene": 2}))

    corrected(fake_model, steps, say, "A round pink lid with a small knob on top.")

    assert scene_2().owner_part_approved_by is None
    assert not owner_parts.is_approved(scene_2())


@pytest.mark.usefixtures("pictured")
def test_a_scene_with_no_owner_part_has_nothing_to_approve(
    fake_model: FakeModel, say: Callable[..., None]
) -> None:
    owner_says(fake_model, say, "Yes.", ("approve_owner_part", {"scene": 1}))

    (approved,) = results_of("approve_owner_part")
    assert approved.startswith("Refused: scene 1 shows no part")


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
    assert "until they say yes to it" in result
    # The starting picture isn't the yes's: it is drawn again only before the clip.
    assert pictures_of_scene_2() == 1


@pytest.mark.usefixtures("pictured")
def test_a_yes_on_an_earlier_picture_doesnt_let_the_clip_of_the_new_one_be_made(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    owner_says(fake_model, say, "Yes, that lid looks right.", ("approve_owner_part", {"scene": 2}))
    corrected(fake_model, steps, say, "A round pink lid with a small knob on top.")

    calling(fake_model, say, ("make_clip", {"scene": 2}))

    assert results_of("make_clip")[-1].startswith("Refused:")
    assert not owner_parts.is_approved(scene_2())
    assert clips_of(2) == 0


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
    assert (row.use, row.added_by) == ("picture", None)
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
def test_a_photo_the_part_cant_be_drawn_from_has_the_shop_owner_asked_for_another(
    fake_model: FakeModel, say: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    def unusable(scene: Scene, file: str, words: str = "") -> None:
        raise ReferencePhotoUnusable(
            "the lid couldn't be drawn from the shop owner's photo. Ask them for another photo "
            "of the lid.",
            thing="the lid",
        )

    monkeypatch.setattr(owner_parts, "photo_correction", unusable)
    fake_model.respond(
        "produce",
        turn(calls=[("correct_owner_part", {"scene": 2, "words": None, "photo": 1})]),
        turn(says="Could you send another?"),
    )
    say("Here is the lid.", ("lid.png", photo(9)))

    (result,) = results_of("correct_owner_part")
    assert result.startswith("Refused: the lid couldn't be drawn")
    assert "Ask them for another photo" in result
    assert pictures_of_scene_2() == 1


@pytest.mark.usefixtures("pictured")
def test_a_correction_needs_words_or_a_photo(
    fake_model: FakeModel, say: Callable[..., None]
) -> None:
    owner_says(
        fake_model, say, "Hmm.", ("correct_owner_part", {"scene": 2, "words": None, "photo": None})
    )

    (result,) = results_of("correct_owner_part")
    assert result.startswith("Refused:")
    assert pictures_of_scene_2() == 1


@pytest.mark.usefixtures("pictured")
def test_three_rounds_of_corrections_then_a_yes_make_the_clip(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    number = part_item().number
    for colour in ["pink", "blue", "green"]:
        corrected(fake_model, steps, say, f"A round {colour} lid with a small knob on top.")
        drawn_again(fake_model, steps, say)
        calling(fake_model, say, ("make_clip", {"scene": 2}))
        assert results_of("make_clip")[-1].startswith("Refused: scene 2 shows the lid")

    yes(fake_model, steps, say)

    # One library item, drawn four times.
    assert part_item().number == number
    assert len(handoffs("make_part_picture")) == 4
    assert clips_of(2) == 1
    clip = Job.objects.get().produced.get(kind="clip", scene__number=2)
    assert clip.picture is not None and clip.picture.version == 4


# --- The rest of the ad --------------------------------------------------------------------------


def other_scenes_clipped(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    """Scenes 1 and 3 are made to the end while scene 2 waits on the shop owner's yes."""
    made_ready(fake_model, steps, say, (1, 3))
    calling(fake_model, say, ("make_clip", {"scene": 1}), ("make_clip", {"scene": 3}))
    run(fake_model, steps)


def waits_notices() -> list[str]:
    return list(
        Message.objects.filter(role="notice", text__contains="waits for your yes").values_list(
            "text", flat=True
        )
    )


@pytest.mark.usefixtures("pictured")
def test_once_only_the_scene_waits_the_shop_owner_is_told_once(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    made_ready(fake_model, steps, say, (1, 3))
    calling(fake_model, say, ("make_clip", {"scene": 1}))
    run(fake_model, steps)
    assert waits_notices() == []

    calling(fake_model, say, ("make_clip", {"scene": 3}))
    run(fake_model, steps)

    (told,) = waits_notices()
    assert "Scene 2" in told and "the lid" in told
    assert "told the shop owner that scene 2 waits" in step_results()[-1]
    # Told again by nothing: not by a later look, nor by the ad refused.
    tasks.say_what_waits_on_the_shop_owner(Job.objects.get())
    calling(fake_model, say, ("assemble_ad", {}))
    assert len(waits_notices()) == 1
    assert results_of("assemble_ad")[-1].startswith("Refused:")
    assert not Job.objects.get().produced.filter(kind="finished_ad").exists()


@pytest.mark.usefixtures("pictured")
def test_the_shop_owners_yes_after_another_scenes_clip_still_reaches_the_producer(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # After the ad's first clip, the shop owner's messages are sorted first: a yes to the
    # part is no note on a clip, so it is the producer's to answer.
    other_scenes_clipped(fake_model, steps, say)
    # Every other scene is made while the scene waits.
    assert (clips_of(1), clips_of(2), clips_of(3)) == (1, 0, 1)

    yes(fake_model, steps, say)

    assert owner_parts.is_approved(scene_2())
    assert clips_of(2) == 1


def test_a_scene_with_no_owner_part_is_made_as_before(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    Scene.objects.filter(number=2).update(owner_part=None)
    made_ready(fake_model, steps, say, (2,))

    calling(fake_model, say, ("make_clip", {"scene": 2}))
    run(fake_model, steps)

    assert clips_of(2) == 1
    assert not any("says yes" in text for text in step_results())


def test_the_producer_takes_only_the_shop_owners_own_yes() -> None:
    assert (
        "Only when they say yes to that picture in their own words, call approve_owner_part."
        in INSTRUCTIONS
    )
    assert "never decide for them" in INSTRUCTIONS


# --- What the producer is refused -------------------------------------------------------------


@pytest.mark.usefixtures("pictured")
def test_a_second_yes_is_kept_once(fake_model: FakeModel, say: Callable[..., None]) -> None:
    owner_says(fake_model, say, "Yes, that lid looks right.", ("approve_owner_part", {"scene": 2}))
    owner_says(fake_model, say, "Yes, really.", ("approve_owner_part", {"scene": 2}))

    assert results_of("approve_owner_part")[-1].startswith(
        "The shop owner already said yes to the picture of the lid"
    )
    assert owner_parts.is_approved(scene_2())


def test_a_yes_before_any_picture_of_the_part_is_refused(
    fake_model: FakeModel, say: Callable[..., None]
) -> None:
    owner_says(fake_model, say, "Yes.", ("approve_owner_part", {"scene": 2}))

    (approved,) = results_of("approve_owner_part")
    assert approved.startswith("Refused: scene 2 has no picture of the lid as it is now")
    assert scene_2().owner_part_approved_at is None


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
    assert pictures_of_scene_2() == 1


@pytest.mark.usefixtures("pictured")
def test_a_correction_once_the_clip_is_made_is_refused(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    yes(fake_model, steps, say)

    owner_says(
        fake_model,
        say,
        "Make it pink.",
        ("correct_owner_part", {"scene": 2, "words": "A round pink lid.", "photo": None}),
    )

    (result,) = results_of("correct_owner_part")
    assert result.startswith("Refused: scene 2 already has a clip")
    assert owner_parts.owner_part(scene_2()) == owner_parts.OwnerPart(**PART)
    assert owner_parts.is_approved(scene_2())
