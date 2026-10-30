"""The product-only version of each look photo that shows other things (spec #210 point 6,
#273 points 5-7): a page photo of kind side or state with alone = no, or any line-up, is cut
out when it is first sent, never when the page is read, once, by the redraw with its check,
and enters the library beside the original, pointing at it. Before it is cut, the helper ask
names the library pictures that show a part the photo hides. A cut-out that can't be had for
good is Unusable, kept and never tried again; another picture step then sends the photo
whole, in red, never asking the owner. The page is read through the chat: the owner's first
message, the picker, the picture model and its check faked at the gateway, the shop's page on
the local web server through the stand-in Firecrawl."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from adforge import file_store
from adforge.retry import OutsideServiceDown
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter, ModelRequest
from jobs import cut_out, product_only
from jobs.library import (
    LibraryEntry,
    OwnerPhoto,
    PagePhoto,
    PictureLabel,
    ProductOnly,
    add_to_library,
    library_of,
)
from jobs.library_items import originals, version_of
from jobs.models import Job
from jobs.product_only import Unusable, for_clip, to_send

from .conftest import CUT_OUT_OK, LABELLED, READABLE, SHAMPOO_PICKED, photo, recorded
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
    """The red warnings recorded for the ad: a failed cut-out is only the developer's, so
    the shop owner's chat never shows it."""
    assert not [text for level, text in notices(api, session_id) if level == "problem"]
    return [text for level, text in recorded(api, session_id) if level == "problem"]


def the_side_photo(job: Job) -> LibraryEntry:
    (item,) = [item for item in library_of(job) if item.kind == "side" and item.alone == "no"]
    return item


def test_a_side_photo_with_other_things_is_cut_out_when_first_sent_and_kept_beside_it(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    first_message(fake_model, say, labelled_page, FRONT, SIDE_ON_A_SHELF, IN_USE)
    job = Job.objects.get()
    # Reading the page cuts nothing out.
    assert asked("make_product_only") == []
    side = the_side_photo(job)

    version = to_send(job, side)

    items = library_of(job)
    assert [(item.number, item.source) for item in items] == [
        (1, PagePhoto(photo=1)),
        (2, PagePhoto(photo=2)),
        (3, PagePhoto(photo=3)),
        (4, ProductOnly(of=2)),
    ]
    assert version == items[3]
    # Cut out once by the redraw, from the side photo alone, as nothing of it is hidden,
    # told what it shows and to keep only the product.
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
        "hidden_part": None,
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
    # Sent again, it is the same version, paid for once; the rest go whole.
    assert for_clip(job, side) == version
    assert to_send(job, items[0]) == items[0]
    assert to_send(job, items[2]) == items[2]
    assert len(asked("make_product_only")) == 1
    assert len(asked("pick_cut_out_helpers")) == 1


def test_a_line_up_gets_a_version_keeping_only_its_target(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    first_message(fake_model, say, labelled_page, FRONT, LINE_UP)
    job = Job.objects.get()

    for_clip(job, library_of(job)[1])

    assert [item.source for item in library_of(job)][2:] == [ProductOnly(of=2)]
    keep = f"the product that is {LINE_UP['target']}"
    (drawing,) = asked("make_product_only")
    assert f"A clean photo of only {keep} in this photo" in drawing.handoff["prompt"]
    (check,) = asked("check_cut_out")
    assert check.handoff["keep"] == keep


def test_a_cut_out_that_keeps_failing_cant_be_had_and_another_picture_step_sends_it_whole(
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
    side = the_side_photo(job)

    made = for_clip(job, side)

    # Drawn once and redrawn twice, each cut-out checked once.
    assert made == Unusable(
        "cutting the product out of it failed: all 3 tries failed (A towel is still beside "
        "the bottle. The label's print is changed. The cap is missing.)"
    )
    assert len(asked("make_product_only")) == 3
    assert len(asked("check_cut_out")) == 3
    assert [item.source.came_from for item in library_of(job)] == ["page_photo"] * 2
    assert problems(api, session_id) == []

    # Another picture step sends it whole, said in red once.
    assert to_send(job, side) == side
    assert to_send(job, side) == side
    said = (
        f"Library item 2 ({side.label}) goes whole to a picture made from it, with what else "
        f"it shows: its product-only version can't be had ({made.why}). The picture made "
        "from it may copy those other things."
    )
    assert problems(api, session_id) == [said]
    job.refresh_from_db()
    assert [warning["text"] for warning in job.warnings] == [said]
    # Nothing is tried again, and nothing asks the owner for another photo.
    assert len(asked("make_product_only")) == 3
    assert not job.photos.filter(source_url="").exists()


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
    job = Job.objects.get()

    for_clip(job, the_side_photo(job))

    assert len(refusals) == 1
    assert [item.source for item in library_of(job)][2:] == [ProductOnly(of=2)]


def test_a_picture_model_refusing_twice_cant_be_had(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    def refusing(**asked: Any) -> Any:
        raise BlockedBySafetyFilter("content_policy_violation")

    fake_model.edit = refusing  # type: ignore[method-assign]
    first_message(fake_model, say, labelled_page, FRONT, SIDE_ON_A_SHELF)
    job = Job.objects.get()

    made = for_clip(job, the_side_photo(job))

    assert len(asked("make_product_only")) == 2
    assert asked("check_cut_out") == []
    assert made == Unusable(
        "cutting the product out of it failed: the picture model refused to draw it 2 times "
        "(content_policy_violation)"
    )


def test_a_cut_out_whose_check_cant_see_the_photo_cant_be_had(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    """Without the photo, nothing can say the product is unchanged: the cut-out isn't checked
    on its own, and isn't used."""

    def refusing(request: ModelRequest[Any]) -> dict[str, Any]:
        raise BlockedBySafetyFilter("content_policy_violation")

    fake_model.answer_unscripted("check_cut_out", refusing)
    first_message(fake_model, say, labelled_page, FRONT, SIDE_ON_A_SHELF)
    job = Job.objects.get()

    made = for_clip(job, the_side_photo(job))

    assert len(asked("make_product_only")) == 1
    assert made == Unusable(
        "cutting the product out of it failed: its check couldn't be made (the AI model's "
        "safety filter refused to look at it)"
    )


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"kind": "in-use", "side": "none"}, id="in-use"),
        pytest.param({"kind": "result", "side": "none"}, id="result"),
        pytest.param({"kind": "content", "side": "none"}, id="content"),
        pytest.param({"kind": "chart", "side": "none"}, id="chart"),
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
    job = Job.objects.get()
    photo_2 = library_of(job)[1]

    assert for_clip(job, photo_2) == photo_2
    assert [item.source.came_from for item in library_of(job)] == ["page_photo"] * 2
    assert asked("make_product_only") == []
    assert asked("pick_cut_out_helpers") == []


def test_a_state_photo_with_other_things_gets_one_too(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    first_message(fake_model, say, labelled_page, FRONT, {**SIDE_ON_A_SHELF, "kind": "state"})
    job = Job.objects.get()

    for_clip(job, library_of(job)[1])

    assert [item.source for item in library_of(job)][2:] == [ProductOnly(of=2)]


def test_a_picture_model_down_sends_it_whole_for_now_and_it_is_tried_again_next_send(
    api: APIClient,
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    session_id: str,
    say: Say,
) -> None:
    original = fake_model.edit

    def down(**asked: Any) -> Any:
        raise OutsideServiceDown("the picture service answered 503")

    fake_model.edit = down  # type: ignore[method-assign]
    first_message(fake_model, say, labelled_page, FRONT, SIDE_ON_A_SHELF)
    job = Job.objects.get()
    side = the_side_photo(job)

    assert for_clip(job, side) == side

    said = (
        f"Cutting the product out of library item 2 ({side.label}) didn't work this time (the "
        "AI service didn't answer), so for now the photo is sent whole, with what else it "
        "shows. It is tried again the next time the photo is sent."
    )
    assert problems(api, session_id) == [said]

    fake_model.edit = original  # type: ignore[method-assign]
    for_clip(job, side)

    assert [item.source for item in library_of(job)][2:] == [ProductOnly(of=2)]
    assert len(asked("pick_cut_out_helpers")) == 1


def test_a_check_down_once_is_asked_again_next_time_without_drawing_again(
    api: APIClient,
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    session_id: str,
    say: Say,
) -> None:
    # Down through every retry of the one ask.
    fake_model.respond(
        "check_cut_out", *[OutsideServiceDown("the vision service answered 503")] * 3
    )
    first_message(fake_model, say, labelled_page, FRONT, SIDE_ON_A_SHELF)
    job = Job.objects.get()
    side = the_side_photo(job)

    assert for_clip(job, side) == side
    (said,) = problems(api, session_id)
    assert "didn't work this time (the AI service checking it didn't answer)" in said

    for_clip(job, side)

    assert [item.source for item in library_of(job)][2:] == [ProductOnly(of=2)]
    # The cut-out paid for is checked again, not drawn again.
    assert len(asked("make_product_only")) == 1


def test_a_sender_chooses_among_the_originals_and_sends_each_ones_version(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    first_message(fake_model, say, labelled_page, FRONT, SIDE_ON_A_SHELF)
    job = Job.objects.get()
    chosen = originals(job)
    assert [version_of(job, item).number for item in chosen] == [1, 2]

    for_clip(job, chosen[1])

    # The product-only version is never chosen on its own: only through its original.
    assert [item.number for item in originals(job)] == [1, 2]
    assert [version_of(job, item).number for item in chosen] == [1, 3]


def test_a_hidden_part_no_picture_shows_draws_nothing_and_is_asked_once(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    fake_model.respond(
        "pick_cut_out_helpers", {"covered": True, "covered_part": "its pump", "helpers": []}
    )
    first_message(fake_model, say, labelled_page, FRONT, SIDE_ON_A_SHELF)
    job = Job.objects.get()
    side = the_side_photo(job)

    made = for_clip(job, side)

    assert made == Unusable("its pump is hidden in the photo and no other photo shows it")
    assert asked("make_product_only") == []
    assert for_clip(job, side) == made
    assert len(asked("pick_cut_out_helpers")) == 1


def test_the_helper_ask_sees_the_library_and_its_picks_are_kept_to_real_others_up_to_the_most(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Fewer than the page holds, so the cap is reached.
    monkeypatch.setattr(product_only, "MOST_HELPERS", 3)
    others = [
        label(f"I{n}", n, "in-use", "none", f"A hand with the bottle, take {n}.", alone="no")
        for n in (101, 103, 104, 105)
    ]
    first_message(fake_model, say, labelled_page, FRONT, SIDE_ON_A_SHELF, *others)
    job = Job.objects.get()
    side = the_side_photo(job)
    library = library_of(job)
    numbers = [item.number for item in library]
    # A number not in the library, the photo itself, a number twice and more than the most.
    fake_model.respond(
        "pick_cut_out_helpers",
        {
            "covered": True,
            "covered_part": "its cap",
            "helpers": [99, side.number, 1, 1, *numbers[2:], *numbers[2:], 1],
        },
    )

    for_clip(job, side)

    kept = [1, *numbers[2:]][:3]
    (drawing,) = asked("make_product_only")
    files = {item.number: item.file for item in library}
    assert drawing.handoff["pictures"] == [side.file, *(files[n] for n in kept)]
    (helper_ask,) = asked("pick_cut_out_helpers")
    assert helper_ask.images[0]["key"] == side.file
    assert len(helper_ask.images) > 1
    assert helper_ask.handoff["photo_label"] == side.label


def test_helpers_named_for_a_photo_that_hides_nothing_are_not_given(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    fake_model.respond(
        "pick_cut_out_helpers", {"covered": False, "covered_part": "", "helpers": [1]}
    )
    first_message(fake_model, say, labelled_page, FRONT, SIDE_ON_A_SHELF)
    job = Job.objects.get()
    side = the_side_photo(job)

    for_clip(job, side)

    (drawing,) = asked("make_product_only")
    assert drawing.handoff["pictures"] == [side.file]


def test_another_photos_cut_out_may_be_a_helper(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    line_up_on_a_shelf = {**LINE_UP, "picture": "I105"}
    first_message(fake_model, say, labelled_page, FRONT, SIDE_ON_A_SHELF, line_up_on_a_shelf)
    job = Job.objects.get()
    line_up = library_of(job)[2]
    their_cut = for_clip(job, line_up)
    assert isinstance(their_cut, LibraryEntry)
    fake_model.respond(
        "pick_cut_out_helpers",
        {"covered": True, "covered_part": "its cap", "helpers": [their_cut.number]},
    )

    for_clip(job, the_side_photo(job))

    drawing = asked("make_product_only")[-1]
    assert drawing.handoff["pictures"][1:] == [their_cut.file]


def test_the_helper_ask_is_kept_so_a_cut_out_tried_again_asks_it_once(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    original = fake_model.edit

    def down(**asked: Any) -> Any:
        raise OutsideServiceDown("the picture service answered 503")

    fake_model.edit = down  # type: ignore[method-assign]
    first_message(fake_model, say, labelled_page, FRONT, SIDE_ON_A_SHELF)
    job = Job.objects.get()
    side = the_side_photo(job)
    for_clip(job, side)
    # The library grows before the photo is sent again, so its sheets are new.
    add_to_library(
        job,
        file_store.save(f"jobs/{job.pk}/more.png", photo(77)),
        PictureLabel.model_validate({**LABELLED, "label": "Another look at the bottle."}),
        OwnerPhoto(original=f"jobs/{job.pk}/more.png"),
    )
    fake_model.edit = original  # type: ignore[method-assign]

    for_clip(job, side)

    assert len(asked("pick_cut_out_helpers")) == 1


def test_a_cut_out_whose_label_cant_be_had_goes_whole_for_now_and_is_tried_again(
    api: APIClient,
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    session_id: str,
    say: Say,
) -> None:
    first_message(fake_model, say, labelled_page, FRONT, SIDE_ON_A_SHELF)
    job = Job.objects.get()
    side = the_side_photo(job)
    fake_model.respond("label_photo", OutsideServiceDown("down"), OutsideServiceDown("down"))
    fake_model.respond("label_photo", OutsideServiceDown("down"))

    assert for_clip(job, side) == side
    assert any(
        "didn't work this time (the cut-out couldn't be labelled)" in text
        for text in problems(api, session_id)
    )

    made = for_clip(job, side)

    assert made != side and isinstance(made, LibraryEntry)
    assert len(asked("make_product_only")) == 1
