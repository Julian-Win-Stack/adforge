"""A clip's first-send prompt (#210 point 16): the speed line, one label line per picture and
per video of the resolved sent list, in order, and one sentence. The writer is faked at the
gateway; the sent list is the contract's fixture."""

import json
from pathlib import Path
from typing import Any

import pytest

from gateway.fake import FakeModel
from gateway.types import UnusableReply
from jobs.first_send import FirstSendScene, write_first_send_prompt
from jobs.library import PagePhoto, PictureLabel, SentList, add_to_library
from jobs.models import Job

pytestmark = pytest.mark.django_db

SENT = SentList.model_validate(
    json.loads((Path(__file__).parent / "fixtures" / "sent_list.json").read_text())
)
SCENE = FirstSendScene(
    line="Two cloves, one squeeze, and it's done.",
    shows="a hand squeezes two cloves through the press over a bowl and mince comes out",
    broll_kind="does a job",
    usage="squeeze the handles over a bowl",
    result="fresh mince in the bowl",
)
# The writer's labels: one per library picture. The drawn picture's line (Image 7) is code's.
LABELS = [
    "the garlic press, closed, seen from the front",
    "a hand squeezing the press over a bowl: copy the hand and how it holds the press",
    "fresh mince in a small white bowl: the result at the end",
    "two peeled cloves on a board: what goes in",
    "the press open, the chamber empty",
    "a kitchen counter by a window: the place",
    "the garlic press, closed, seen from the back",
    "the garlic press, closed, seen from the side",
]
SENTENCE = "She squeezes two cloves through the red press over a bowl and fresh mince comes out."


def answer(**changes: Any) -> dict[str, Any]:
    return {"labels": LABELS, "sentence": SENTENCE, **changes}


def write(job: Job) -> str:
    return write_first_send_prompt(job, SENT.pictures, SENT.pieces, SCENE)


def a_job(colours: dict[int, str] | None = None) -> Job:
    """A job whose library holds the fixture's 14 items, each red unless `colours` says."""
    job = Job.objects.create(product_url="https://shop.example/press")
    for number in range(1, 15):
        colour = (colours or {}).get(number, "red")
        label = PictureLabel(
            kind="side", side="front", alone="yes", colour=colour, label=f"Item {number}."
        )
        add_to_library(job, f"jobs/x/item-{number}.png", label, PagePhoto(photo=number))
    return job


def test_the_prompt_is_the_speed_line_a_label_per_picture_and_piece_in_order_and_one_sentence(
    fake_model: FakeModel,
) -> None:
    fake_model.respond("write_first_send_prompt", answer())

    prompt = write(a_job())

    assert prompt.split("\n") == [
        "Film this in real-time speed.",
        "Image 1 is the garlic press, closed, seen from the front.",
        "Image 2 is a hand squeezing the press over a bowl: copy the hand and how it holds the "
        "press.",
        "Image 3 is fresh mince in a small white bowl: the result at the end.",
        "Image 4 is two peeled cloves on a board: what goes in.",
        "Image 5 is the press open, the chamber empty.",
        "Image 6 is a kitchen counter by a window: the place.",
        "Image 7 is the woman and the room: she is the person in the clip and it is the "
        "background.",
        "Image 8 is the garlic press, closed, seen from the back.",
        "Image 9 is the garlic press, closed, seen from the side.",
        "Video 1 is only for the movement, not the look.",
        "Video 2 is only for the movement, not the look.",
        SENTENCE,
    ]


def test_the_writer_is_told_the_scene_and_each_picture_by_its_label_and_what_it_is_for(
    fake_model: FakeModel,
) -> None:
    asked: list[Any] = []

    def answering(request: Any) -> dict[str, Any]:
        asked.append(request)
        return answer()

    fake_model.answer_unscripted("write_first_send_prompt", answering)

    write(a_job())

    (request,) = asked
    told = request.handoff.model_dump(mode="json")
    assert told["scene"]["shows"] == SCENE.shows
    assert told["scene"]["result"] == "fresh mince in the bowl"
    assert told["videos"] == 2
    assert told["pictures"][:3] == [
        {
            "image": 1,
            "label": "The garlic press, closed, seen from the front.",
            "colour": "red",
            "used_for": None,
        },
        {
            "image": 2,
            "label": "A hand squeezing the press over a bowl.",
            "colour": "red",
            "used_for": "in-use",
        },
        {
            "image": 3,
            "label": "Fresh mince in a small white bowl.",
            "colour": "red",
            "used_for": "result",
        },
    ]
    assert [picture["image"] for picture in told["pictures"]] == [1, 2, 3, 4, 5, 6, 8, 9]
    assert told["drawn"] == (
        "Image 7 is the woman and the room: she is the person in the clip and it is the background."
    )


def test_the_drawn_pictures_line_is_codes_fixed_text_whatever_the_writer_says(
    fake_model: FakeModel,
) -> None:
    room = SENT.pictures[6].model_copy(update={"drawn": "room"})
    fake_model.respond("write_first_send_prompt", answer(labels=LABELS[:2]))

    prompt = write_first_send_prompt(a_job(), [*SENT.pictures[:2], room], [], SCENE)

    assert prompt.split("\n")[3] == "Image 7 is the room: it is the background."


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param({"labels": LABELS[:-1]}, id="a picture with no label"),
        pytest.param({"labels": [*LABELS, "one more"]}, id="a label for no picture"),
        pytest.param({"labels": [*LABELS[:-1], "the back\nand the side"]}, id="two-line label"),
        pytest.param({"sentence": f"{SENTENCE} Then she smiles."}, id="two sentences"),
        pytest.param({"sentence": "She squeezes the press"}, id="no full stop"),
        pytest.param({"sentence": "She squeezes.\nThe mince comes out."}, id="two lines"),
        pytest.param({"sentence": "She squeezes the press\nover a bowl."}, id="broken line"),
        pytest.param({"sentence": " "}, id="no sentence"),
    ],
)
def test_an_answer_that_breaks_the_prompts_shape_is_refused(
    fake_model: FakeModel, broken: dict[str, Any]
) -> None:
    fake_model.respond("write_first_send_prompt", answer(**broken))

    with pytest.raises(UnusableReply):
        write(a_job())


def test_a_clip_sent_no_piece_has_no_video_line(fake_model: FakeModel) -> None:
    fake_model.respond("write_first_send_prompt", answer(labels=LABELS[:2]))

    prompt = write_first_send_prompt(a_job(), SENT.pictures[:2], [], SCENE)

    assert prompt.split("\n") == [
        "Film this in real-time speed.",
        "Image 1 is the garlic press, closed, seen from the front.",
        "Image 2 is a hand squeezing the press over a bowl: copy the hand and how it holds the "
        "press.",
        SENTENCE,
    ]


def test_a_label_the_writer_ends_with_a_full_stop_gets_only_one(fake_model: FakeModel) -> None:
    fake_model.respond("write_first_send_prompt", answer(labels=[f"{label}." for label in LABELS]))

    prompt = write(a_job())

    assert prompt.split("\n")[1] == "Image 1 is the garlic press, closed, seen from the front."


def test_a_picture_whose_label_names_another_colour_is_written_in_its_items_colour(
    fake_model: FakeModel,
) -> None:
    # Item 14 is item 1 recoloured: its label still says the colour it was in before.
    job = a_job({14: "green"})
    black = SENT.pictures[0].model_copy(
        update={"label": "The black garlic press, closed, seen from the front."}
    )
    told: list[Any] = []

    def a_writer_that_follows_its_rules(request: Any) -> dict[str, Any]:
        # As a writer told the rule does: the picture's colour, not the label's colour word.
        (picture,) = request.handoff.pictures
        told.append(request.instructions)
        line = picture.label.lower().rstrip(".").replace("black", picture.colour)
        return {"labels": [line], "sentence": SENTENCE}

    fake_model.answer_unscripted("write_first_send_prompt", a_writer_that_follows_its_rules)

    prompt = write_first_send_prompt(job, [black], [], SCENE)

    assert (
        prompt.split("\n")[1] == "Image 1 is the green garlic press, closed, seen from the front."
    )
    assert "never a colour word from its label" in told[0]
