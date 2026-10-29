"""The shapes spec #210's build shares, owned by jobs.library: the library item, the video
moment and the sent list. Each goes in and comes back through its schema, and a wrong fixed
value is refused at the boundary. Nothing reads these shapes yet."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from jobs.library import (
    LookFrame,
    Made,
    OwnerPart,
    OwnerPhoto,
    PagePhoto,
    PictureLabel,
    ProductOnly,
    Recoloured,
    SentList,
    Source,
    VideoWatch,
    WatchedMoment,
    add_to_library,
    keep_sent_list,
    keep_watch,
    library_of,
    number_moments,
    sent_list_of,
    watch_of,
)
from jobs.models import Job, LibraryItem, ProducedItem, ProductVideo

pytestmark = pytest.mark.django_db

FRONT = PictureLabel(
    kind="side",
    side="front",
    alone="yes",
    colour="red",
    label="The garlic press, closed, seen from the front.",
)
IN_USE = PictureLabel(
    kind="in-use",
    side="none",
    alone="no",
    colour="red",
    label="A hand squeezing the press over a bowl.",
)


def a_job() -> Job:
    return Job.objects.create(product_url="https://shop.example/press")


def test_library_numbers_run_in_the_order_items_enter_and_come_back_as_they_went_in() -> None:
    job = a_job()
    add_to_library(job, "jobs/x/photo-2.jpg", IN_USE, PagePhoto(photo=2))
    add_to_library(job, "jobs/x/photo-1.jpg", FRONT, PagePhoto(photo=1))

    library = library_of(job)

    assert [(item.number, item.label, item.source) for item in library] == [
        (1, IN_USE.label, PagePhoto(photo=2)),
        (2, FRONT.label, PagePhoto(photo=1)),
    ]
    assert library[1].model_dump(include=set(PictureLabel.model_fields)) == FRONT.model_dump()
    assert library[0].file == "jobs/x/photo-2.jpg"


@pytest.mark.parametrize(
    "wrong",
    [
        {"kind": "front"},
        {"side": "left"},
        {"alone": True},
        {"colour": "light blue"},
        {"colour": ""},
        {"label": ""},
        {"kind": "line-up", "target": ""},
        {"target": "the second from the left"},
        {"shade": "red"},
    ],
)
def test_a_wrong_fixed_value_is_refused_at_the_boundary(wrong: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PictureLabel.model_validate({**FRONT.model_dump(), **wrong})


def test_a_line_up_names_its_target_and_a_colour_is_kept_as_one_lowercase_word() -> None:
    line_up = PictureLabel.model_validate(
        {
            **FRONT.model_dump(),
            "kind": "line-up",
            "colour": " Red ",
            "target": "the second from the left",
        }
    )

    assert (line_up.colour, line_up.target) == ("red", "the second from the left")


def test_a_row_kept_before_a_value_left_its_set_still_reads_back_with_that_value() -> None:
    """A fixed value set is changed in one line: rows already kept with an old value stay
    readable, while a new record with that value is still refused."""
    job = a_job()
    add_to_library(job, "jobs/x/photo-1.jpg", FRONT, PagePhoto(photo=1))
    LibraryItem.objects.filter(job=job).update(side="left")

    assert library_of(job)[0].model_dump()["side"] == "left"
    with pytest.raises(ValidationError):
        add_to_library(
            job, "jobs/x/photo-2.jpg", FRONT.model_copy(update={"side": "left"}), PagePhoto(photo=2)
        )


def test_every_source_comes_back_as_it_went_in() -> None:
    job = a_job()
    red_frame = FRONT.model_copy(update={"label": "The press open, from the video."})
    black = FRONT.model_copy(update={"colour": "black"})
    sources: list[tuple[PictureLabel, Source]] = [
        (FRONT, PagePhoto(photo=1)),
        (IN_USE, OwnerPhoto(original="jobs/x/owner/hand.jpg")),
        (red_frame, LookFrame(video=1, second=4.0)),
        (FRONT, OwnerPart(part="the handle's underside")),
        (FRONT, ProductOnly(of=2)),
        (black, Recoloured(of=1, colour="black")),
        (FRONT, Made(references=[1, 3])),
        (FRONT, Made(references=[])),
    ]
    for number, (label, source) in enumerate(sources, start=1):
        add_to_library(job, f"jobs/x/item-{number}.jpg", label, source)

    assert [item.source for item in library_of(job)] == [source for _, source in sources]


@pytest.mark.parametrize(
    "source",
    [
        ProductOnly(of=2),
        ProductOnly(of=3),
        ProductOnly.model_construct(of=0),
        Recoloured(of=1, colour="black"),
        Made(references=[1, 2]),
        Made.model_construct(references=[0]),
    ],
    ids=[
        "of itself",
        "of a later item",
        "of no item",
        "recoloured to another colour",
        "made from itself",
        "made from no item",
    ],
)
def test_a_version_of_another_item_must_name_an_earlier_item_and_its_own_colour(
    source: ProductOnly | Recoloured | Made,
) -> None:
    job = a_job()
    add_to_library(job, "jobs/x/item-1.jpg", FRONT, PagePhoto(photo=1))

    with pytest.raises(ValueError):
        add_to_library(job, "jobs/x/item-2.jpg", FRONT, source)

    assert job.library.count() == 1


SQUEEZE = WatchedMoment(
    start=6.0,
    end=11.0,
    happens="The press squeezes two cloves into a bowl.",
    on_screen="The press closed in one hand, mince coming out the holes.",
)
OPEN = WatchedMoment(
    start=1.0,
    end=4.5,
    happens="The press is opened and two cloves go in.",
    on_screen="The press open, two hands, the cloves in the chamber.",
)


def a_watch(**changes: Any) -> dict[str, Any]:
    """A video's watch as it is stored, with `changes`."""
    return {
        "moments": [
            {"number": 1, **OPEN.model_dump()},
            {"number": 2, **SQUEEZE.model_dump()},
        ],
        "look_frames": [2.0, 9.0],
        "colour": "red",
        "transcript": "Two cloves, one squeeze.",
        **changes,
    }


def test_moments_are_numbered_per_video_in_time_order() -> None:
    moments = number_moments([SQUEEZE, OPEN])

    assert [(moment.number, moment.start) for moment in moments] == [(1, 1.0), (2, 6.0)]


def test_a_video_watch_is_kept_on_its_video_and_comes_back_as_it_went_in() -> None:
    job = a_job()
    video = ProductVideo.objects.create(job=job, position=1, kind=ProductVideo.Kind.VIDEO)
    watch = VideoWatch.model_validate(a_watch())

    keep_watch(video, watch)

    assert watch_of(ProductVideo.objects.get(pk=video.pk)) == watch
    assert watch_of(ProductVideo.objects.create(job=job, position=2, kind="video")) is None


def test_a_moment_over_the_video_apis_limit_is_still_a_moment_for_the_cut_to_trim() -> None:
    long = {"number": 1, **OPEN.model_dump(), "start": 0.0, "end": 20.0}

    assert VideoWatch.model_validate(a_watch(moments=[long])).moments[0].end == 20.0


@pytest.mark.parametrize(
    "wrong",
    [
        {"moments": [{"number": 1, **OPEN.model_dump(), "end": 1.0}]},
        {"moments": [{"number": 1, **OPEN.model_dump(), "start": -1.0}]},
        {"moments": [{"number": 2, **OPEN.model_dump()}]},
        {
            "moments": [
                {"number": 1, **SQUEEZE.model_dump()},
                {"number": 2, **OPEN.model_dump()},
            ]
        },
        {"moments": [{"number": 1, **OPEN.model_dump(), "happens": ""}]},
        {"look_frames": [-2.0]},
        {"colour": "dark red"},
        {"seen": "a press"},
    ],
    ids=[
        "ends before it starts",
        "starts before the video",
        "not numbered from 1",
        "not in time order",
        "says nothing happens",
        "a look frame before the video",
        "two words of colour",
        "a field the contract lacks",
    ],
)
def test_a_wrong_video_watch_is_refused_at_the_boundary(wrong: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        VideoWatch.model_validate(a_watch(**wrong))


# One clip's sent list, serialised: what later tickets (the scene card, the grading page, the
# judge, the decider, the fixing agent) build against.
SENT_LIST_FIXTURE = Path(__file__).parent / "fixtures" / "sent_list.json"


def the_fixture() -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(SENT_LIST_FIXTURE.read_text())
    return raw


def test_the_sent_list_fixture_holds_a_left_out_a_trimmed_piece_and_a_fallen_back_recolour() -> (
    None
):
    sent = SentList.model_validate(the_fixture())

    assert sent.model_dump(mode="json") == the_fixture()
    assert [(left.library_number, left.tier) for left in sent.left_out] == [(5, 4)]
    assert [(piece.number, piece.trim) for piece in sent.pieces if piece.trim] == [
        (2, "pieces_over_limit")
    ]
    assert [(recolour.of, recolour.made) for recolour in sent.recolours] == [(1, 14), (2, None)]
    assert [recolour.outcome for recolour in sent.recolours] == ["done", "fell_back"]


def test_a_sent_list_is_kept_on_its_clip_and_comes_back_as_it_went_in() -> None:
    job = a_job()
    clip = ProducedItem.objects.create(job=job, kind=ProducedItem.Kind.CLIP)
    sent = SentList.model_validate(the_fixture())

    keep_sent_list(clip, sent)

    assert sent_list_of(ProducedItem.objects.get(pk=clip.pk)) == sent
    assert ProducedItem.objects.get(pk=clip.pk).sent == the_fixture()
    assert sent_list_of(ProducedItem.objects.create(job=job, kind="clip", version=2)) is None


def test_a_sent_list_kept_before_a_value_left_its_set_still_reads_back() -> None:
    clip = ProducedItem.objects.create(job=a_job(), kind=ProducedItem.Kind.CLIP)
    ProducedItem.objects.filter(pk=clip.pk).update(sent=changed("pictures.1.for", "colour"))

    sent = sent_list_of(ProducedItem.objects.get(pk=clip.pk))

    assert sent is not None and sent.pictures[1].model_dump()["for"] == "colour"


def changed(path: str, value: Any) -> dict[str, Any]:
    """The fixture with the value at `path` ("pictures.0.tier") set to `value`."""
    raw = the_fixture()
    *steps, last = path.split(".")
    place: Any = raw
    for step in steps:
        place = place[int(step)] if isinstance(place, list) else place[step]
    if isinstance(place, list):
        place[int(last)] = value
    else:
        place[last] = value
    return raw


def with_pictures(count: int) -> dict[str, Any]:
    raw = the_fixture()
    tier_4 = raw["pictures"][-1]
    raw["pictures"] += [
        {**tier_4, "image": image} for image in range(len(raw["pictures"]) + 1, count + 1)
    ]
    return raw


@pytest.mark.parametrize(
    "wrong",
    [
        changed("pictures.0.tier", 5),
        changed("pictures.1.for", "colour"),
        changed("pictures.7.for", "state"),
        changed("pictures.0.drawn", "room"),
        changed("pictures.6.library_number", 6),
        changed("pictures.6.tier", 2),
        changed("pictures.1.drawn", "room"),
        changed("pictures.2.image", 4),
        changed("pictures.0.label", ""),
        with_pictures(10),
        changed("left_out.0.tier", 0),
        changed("pieces.0.trim", "too_long"),
        changed("pieces.0.start", 7.0),
        changed("pieces.1.end", 16.0),
        changed("pieces.1.start", 13.0),
        changed("pieces.1.number", 3),
        changed("pieces.1.end", 15.0),
        changed("recolours.0.outcome", "maybe"),
        changed("recolours.0.made", None),
        changed("recolours.1.made", 15),
        changed("prompt", ""),
        {**the_fixture(), "colour_of_clip": "red"},
    ],
    ids=[
        "a tier that isn't one",
        "a purpose that isn't one",
        "a purpose outside the planner's tier",
        "a drawn picture as a library item too",
        "the drawn picture with a library number",
        "the drawn picture outside its tier",
        "a library item as a drawn picture too",
        "images not numbered 1, 2, 3...",
        "a picture with no label",
        "more pictures than the video API takes",
        "left out for no tier",
        "a trim that isn't one",
        "an untrimmed piece off its moment's seconds",
        "a trimmed piece past its moment's end",
        "a trimmed piece of no seconds",
        "pieces not numbered 1, 2...",
        "more seconds of video than the video API takes",
        "an outcome that isn't one",
        "a done recolour with nothing made",
        "a fallen-back recolour with something made",
        "no prompt",
        "a field the contract lacks",
    ],
)
def test_a_wrong_sent_list_is_refused_at_the_boundary(wrong: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SentList.model_validate(wrong)
