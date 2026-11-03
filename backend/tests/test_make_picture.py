"""make_picture (spec #247 point 5, Julian Q11): the producer makes a picture from its own
prompt with library items as references; the picture is checked once, as a cut-out is, and
stored as a new library item with the label the producer gives, so a drawn or combined
picture is a reference like any other. A failed check, a safety refusal or a refusal for
money comes back as the tool's answer with the reason, and the shop owner is never asked.
Driven through the chat: the owner's first message reads the page (the picker keeps two
photos, items 1 and 2), then the producer, faked and scripted at the gateway, makes the
picture; every other model is faked at the gateway too."""

import logging
from collections.abc import Callable
from typing import Any

import pytest
from django.db.models.signals import pre_save
from pytest_django import Settings

from adforge import file_store
from agents.models import ToolCall
from agents.tasks import restart_dead_producers
from chat.models import Message
from gateway import gateway
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter, RefusedForMoney
from jobs import cut_out
from jobs.library import Made, OwnerPart, PictureLabel, ProductOnly, add_to_library, library_of
from jobs.library_items import library_text
from jobs.models import Job, LibraryItem, Scene

from .conftest import LABELLED, HeldSteps, photo
from .test_picker_labels import LABELS, labelled_page  # noqa: F401 (a fixture)
from .test_producer_restarts import (
    WorkerStopped,
    the_producer_died,
    the_worker_stops,
    times_paid_for,
)
from .test_product_only import SIDE_ON_A_SHELF, first_message

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("legacy_path")]

Say = Callable[..., None]

FRONT = next(picked for picked in LABELS if picked["picture"] == "I8")
BACK = next(picked for picked in LABELS if picked["picture"] == "I101")

PROMPT = (
    "One photo with the bottle twice, side by side on a plain grey background: on the left "
    "from the front as in picture 1, on the right from the back as in picture 2."
)
LABEL = "The amber bottle from the front and from the back, side by side."

PASSES: dict[str, Any] = {
    "references_show": "The bottle from the front, and from the back with its label.",
    "picture_shows": "The bottle twice, front and back, unchanged, on plain grey.",
    "verdict": "pass",
    "problem": None,
}


def fails(problem: str) -> dict[str, Any]:
    return {**PASSES, "verdict": "fail", "problem": problem}


def making(
    fake_model: FakeModel, say: Say, *, references: list[int], prompt: str = PROMPT
) -> ToolCall:
    """The owner asks for the picture and the producer calls make_picture: gives the call."""
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "make_picture",
                    {"prompt": prompt, "references": references, "label": LABEL},
                )
            ]
        ),
        turn(says="I made it."),
    )
    say("Make one picture of the bottle from the front and the back.")
    return ToolCall.objects.filter(tool="make_picture").latest("id")


def asked(purpose: str) -> list[ModelCall]:
    return list(ModelCall.objects.filter(purpose=purpose).order_by("id"))


@pytest.fixture
def library(fake_model: FakeModel, labelled_page: str, say: Say) -> Job:  # noqa: F811
    """The ad's page read: its library holds the front (1) and the back (2)."""
    first_message(fake_model, say, labelled_page, FRONT, BACK)
    job = Job.objects.get()
    assert [item.number for item in library_of(job)] == [1, 2]
    return job


def test_a_prompt_and_two_library_items_make_a_checked_library_item_with_the_producers_label(
    fake_model: FakeModel, library: Job, say: Say
) -> None:
    fake_model.respond("check_made_picture", PASSES)

    call = making(fake_model, say, references=[1, 2])

    front, back, made = library_of(library)
    assert (made.number, made.source, made.label) == (3, Made(references=[1, 2]), LABEL)
    # The fixed fields are the labelling call's, which is told what the producer says it shows.
    assert (made.kind, made.side, made.colour) == (
        LABELLED["kind"],
        LABELLED["side"],
        LABELLED["colour"],
    )
    labelling = asked("label_photo")[-1]
    assert labelling.images[0]["key"] == made.file
    assert labelling.handoff["shows"] == LABEL
    # Drawn from the producer's prompt with the references' files, in its order.
    (drawing,) = asked("make_picture")
    assert drawing.handoff == {"prompt": PROMPT, "pictures": [front.file, back.file]}
    assert drawing.output == {"file": made.file}
    # Checked once, shown the picture first and the references after it.
    (check,) = asked("check_made_picture")
    assert [image["key"] for image in check.images] == [made.file, front.file, back.file]
    assert check.handoff == {
        "asked": PROMPT,
        "label": LABEL,
        "references": [front.label, back.label],
        "pictures": cut_out.MADE_PICTURES_SHOWN,
    }
    # The producer is told what it made, by number, with its label.
    assert call.answer == "made"
    assert "library item 3" in call.result
    assert LABEL in call.result
    assert "made by the producer from items 1, 2" in library_text(library)


def prepare_scenes(fake_model: FakeModel, say: Say, steps: HeldSteps, *scenes: Scene) -> None:
    """Prepare stored scene fixtures through the producer's real person/audio tools."""
    from .test_producer_broll import calling, run

    job = scenes[0].job
    job.person_looks = "A friendly adult in a plain shirt."
    job.person_voice = "A clear, warm speaking voice."
    job.save(update_fields=["person_looks", "person_voice"])
    calling(fake_model, say, ("create_person", {}))
    calling(fake_model, say, *[("make_line_audio", {"scene": scene.number}) for scene in scenes])
    run(fake_model, steps)
    calling(
        fake_model, say, *[("transcribe_line_audio", {"scene": scene.number}) for scene in scenes]
    )
    run(fake_model, steps)


def draw_for(
    fake_model: FakeModel,
    say: Say,
    steps: HeldSteps,
    scene: Scene,
    *picked: int,
) -> tuple[ToolCall, list[ModelCall]]:
    """The scene agent picks generated pictures for the picture model, through the chat."""
    from .test_producer_broll import calling, run
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    before = len(asked("draw_scene_picture"))
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(
            calls=[
                (
                    "draw_starting_picture",
                    {
                        **PICTURE_FORM,
                        "picks": [
                            {"item": number, "take": "Use this picture's product detail."}
                            for number in picked
                        ],
                    },
                )
            ]
        ),
        turn(calls=[("give_up", {"reason": "Another angle is needed before filming."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": scene.number,
                "instruction": "Draw this scene's planned action.",
                "quotes": ["Go on"],
            },
        ),
    )
    run(fake_model, steps)
    call = ToolCall.objects.filter(
        tool="draw_starting_picture", agent_fix__step__scene=scene
    ).latest("id")
    return call, asked("draw_scene_picture")[before:]


def a_scene(job: Job, number: int) -> Scene:
    return Scene.objects.create(
        job=job,
        number=number,
        line="It cleans in one wash.",
        shows="The bottle on a shelf.",
        broll_kind="showcase",
        person_shown=Scene.PersonShown.NO_FACE,
        fact_checked=True,
    )


@pytest.fixture
def in_the_ads_colour(fake_model: FakeModel, library: Job) -> None:
    """The ad's colour is its photos', and a recolour, when one is made, passes its check."""
    library.product_colour = "amber"
    library.save(update_fields=["product_colour"])


def test_a_made_side_is_sent_only_when_the_scene_agent_picks_it(
    fake_model: FakeModel,
    library: Job,
    say: Say,
    in_the_ads_colour: None,
    steps: HeldSteps,
) -> None:
    fake_model.respond("check_made_picture", PASSES)
    fake_model.respond("label_photo", {**LABELLED, "side": "back", "colour": "amber"})
    making(fake_model, say, references=[1, 2])
    made = library_of(library)[2]
    assert (made.kind, made.side) == ("side", "back")
    chosen, other = a_scene(library, 1), a_scene(library, 2)
    prepare_scenes(fake_model, say, steps, chosen, other)

    call, [drawing] = draw_for(fake_model, say, steps, chosen, made.number)
    assert call.answer == "made"
    assert made.file in drawing.handoff["pictures"]
    call, [drawing] = draw_for(fake_model, say, steps, other, 1)
    assert call.answer == "made"
    assert made.file not in drawing.handoff["pictures"]


def holding_the_part(job: Job, number: int, part: int) -> Scene:
    """Scene `number`, holding the owner part pictured in library item `part`."""
    scene = a_scene(job, number)
    item = job.library.get(number=part)
    scene.owner_part = {"what": "the pump's spare nozzle", "source": "invented", "looks": "Flat."}
    scene.owner_part_picture = item
    from django.utils import timezone

    scene.owner_part_drawn = {
        "part": scene.owner_part,
        "photos": [],
        "file": item.file,
        "shown_at": timezone.now().isoformat(),
    }
    scene.save()
    return scene


def test_a_picture_made_from_an_owner_part_is_drawn_only_by_a_scene_holding_that_part(
    fake_model: FakeModel,
    library: Job,
    say: Say,
    in_the_ads_colour: None,
    steps: HeldSteps,
) -> None:
    file = file_store.save(f"jobs/{library.pk}/part.png", photo(62))
    add_to_library(
        library,
        file,
        PictureLabel.model_validate({**LABELLED, "label": "The pump's spare nozzle."}),
        OwnerPart(part="the pump's spare nozzle"),
    )
    fake_model.respond("check_made_picture", PASSES)
    fake_model.respond("label_photo", {**LABELLED, "colour": "amber"})
    making(fake_model, say, references=[1, 3])
    made = library_of(library)[3]
    assert made.source == Made(references=[1, 3])
    holding, other = holding_the_part(library, 1, part=3), a_scene(library, 2)
    prepare_scenes(fake_model, say, steps, holding, other)
    fake_model.respond(
        "produce", turn(calls=[("approve_owner_part", {"scene": 1})]), turn(says="Approved.")
    )
    say("Yes, that part picture is right.")

    call, drawings = draw_for(fake_model, say, steps, other, made.number)
    assert call.result.startswith("Refused:") and "shows an owner part" in call.result
    assert drawings == []
    call, [drawing] = draw_for(fake_model, say, steps, holding, made.number)
    assert call.answer == "made"
    assert made.file in drawing.handoff["pictures"]


def test_a_reference_with_a_product_only_version_is_drawn_from_that_version(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    first_message(fake_model, say, labelled_page, FRONT, SIDE_ON_A_SHELF)
    job = Job.objects.get()
    front, shelf = library_of(job)
    fake_model.respond("check_made_picture", PASSES)

    making(fake_model, say, references=[1, 2])

    # The shelf photo's product-only version is made for it, as it is first used.
    alone = library_of(job)[2]
    assert alone.source == ProductOnly(of=shelf.number)
    (drawing,) = asked("make_picture")
    assert drawing.handoff["pictures"] == [front.file, alone.file]
    (check,) = asked("check_made_picture")
    assert [image["key"] for image in check.images][1:] == [front.file, alone.file]
    assert library_of(job)[3].source == Made(references=[1, 2])


def test_a_made_picture_that_fails_its_check_is_a_cant_with_the_reason_and_never_stored(
    fake_model: FakeModel, library: Job, say: Say
) -> None:
    fake_model.respond("check_made_picture", fails("The back's label is printed backwards."))
    messages_before = Message.objects.filter(session__jobs=library).count()

    call = making(fake_model, say, references=[1, 2])

    assert [item.number for item in library_of(library)] == [1, 2]
    assert len(asked("make_picture")) == len(asked("check_made_picture")) == 1
    assert call.answer == "cant"
    assert call.result.startswith("Failed:")
    assert "The back's label is printed backwards." in call.result
    # The shop owner is never asked: the chat holds the message and the producer's reply only.
    assert Message.objects.filter(session__jobs=library).count() == messages_before + 2


def test_a_picture_the_picture_models_safety_filter_refuses_is_a_cant(
    fake_model: FakeModel, library: Job, say: Say
) -> None:
    fake_model.respond("make_picture", BlockedBySafetyFilter("moderation_blocked"))

    call = making(fake_model, say, references=[1, 2])

    assert call.answer == "cant"
    assert "safety filter" in call.result
    assert asked("check_made_picture") == []
    assert [item.number for item in library_of(library)] == [1, 2]


def test_a_picture_refused_for_money_with_no_azure_set_up_is_a_cant_saying_so(
    fake_model: FakeModel,
    library: Job,
    say: Say,
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings.AZURE_PICTURES_KEY = ""
    settings.AZURE_PICTURES_BASE_URL = ""
    fake_model.respond("make_picture", RefusedForMoney("insufficient_quota"))

    with caplog.at_level(logging.ERROR, logger="agents.loop"):
        call = making(fake_model, say, references=[1, 2])

    assert call.answer == "cant"
    assert "out of money" in call.result
    # An expected failure, not an unexpected error for the server log.
    assert not [r for r in caplog.records if r.name == "agents.loop"]
    assert [item.number for item in library_of(library)] == [1, 2]


def test_a_reference_the_library_doesnt_hold_is_refused_before_anything_is_paid(
    fake_model: FakeModel, library: Job, say: Say
) -> None:
    call = making(fake_model, say, references=[1, 7])

    assert call.answer == "cant"
    assert call.result.startswith("Refused:")
    assert "7" in call.result
    assert asked("make_picture") == []


def test_a_picture_refused_for_money_is_drawn_on_azure_and_stored(
    fake_model: FakeModel, library: Job, say: Say, settings: Settings, monkeypatch: Any
) -> None:
    settings.AZURE_PICTURES_KEY = "azure-key"
    settings.AZURE_PICTURES_BASE_URL = "https://ours.openai.azure.com/openai/v1/"
    monkeypatch.setattr(gateway, "_wait", lambda _: None)
    fake_model.respond("make_picture", RefusedForMoney("insufficient_quota"))
    fake_model.respond("check_made_picture", PASSES)
    drawn_before = len(fake_model.pictures_drawn_on)

    call = making(fake_model, say, references=[1, 2])

    assert fake_model.pictures_drawn_on[drawn_before:] == ["openai", "azure"]
    assert call.answer == "made"
    assert library_of(library)[2].source == Made(references=[1, 2])


# Just before the item is written, or once it is and before the call's answer is.
STOPS = {
    "before": (pre_save, LibraryItem, lambda row: row.source.get("came_from") == "made"),
    "after": (pre_save, ToolCall, lambda call: call.tool == "make_picture" and bool(call.result)),
}


@pytest.mark.parametrize("stop", STOPS, ids=list(STOPS))
def test_a_worker_stopped_as_the_item_is_stored_pays_for_nothing_twice_and_stores_it_once(
    fake_model: FakeModel, library: Job, say: Say, session_id: str, stop: str
) -> None:
    fake_model.respond("check_made_picture", PASSES)
    fake_model.respond(
        "produce",
        turn(calls=[("make_picture", {"prompt": PROMPT, "references": [1, 2], "label": LABEL})]),
    )
    signal, sender, when = STOPS[stop]
    stored = the_worker_stops(signal, sender, when=when)
    with stored, pytest.raises(WorkerStopped):
        say("Make one picture of the bottle from the front and the back.")
    the_producer_died(session_id)
    fake_model.respond("produce", turn(says="I made it."))

    restart_dead_producers()

    assert [item.number for item in library_of(library)] == [1, 2, 3]
    assert times_paid_for("make_picture") == times_paid_for("check_made_picture") == 1
    assert "library item 3" in ToolCall.objects.get(tool="make_picture").result


def test_a_made_picture_whose_check_gives_no_answer_is_a_cant_and_never_stored(
    fake_model: FakeModel, library: Job, say: Say
) -> None:
    fake_model.respond("check_made_picture", BlockedBySafetyFilter("moderation_blocked"))

    call = making(fake_model, say, references=[1, 2])

    assert call.answer == "cant"
    assert "check couldn't be made" in call.result
    assert [item.number for item in library_of(library)] == [1, 2]


def test_a_picture_with_no_references_is_drawn_from_the_prompt_alone(
    fake_model: FakeModel, library: Job, say: Say
) -> None:
    fake_model.respond("check_made_picture", PASSES)
    prompt = "A plain grey background with soft light from the left, nothing else in it."

    call = making(fake_model, say, references=[], prompt=prompt)

    (drawing,) = asked("make_picture")
    assert drawing.handoff == {"prompt": prompt}
    (check,) = asked("check_made_picture")
    assert len(check.images) == 1
    assert call.answer == "made"
    assert library_of(library)[2].source == Made(references=[])
    assert "made by the producer from its own words" in library_text(library)
