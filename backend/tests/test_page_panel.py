"""The page panel: when the product page can't be read, the shop owner pastes its text and
attaches its photos, and code keeps them as the page before any model sees the paste.
Driven through the chat, with the producer's model faked at the gateway and the shop and
Firecrawl served from a local server."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from adforge.retry import OutsideServiceDown
from agents import page_panel
from chat import messages as chat_messages
from chat.api import (
    ATTACH_A_PHOTO,
    MAX_PASTED_PAGE_CHARACTERS,
    MAX_TEXT_CHARACTERS,
    PAGE_PANEL_NOT_WANTED,
    PASTE_BEING_CHECKED,
    PASTE_THE_PAGE,
    PASTE_TOO_LONG,
)
from chat.models import Attachment, Message, Session
from gateway.fake import FakeModel, meanwhile, turn
from gateway.types import AnswerTookTooLong, BlockedBySafetyFilter
from jobs.models import Job

from .conftest import (
    PLAN,
    READABLE,
    FakeFirecrawl,
    given_to_the_producer,
    handoffs,
    paid_for,
    photo,
    producer_turns,
    recorded,
    results_of,
)

pytestmark = pytest.mark.django_db(transaction=True)

PASTE = (
    "Stoneware Mug\n"
    "Hand-thrown in our studio from speckled stoneware.\n"
    "Holds 350 ml and is dishwasher safe.\n"
    "Free shipping on orders over $50."
)
FRONT = photo(1)
SIDE = photo(2)
UNREADABLE = {"decision": "unreadable", "reason": "The text is only a cookie banner."}
ASKED = "I couldn't read that page. Paste its text and photos in the panel below."


@pytest.fixture
def unreadable(
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
) -> str:
    """A chat whose product page couldn't be read: the shop answered 404. Gives the link."""
    firecrawl.page_read["data"]["metadata"]["statusCode"] = 404
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": shampoo_page_url, "target_seconds": None})]),
        turn(says=ASKED),
    )
    say(f"Make an ad for {shampoo_page_url}")
    return shampoo_page_url


@pytest.fixture
def paste(api: APIClient, session_id: str) -> Callable[..., Any]:
    """Send the page panel's message: the pasted page text and its photos, as (name,
    content). The producer runs before the request returns, so script its turns first."""

    def sending(text: str, *photos: tuple[str, bytes]) -> Any:
        return api.post(
            f"/api/sessions/{session_id}/messages/",
            {
                "text": text,
                "photos": [
                    SimpleUploadedFile(name, content, content_type="image/png")
                    for name, content in photos
                ],
                "pasted_page": "true",
            },
            format="multipart",
        )

    return sending


def messages(api: APIClient, session_id: str) -> list[dict[str, Any]]:
    response = api.get(f"/api/sessions/{session_id}/messages/")
    listed: list[dict[str, Any]] = response.json()
    return listed


def the_paste(api: APIClient, session_id: str) -> dict[str, Any]:
    (pasted,) = [each for each in messages(api, session_id) if each["pasted_page"]]
    return pasted


def given_as_text(conversation: list[dict[str, Any]]) -> str:
    return json.dumps(conversation)


def test_a_pasted_page_with_photos_is_kept_as_the_page_and_the_ad_is_planned_from_it(
    api: APIClient,
    fake_model: FakeModel,
    unreadable: str,
    session_id: str,
    paste: Callable[..., Any],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))
    fake_model.respond("plan_ad", PLAN)

    sent = paste(PASTE, ("front.png", FRONT), ("side.png", SIDE))

    assert sent.status_code == 201, sent.json()
    # The page check judged the paste, as the page at the shop's link with 2 photos.
    (checked,) = handoffs("check_page")
    assert (checked["product_url"], checked["page_url"]) == (unreadable, unreadable)
    assert (checked["page_text"], checked["photo_count"]) == (PASTE, 2)
    job = Job.objects.get()
    assert job.page_text == PASTE
    assert job.page_text_full == PASTE
    assert job.page_html_key
    # The owner's photos are the product photos, kept where the chat stored them.
    photos = list(job.photos.all())
    attached = Attachment.objects.order_by("position").values_list("file", flat=True)
    assert [(photo.position, photo.file) for photo in photos] == list(enumerate(attached, 1))
    assert [photo.has_face for photo in photos] == [False, False]
    # Each owner photo is labelled for the job's library, as the picker never saw it.
    assert paid_for() == [
        "check_page",
        "copy_page_text",
        "note_face",
        "note_face",
        "label_photo",
        "label_photo",
        "plan_ad",
    ]
    # The chat says what was kept, and both the paste and that line carry the result.
    chat = messages(api, session_id)
    confirmed = [each for each in chat if each["pasted_page_result"] and not each["pasted_page"]]
    assert [(each["role"], each["text"]) for each in confirmed] == [
        (
            "notice",
            "Got it: I'm using your text as the page and these 2 photos as the product photos.",
        )
    ]
    assert confirmed[0]["pasted_page_result"] == "accepted"
    assert confirmed[0]["wants_more_photos"] is False
    assert the_paste(api, session_id)["pasted_page_result"] == "accepted"
    # The producer was told by the app, never given the paste, and planned.
    assert results_of("plan_ad")[0].startswith("Planned 3 scenes.")
    # The planner reads the paste as the page text, never as something the owner said.
    (planned_from,) = handoffs("plan_ad")
    assert all(PASTE not in said["text"] for said in planned_from["conversation"])
    told = given_as_text(given_to_the_producer(3))
    assert "Free shipping on orders over $50." not in told
    assert "sent the product page's text (22 words) and 2 photos through the page panel" in told
    assert producer_turns() == 4


def test_a_pasted_page_that_fails_its_check_keeps_nothing_and_says_why_until_a_good_one(
    api: APIClient,
    fake_model: FakeModel,
    unreadable: str,
    session_id: str,
    paste: Callable[..., Any],
) -> None:
    fake_model.respond("check_page", UNREADABLE, READABLE)

    sent = paste("Accept all cookies", ("front.png", FRONT), ("side.png", SIDE))

    assert sent.status_code == 201, sent.json()
    job = Job.objects.get()
    assert (job.page_text, job.page_html_key, job.photos.count()) == ("", "", 0)
    failed = [
        each
        for each in messages(api, session_id)
        if each["role"] == "notice" and each["pasted_page_result"]
    ]
    assert [(each["level"], each["text"], each["pasted_page_result"]) for each in failed] == [
        (
            "problem",
            "I can't use the page you pasted. The text is only a cookie banner.",
            "The text is only a cookie banner.",
        )
    ]
    assert the_paste(api, session_id)["pasted_page_result"] == "The text is only a cookie banner."
    # Nothing new for the producer: the chat already told the shop owner why.
    assert producer_turns() == 2
    assert paid_for() == ["check_page"]

    fake_model.respond("produce", turn(says="Thanks, I've got your page."))
    again = paste(PASTE, ("front.png", FRONT), ("side.png", SIDE))

    assert again.status_code == 201, again.json()
    job.refresh_from_db()
    assert (job.page_text, job.photos.count()) == (PASTE, 2)
    assert producer_turns() == 3


def test_a_pasted_page_with_one_photo_stops_before_planning_until_the_owner_answers(
    api: APIClient,
    fake_model: FakeModel,
    unreadable: str,
    session_id: str,
    paste: Callable[..., Any],
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond(
        "produce", turn(calls=[("plan_ad", {})]), turn(says="Only one photo: go ahead?")
    )

    paste(PASTE, ("front.png", FRONT))

    (confirmed,) = [
        each
        for each in messages(api, session_id)
        if each["role"] == "notice" and each["pasted_page_result"] == "accepted"
    ]
    assert confirmed["text"].endswith("this photo as the product photo.")
    # The page panel stays open for more photos until the owner answers.
    assert confirmed["wants_more_photos"] is True
    told = given_as_text(given_to_the_producer(3))
    assert "owner photo 1 is the product photo" in told
    assert "go ahead with this photo or attach more" in told
    assert results_of("plan_ad")[0].startswith("Refused: the shop owner hasn't answered")
    assert not Job.objects.get().scenes.exists()

    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))
    fake_model.respond("plan_ad", PLAN)
    say("Go ahead with that one")

    assert results_of("plan_ad")[1].startswith("Planned 3 scenes.")


def test_a_pasted_page_is_refused_once_the_page_was_read_from_its_link(
    api: APIClient,
    fake_model: FakeModel,
    session_id: str,
    paste: Callable[..., Any],
    page_read: str,
) -> None:
    refused = paste(PASTE, ("front.png", FRONT))

    assert refused.status_code == 400
    assert refused.json() == {"detail": PAGE_PANEL_NOT_WANTED}
    assert not Message.objects.filter(pasted_page=True).exists()


def test_a_pasted_page_with_no_photo_or_no_text_is_refused(
    api: APIClient, unreadable: str, paste: Callable[..., Any]
) -> None:
    no_photo = paste(PASTE)
    no_text = paste("  ", ("front.png", FRONT))

    assert (no_photo.status_code, no_photo.json()) == (400, {"detail": ATTACH_A_PHOTO})
    assert (no_text.status_code, no_text.json()) == (400, {"text": [PASTE_THE_PAGE]})
    assert not Message.objects.filter(pasted_page=True).exists()


def test_a_pasted_page_that_cant_be_checked_keeps_nothing_and_says_so_once(
    api: APIClient,
    fake_model: FakeModel,
    unreadable: str,
    session_id: str,
    paste: Callable[..., Any],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("copy_page_text", *[OutsideServiceDown("503 from the provider")] * 3)

    paste(PASTE, ("front.png", FRONT), ("side.png", SIDE))

    # The shop owner is told plainly; the service's error is the developer's.
    why = "I couldn't check it, so nothing was kept. Send it again."
    assert [
        (each["text"], each["pasted_page_result"])
        for each in messages(api, session_id)
        if each["role"] == "notice" and each["pasted_page_result"]
    ] == [(f"I can't use the page you pasted. {why}", why)]
    assert (
        "problem",
        "Checking the page the shop owner pasted failed (The model provider was still down "
        "after 3 tries: 503 from the provider), so nothing was kept; they are asked to send it "
        "again.",
    ) in recorded(api, session_id)
    assert Job.objects.get().page_html_key == ""
    assert producer_turns() == 2


def test_a_pasted_page_counts_each_line_end_once_and_says_plainly_when_too_long(
    fake_model: FakeModel, unreadable: str, paste: Callable[..., Any]
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("produce", turn(says="Got your page."))
    # A browser sends each line end of the text box as two characters.
    lines = MAX_PASTED_PAGE_CHARACTERS // 10
    just_fits = "\r\n".join(["Mug line."] * lines)
    assert len(just_fits) > MAX_PASTED_PAGE_CHARACTERS >= len(just_fits.replace("\r\n", "\n"))
    # A whole page pasted may be longer than a typed message.
    assert len(just_fits.replace("\r\n", "\n")) > MAX_TEXT_CHARACTERS

    too_long = paste(just_fits + "\r\nOne more line.", ("front.png", FRONT))
    sent = paste(just_fits, ("front.png", FRONT), ("side.png", SIDE))

    assert (too_long.status_code, too_long.json()) == (400, {"text": [PASTE_TOO_LONG]})
    assert sent.status_code == 201, sent.json()
    kept = Message.objects.get(pasted_page=True)
    assert (kept.text, kept.pasted_page_result) == (just_fits.replace("\r\n", "\n"), "accepted")


def test_a_second_pasted_page_is_refused_while_the_first_is_checked(
    api: APIClient,
    fake_model: FakeModel,
    unreadable: str,
    session_id: str,
    paste: Callable[..., Any],
) -> None:
    second: list[Any] = []

    def checked_while_pasted_again() -> dict[str, Any]:
        second.append(paste(PASTE, ("front.png", FRONT)))
        return READABLE

    fake_model.respond("check_page", checked_while_pasted_again)
    fake_model.respond("produce", turn(says="Got your page."))

    sent = paste(PASTE, ("front.png", FRONT), ("side.png", SIDE))

    assert sent.status_code == 201, sent.json()
    assert (second[0].status_code, second[0].json()) == (400, {"detail": PASTE_BEING_CHECKED})
    assert [
        each["pasted_page_result"] for each in messages(api, session_id) if each["pasted_page"]
    ] == ["accepted"]


def test_a_page_pasted_while_the_producer_answers_a_question_is_kept_without_another_message(
    api: APIClient,
    fake_model: FakeModel,
    unreadable: str,
    session_id: str,
    paste: Callable[..., Any],
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond(
        "produce",
        meanwhile(
            lambda: paste(PASTE, ("front.png", FRONT), ("side.png", SIDE)),
            turn(says="Paste it in the panel below."),
        ),
        turn(says="Got your page."),
    )

    say("Where do I paste my page?")

    assert Job.objects.get().page_text == PASTE
    assert the_paste(api, session_id)["pasted_page_result"] == "accepted"


def test_a_question_typed_while_a_one_photo_paste_is_checked_doesnt_answer_the_pause(
    api: APIClient,
    fake_model: FakeModel,
    unreadable: str,
    session_id: str,
    paste: Callable[..., Any],
    say: Callable[..., None],
) -> None:
    def checked_while_asked() -> dict[str, Any]:
        say("How long will my ad be?")
        return READABLE

    fake_model.respond("check_page", checked_while_asked)
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]))
    fake_model.answer_unscripted("produce", lambda _: turn(says="Go ahead with this photo?"))

    paste(PASTE, ("front.png", FRONT))

    assert results_of("plan_ad")[0].startswith("Refused: the shop owner hasn't answered")
    assert not Job.objects.get().scenes.exists()


def test_the_same_photo_attached_twice_with_a_pasted_page_is_kept_once(
    api: APIClient,
    fake_model: FakeModel,
    unreadable: str,
    session_id: str,
    paste: Callable[..., Any],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.answer_unscripted("produce", lambda _: turn(says="Go ahead with this photo?"))

    paste(PASTE, ("front.png", FRONT), ("front-copy.png", FRONT))

    assert Job.objects.get().photos.count() == 1
    assert "Got it: I'm using your text as the page and this photo as the product photo." in [
        each["text"] for each in messages(api, session_id)
    ]


def test_a_paste_found_after_the_page_was_kept_closes_the_panel(
    api: APIClient, fake_model: FakeModel, unreadable: str, session_id: str
) -> None:
    # As when a worker stopped after keeping the page and before the chat was told.
    session = Session.objects.get(pk=session_id)
    chat_messages.add(session, role=Message.Role.USER, text=PASTE, pasted_page=True)
    Job.objects.update(page_html_key="jobs/kept/page.html")

    page_panel.take_pastes(session)

    (said,) = [each for each in messages(api, session_id) if each["pasted_page_result"]][-1:]
    assert (said["level"], said["wants_pasted_page"]) == ("info", False)
    assert not chat_messages.page_panel_wanted(session)


@pytest.mark.parametrize(
    "fails",
    [
        pytest.param(
            BlockedBySafetyFilter("The provider's filter refused it."), id="safety filter"
        ),
        pytest.param(AnswerTookTooLong("check_page took too long."), id="too slow"),
    ],
)
def test_a_pasted_page_whose_check_is_refused_is_answered_and_can_be_sent_again(
    api: APIClient,
    fake_model: FakeModel,
    unreadable: str,
    session_id: str,
    paste: Callable[..., Any],
    fails: Exception,
) -> None:
    fake_model.respond("check_page", fails, READABLE)
    fake_model.answer_unscripted("produce", lambda _: turn(says="Got your page."))

    first = paste(PASTE, ("front.png", FRONT), ("side.png", SIDE))
    again = paste(PASTE, ("front.png", FRONT), ("side.png", SIDE))

    assert (first.status_code, again.status_code) == (201, 201)
    results = [
        each["pasted_page_result"] for each in messages(api, session_id) if each["pasted_page"]
    ]
    assert results[0] == page_panel.CHECK_FAILED
    assert results[1] == "accepted"
    assert not any(each["text"].startswith("I had to stop") for each in messages(api, session_id))


def test_the_photos_of_a_pasted_page_join_the_library_as_the_pages_labelled(
    api: APIClient,
    fake_model: FakeModel,
    unreadable: str,
    session_id: str,
    paste: Callable[..., Any],
) -> None:
    from jobs.library import PagePhoto, library_of

    fake_model.respond("check_page", READABLE)
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))
    fake_model.respond("plan_ad", PLAN)

    paste(PASTE, ("front.png", FRONT), ("side.png", SIDE))

    attached = list(Attachment.objects.order_by("position").values_list("file", flat=True))
    # The page's photos, as the page at the shop's link would give them.
    assert [(item.number, item.source, item.file) for item in library_of(Job.objects.get())] == [
        (number, PagePhoto(photo=number), file) for number, file in enumerate(attached, 1)
    ]
    labelled = handoffs("label_photo")
    assert [each["photo"] for each in labelled] == attached
    assert {each["product_text"] for each in labelled} == {PASTE}
    told = (
        "info",
        "The photo picker didn't say what 2 of the page's photos show, so a call of its own "
        "looked at each and labelled them for the ad's picture library, as the picker labels "
        "the photos it picks.",
    )
    assert told in recorded(api, session_id)
    # Only recorded for the developer: the shop owner's chat doesn't show it.
    assert told not in [(each["level"], each["text"]) for each in messages(api, session_id)]
