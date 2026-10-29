"""The product-only version of each look photo that shows other things (spec #210 point 6):
a page photo of kind side or state with alone = no, or any line-up, is cut out once by the
redraw with its check, asked once, and enters the library beside the original, pointing at
it. A cut-out that can't be had leaves the original to be sent whole, in red, never asking
the owner. Driven through the chat: the owner's first message, the picker, the picture model
and its check faked at the gateway, the shop's page on the local web server through the
stand-in Firecrawl."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter, ModelRequest
from jobs import cut_out
from jobs.library import LibraryEntry, PagePhoto, ProductOnly, library_of
from jobs.library_items import add_kept_photos, to_send
from jobs.models import Job

from .conftest import CUT_OUT_OK, LABELLED, READABLE, SHAMPOO_PICKED
from .test_picker_labels import LABELS, label, labelled_page, notices  # noqa: F401 (a fixture)

pytestmark = pytest.mark.django_db(transaction=True)

Say = Callable[..., None]

# The front alone, the left side on a bathroom shelf with a towel, the product in use, and
# a line-up of three bottles.
SIDE_ON_A_SHELF = label(
    "I102", 3, "side", "side", "The amber bottle from its left side, a towel beside it.", alone="no"
)
IN_USE = next(picked for picked in LABELS if picked["picture"] == "I104")
LINE_UP = next(picked for picked in LABELS if picked["picture"] == "I105")
FRONT = next(picked for picked in LABELS if picked["picture"] == "I8")


def picked(*pictures: dict[str, Any]) -> dict[str, Any]:
    """The picker's answer keeping `pictures`, in their order."""
    return {
        **SHAMPOO_PICKED,
        "gallery_images": [int(picture["picture"][1:]) for picture in pictures],
        "pictures": list(pictures),
    }


def first_message(fake_model: FakeModel, say: Say, link: str, *pictures: dict[str, Any]) -> None:
    """The owner's first message: the page's link, read, the picker keeping `pictures`."""
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": link, "target_seconds": None})]),
        turn(says="Done."),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("pick_photos", picked(*pictures))
    say(f"Make an ad for {link}.")


def failing(problem: str) -> dict[str, Any]:
    return {**CUT_OUT_OK, "verdict": "fail", "problem": problem}


def asked(purpose: str) -> list[ModelCall]:
    return list(ModelCall.objects.filter(purpose=purpose).order_by("id"))


def problems(api: APIClient, session_id: str) -> list[str]:
    return [text for level, text in notices(api, session_id) if level == "problem"]


def the_side_photo(job: Job) -> LibraryEntry:
    (item,) = [item for item in library_of(job) if item.kind == "side" and item.alone == "no"]
    return item


def test_a_side_photo_with_other_things_gets_its_product_only_version_beside_it(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    first_message(fake_model, say, labelled_page, FRONT, SIDE_ON_A_SHELF, IN_USE)

    job = Job.objects.get()
    items = library_of(job)
    assert [(item.number, item.source) for item in items] == [
        (1, PagePhoto(photo=1)),
        (2, PagePhoto(photo=2)),
        (3, PagePhoto(photo=3)),
        # The side photo's product-only version; the front is alone already, and an in-use
        # photo is sent whole.
        (4, ProductOnly(of=2)),
    ]
    side, version = items[1], items[3]
    # Cut out once by the redraw, from the side photo alone, told what it shows and to keep
    # only the product.
    (drawing,) = asked("make_product_only")
    assert drawing.handoff["pictures"] == [side.file]
    assert drawing.handoff["prompt"].startswith(f"The photo shows: {side.label} ")
    assert "A clean photo of only the product in this photo" in drawing.handoff["prompt"]
    assert drawing.output == {"file": version.file}
    # Its check is asked once, shown the cut-out first and the photo beside it.
    (check,) = asked("check_cut_out")
    assert [image["key"] for image in check.images] == [version.file, side.file]
    assert check.handoff == {
        "keep": "the product",
        "photo_label": side.label,
        "pictures": cut_out.PICTURES_SHOWN,
    }
    # Labelled as the picker would label it, by the labelling call.
    assert (version.kind, version.alone, version.label) == (
        LABELLED["kind"],
        LABELLED["alone"],
        LABELLED["label"],
    )
    (labelling,) = asked("label_photo")
    assert labelling.images[0]["key"] == version.file
    # Whatever sends the side photo sends its product-only version; the rest go whole.
    assert to_send(job, side) == version
    assert to_send(job, items[0]) == items[0]
    assert to_send(job, items[2]) == items[2]


def test_a_line_up_gets_a_version_keeping_only_its_target(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    first_message(fake_model, say, labelled_page, FRONT, LINE_UP)

    job = Job.objects.get()
    assert [item.source for item in library_of(job)][2:] == [ProductOnly(of=2)]
    keep = f"the product that is {LINE_UP['target']}"
    (drawing,) = asked("make_product_only")
    assert f"A clean photo of only {keep} in this photo" in drawing.handoff["prompt"]
    (check,) = asked("check_cut_out")
    assert check.handoff["keep"] == keep


def test_a_cut_out_that_keeps_failing_sends_the_original_in_red_and_never_asks_the_owner(
    api: APIClient,
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    session_id: str,
    say: Say,
) -> None:
    fake_model.respond(
        "check_cut_out",
        failing("A towel is still beside the bottle."),
        failing("The label's print is changed."),
        failing("The cap is missing."),
    )

    first_message(fake_model, say, labelled_page, FRONT, SIDE_ON_A_SHELF)

    job = Job.objects.get()
    # Drawn once and redrawn twice, each cut-out checked once.
    assert len(asked("make_product_only")) == 3
    assert len(asked("check_cut_out")) == 3
    assert [item.source.came_from for item in library_of(job)] == ["page_photo"] * 2
    side = the_side_photo(job)
    assert to_send(job, side) == side
    said = (
        f"Cutting the product out of library item 2 ({side.label}) failed: all 3 cut-outs "
        "drawn failed their check (A towel is still beside the bottle. The label's print is "
        "changed. The cap is missing.), so the photo is sent whole wherever it is sent, with "
        "what else it shows. The video may copy those other things."
    )
    assert problems(api, session_id) == [said]
    assert [warning["text"] for warning in job.warnings] == [said]
    # Nothing asks the owner for another photo.
    assert not job.photos.filter(source_url="").exists()
    assert "photo" not in api.get(f"/api/sessions/{session_id}/messages/").json()[-1]["text"]

    # Photos added again try nothing again, and say nothing again.
    add_kept_photos(job)
    assert len(asked("make_product_only")) == 3
    assert len(asked("check_cut_out")) == 3
    assert problems(api, session_id) == [said]


def test_a_picture_model_refusal_is_retried_once(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    original = fake_model.edit
    refusals: list[str] = []

    def refusing_once(**asked: Any) -> Any:
        if not refusals:
            refusals.append(asked["prompt"])
            raise BlockedBySafetyFilter("refused")
        return original(**asked)

    fake_model.edit = refusing_once  # type: ignore[method-assign]

    first_message(fake_model, say, labelled_page, FRONT, SIDE_ON_A_SHELF)

    assert len(refusals) == 1
    assert [item.source for item in library_of(Job.objects.get())][2:] == [ProductOnly(of=2)]


def test_a_picture_model_refusing_twice_sends_the_original_in_red(
    api: APIClient,
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    session_id: str,
    say: Say,
) -> None:
    def refusing(**asked: Any) -> Any:
        raise BlockedBySafetyFilter("content_policy_violation")

    fake_model.edit = refusing  # type: ignore[method-assign]

    first_message(fake_model, say, labelled_page, FRONT, SIDE_ON_A_SHELF)

    job = Job.objects.get()
    assert len(asked("make_product_only")) == 2
    assert asked("check_cut_out") == []
    side = the_side_photo(job)
    assert to_send(job, side) == side
    (said,) = problems(api, session_id)
    assert said.startswith(
        f"Cutting the product out of library item 2 ({side.label}) failed: the picture model "
        "refused to draw it 2 times (content_policy_violation), so the photo is sent whole"
    )
    assert said in [warning["text"] for warning in job.warnings]


def test_a_cut_out_whose_check_cant_see_the_photo_sends_the_original_in_red(
    api: APIClient,
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    session_id: str,
    say: Say,
) -> None:
    """Without the photo, nothing can say the product is unchanged: the cut-out isn't checked
    on its own, and isn't used."""

    def refusing(request: ModelRequest[Any]) -> dict[str, Any]:
        raise BlockedBySafetyFilter("content_policy_violation")

    fake_model.answer_unscripted("check_cut_out", refusing)

    first_message(fake_model, say, labelled_page, FRONT, SIDE_ON_A_SHELF)

    job = Job.objects.get()
    assert len(asked("make_product_only")) == 1
    side = the_side_photo(job)
    assert to_send(job, side) == side
    (said,) = problems(api, session_id)
    assert said.startswith(
        f"Cutting the product out of library item 2 ({side.label}) failed: its check couldn't "
        "be made (the AI model's safety filter refused to look at it), so the photo is sent whole"
    )


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"kind": "in-use", "side": "none"}, id="in-use"),
        pytest.param({"kind": "result", "side": "none"}, id="result"),
        pytest.param({"kind": "content", "side": "none"}, id="content"),
        pytest.param({"kind": "side", "alone": "yes"}, id="side, alone"),
        pytest.param({"kind": "state", "alone": "yes"}, id="state, alone"),
    ],
)
def test_photos_code_sends_whole_get_no_version(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
    fields: dict[str, str],
) -> None:
    first_message(fake_model, say, labelled_page, FRONT, {**SIDE_ON_A_SHELF, **fields})

    assert [item.source.came_from for item in library_of(Job.objects.get())] == ["page_photo"] * 2
    assert asked("make_product_only") == []
    assert asked("check_cut_out") == []


def test_a_state_photo_with_other_things_gets_one_too(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    first_message(fake_model, say, labelled_page, FRONT, {**SIDE_ON_A_SHELF, "kind": "state"})

    assert [item.source for item in library_of(Job.objects.get())][2:] == [ProductOnly(of=2)]
