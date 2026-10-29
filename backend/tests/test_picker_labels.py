"""The photo picker labels every page photo it keeps with fixed fields and names its copy
groups, driven through the chat: the owner's first message, the picker faked at the gateway,
the shop's page on the local web server through the stand-in Firecrawl."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import work
from jobs.library import PictureLabel
from jobs.models import Job, ProductPhoto

from .conftest import (
    READABLE,
    SHAMPOO_PHOTO_PATH,
    SHAMPOO_PICKED,
    FakeFirecrawl,
    paid_for,
    photo,
    picture,
)

pytestmark = pytest.mark.django_db(transaction=True)


# The shampoo's page grows these pictures, each at its own link: I<n> for n here.
BACK = photo(101)
BACK_BIGGER = photo(101, 224, 280)  # The same back photo at another size and name.
SIDE = photo(102)
CAP_OFF = photo(103)
IN_USE = photo(104)
LINE_UP = photo(105)
# The front in another shade: one colour all over, as the amber front photo is, so the old
# fingerprint check took the two for copies.
FRONT_IN_BLACK = picture(300, 400, (20, 20, 20), format="JPEG")
ADDED = {
    101: BACK,
    106: BACK_BIGGER,
    102: SIDE,
    103: CAP_OFF,
    104: IN_USE,
    105: LINE_UP,
    107: FRONT_IN_BLACK,
}


def label(
    mark: str,
    group: int,
    kind: str,
    side: str,
    sentence: str,
    *,
    alone: str = "yes",
    colour: str = "amber",
    target: str = "",
) -> dict[str, Any]:
    return {
        "picture": mark,
        "copy_group": group,
        "kind": kind,
        "side": side,
        "alone": alone,
        "colour": colour,
        "target": target,
        "label": sentence,
    }


# I16 is a thumbnail of I8's photo, at the same link.
LABELS = [
    label("I8", 1, "side", "front", "The amber bottle from the front, cap on."),
    label("I16", 1, "side", "front", "The amber bottle from the front, cap on."),
    label("I101", 2, "side", "back", "The amber bottle from the back, its label showing."),
    label("I106", 2, "side", "back", "The amber bottle from the back, its label showing."),
    label("I102", 3, "side", "side", "The amber bottle from its left side."),
    label("I103", 4, "state", "front", "The amber bottle with its cap off, the pump showing."),
    label(
        "I104",
        5,
        "in-use",
        "none",
        "A hand pressing the pump, the shampoo coming out.",
        alone="no",
    ),
    label(
        "I105",
        6,
        "line-up",
        "front",
        "Three bottles in a row on a shelf.",
        alone="no",
        target="the second bottle from the left",
    ),
    # A colour is stored in small letters, however the picker wrote it.
    label("I107", 7, "side", "front", "The black bottle from the front, cap on.", colour="Black"),
]
GALLERY = [8, 16, 101, 106, 102, 103, 104, 105, 107]
PICKED = {**SHAMPOO_PICKED, "gallery_images": GALLERY, "pictures": LABELS}


@pytest.fixture
def labelled_page(httpserver: HTTPServer, firecrawl: FakeFirecrawl, shampoo_page_url: str) -> str:
    """The shampoo's page with the pictures in ADDED in its gallery, each served. Gives the
    page's link."""
    returns = firecrawl.marked["data"]["actions"]["javascriptReturns"]
    marks = json.loads(returns[3]["value"])
    marks["imgs"] += [
        {
            "n": n,
            "src": f"{firecrawl.shop}/cdn/gallery/{n}.png",
            "srcset": "",
            "alt": "",
            "href": "",
        }
        for n in ADDED
    ]
    returns[3]["value"] = json.dumps(marks)
    for n, content in ADDED.items():
        httpserver.expect_request(f"/cdn/gallery/{n}.png").respond_with_data(
            content, content_type="image/png"
        )
    return shampoo_page_url


def reading(fake_model: FakeModel, link: str) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": link, "target_seconds": None})]),
        turn(says="Done."),
    )


def kept(job: Job) -> list[dict[str, Any]]:
    """Each kept page photo: where it came from on the page and the picker's fields."""
    return [
        {
            "from": photo.source_url.rsplit("/", 1)[-1].split("?")[0],
            "kind": photo.kind,
            "side": photo.side,
            "alone": photo.alone,
            "colour": photo.colour,
            "target": photo.target,
            "label": photo.label,
        }
        for photo in job.photos.exclude(source_url="").filter(from_video=None)
    ]


def notices(api: APIClient, session_id: str) -> list[tuple[str, str]]:
    messages = api.get(f"/api/sessions/{session_id}/messages/").json()
    return [(m["level"], m["text"]) for m in messages if m["role"] == "notice"]


def test_every_kept_page_photo_carries_the_pickers_fixed_fields_and_one_label(
    api: APIClient,
    fake_model: FakeModel,
    labelled_page: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    reading(fake_model, labelled_page)
    fake_model.respond("check_page", READABLE)
    fake_model.respond("pick_photos", PICKED)

    say(f"Make an ad for {labelled_page}")

    front = SHAMPOO_PHOTO_PATH.rsplit("/", 1)[-1]
    assert kept(Job.objects.get()) == [
        {
            "from": front,
            "kind": "side",
            "side": "front",
            "alone": "yes",
            "colour": "amber",
            "target": "",
            "label": "The amber bottle from the front, cap on.",
        },
        {
            "from": "101.png",
            "kind": "side",
            "side": "back",
            "alone": "yes",
            "colour": "amber",
            "target": "",
            "label": "The amber bottle from the back, its label showing.",
        },
        {
            "from": "102.png",
            "kind": "side",
            "side": "side",
            "alone": "yes",
            "colour": "amber",
            "target": "",
            "label": "The amber bottle from its left side.",
        },
        {
            "from": "103.png",
            "kind": "state",
            "side": "front",
            "alone": "yes",
            "colour": "amber",
            "target": "",
            "label": "The amber bottle with its cap off, the pump showing.",
        },
        {
            "from": "104.png",
            "kind": "in-use",
            "side": "none",
            "alone": "no",
            "colour": "amber",
            "target": "",
            "label": "A hand pressing the pump, the shampoo coming out.",
        },
        {
            "from": "105.png",
            "kind": "line-up",
            "side": "front",
            "alone": "no",
            "colour": "amber",
            "target": "the second bottle from the left",
            "label": "Three bottles in a row on a shelf.",
        },
        {
            "from": "107.png",
            "kind": "side",
            "side": "front",
            "alone": "yes",
            "colour": "black",
            "target": "",
            "label": "The black bottle from the front, cap on.",
        },
    ]
    # Each copies into the library as it stands.
    for fields in kept(Job.objects.get()):
        PictureLabel.model_validate({key: fields[key] for key in fields if key != "from"})
    assert notices(api, session_id) == []
    assert Job.objects.get().warnings == []


def test_a_picker_answer_with_a_value_outside_the_fixed_fields_is_refused(
    api: APIClient,
    fake_model: FakeModel,
    labelled_page: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    reading(fake_model, labelled_page)
    fake_model.respond("check_page", READABLE)
    fake_model.respond(
        "pick_photos",
        {**PICKED, "pictures": [{**LABELS[0], "kind": "packshot"}, *LABELS[1:]]},
    )

    say(f"Make an ad for {labelled_page}")

    (picking,) = ModelCall.objects.filter(purpose="pick_photos")
    assert picking.outcome == ModelCall.Outcome.FAILED
    assert "kind" in picking.error
    # The page's declared photos are kept instead, with no picker's fields on them.
    assert {photo["kind"] for photo in kept(Job.objects.get())} == {""}
    (problem,) = [text for level, text in notices(api, session_id) if level == "problem"]
    assert problem.startswith("Picking this product's photos off the page failed")


@pytest.mark.parametrize(
    "changed",
    [
        pytest.param({"kind": "side", "target": "the bottle on the left"}, id="target, no line-up"),
        pytest.param({"kind": "line-up", "target": ""}, id="line-up, no target"),
        pytest.param({"colour": "light amber"}, id="colour of two words"),
        pytest.param({"label": " "}, id="no label"),
        pytest.param({"picture": "8"}, id="not a mark"),
    ],
)
def test_a_picture_whose_fields_break_their_rules_is_refused(
    fake_model: FakeModel,
    labelled_page: str,
    say: Callable[..., None],
    changed: dict[str, str],
) -> None:
    reading(fake_model, labelled_page)
    fake_model.respond("check_page", READABLE)
    fake_model.respond(
        "pick_photos", {**PICKED, "pictures": [{**LABELS[0], **changed}, *LABELS[1:]]}
    )

    say(f"Make an ad for {labelled_page}")

    (picking,) = ModelCall.objects.filter(purpose="pick_photos")
    assert picking.outcome == ModelCall.Outcome.FAILED


def test_a_picked_picture_the_picker_gave_no_fields_is_kept_and_named_in_red(
    api: APIClient,
    fake_model: FakeModel,
    labelled_page: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    reading(fake_model, labelled_page)
    fake_model.respond("check_page", READABLE)
    without = {"I102", "I104"}
    fake_model.respond(
        "pick_photos",
        {**PICKED, "pictures": [entry for entry in LABELS if entry["picture"] not in without]},
    )

    say(f"Make an ad for {labelled_page}")

    by_link = {photo["from"]: photo for photo in kept(Job.objects.get())}
    assert by_link["102.png"]["kind"] == by_link["104.png"]["kind"] == ""
    assert by_link["101.png"]["kind"] == "side"
    text = (
        "The photo picker kept pictures I102 and I104 off the page without saying what each "
        "shows (its kind, side, colour and label), so they are kept unlabelled and may each be "
        "kept twice if the page has another copy."
    )
    assert notices(api, session_id) == [("problem", text)]
    assert [warning["text"] for warning in Job.objects.get().warnings] == [text]


def test_a_page_read_again_keeps_each_copy_group_once_without_comparing_the_photos(
    api: APIClient,
    fake_model: FakeModel,
    labelled_page: str,
    session_id: str,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The first read stops after its photos are kept, as when a worker stops: the page is
    # left unread, and the owner asks for it again.
    stopped: list[bool] = []

    def stops_once(*args: Any) -> None:
        if not stopped:
            stopped.append(True)
            raise RuntimeError("the worker stopped")
        work.keep_page(*args)

    monkeypatch.setattr("agents.producer.keep_page", stops_once)
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": labelled_page, "target_seconds": None})]),
        turn(says="Something went wrong, I'll try again."),
        turn(calls=[("read_page", {"link": labelled_page, "target_seconds": None})]),
        turn(says="Done."),
    )
    fake_model.respond("check_page", READABLE, READABLE)
    fake_model.respond("pick_photos", PICKED)
    say(f"Make an ad for {labelled_page}")
    first = [(photo.pk, photo.source_url) for photo in Job.objects.get().photos.all()]

    say("Try again")

    job = Job.objects.get()
    assert job.status == Job.Status.PAGE_READ
    # The same photos, each kept once: the back's other copy (I106), whose link the first
    # read never kept, isn't added now, and the black front stays beside the amber one.
    assert [(photo.pk, photo.source_url) for photo in job.photos.all()] == first
    assert "106.png" not in " ".join(url for _pk, url in first)
    assert "107.png" in " ".join(url for _pk, url in first)
    assert paid_for().count("pick_photos") == 1
    # The kept back photo names its copy group, for the library to copy in.
    back = ProductPhoto.objects.get(source_url__endswith="101.png")
    assert back.copy_links == [back.source_url, back.source_url.replace("101.png", "106.png")]


def test_a_copy_marked_with_a_face_gives_the_photo_kept_for_its_group_the_face(
    fake_model: FakeModel, labelled_page: str, say: Callable[..., None]
) -> None:
    reading(fake_model, labelled_page)
    fake_model.respond("check_page", READABLE)
    fake_model.respond("pick_photos", {**PICKED, "face_images": [106]})

    say(f"Make an ad for {labelled_page}")

    faces = {
        photo.source_url.rsplit("/", 1)[-1]: photo.has_face
        for photo in Job.objects.get().photos.exclude(source_url__contains="Detox")
    }
    assert faces == {
        "101.png": True,
        "102.png": False,
        "103.png": False,
        "104.png": False,
        "105.png": False,
        "107.png": False,
    }


def test_a_later_read_never_keeps_a_copy_the_first_read_grouped_with_a_kept_photo(
    fake_model: FakeModel, labelled_page: str, say: Callable[..., None]
) -> None:
    reading(fake_model, labelled_page)
    fake_model.respond("check_page", READABLE)
    fake_model.respond("pick_photos", PICKED)
    say(f"Make an ad for {labelled_page}")
    job = Job.objects.get()
    before = list(job.photos.values_list("pk", flat=True))
    back = job.photos.get(source_url__endswith="101.png").source_url
    copy = back.replace("101.png", "106.png")

    # The page read again shows only the back's other copy, in a group of its own.
    work.save_photos(job, [copy], copies={copy: (copy,)}, faces=frozenset())

    assert list(job.photos.values_list("pk", flat=True)) == before
