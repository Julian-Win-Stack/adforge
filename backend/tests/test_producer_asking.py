"""The planner asking the shop owner because of B-roll (#100): when a scene needs something
no photo shows, the product's "how to use" is missing, or no photo clearly shows the product. The
question goes to the shop owner, the job waits for their answer, and what they answer, in
words or as a photo, reaches the next planning call. Driven through the chat, with the
producer's model and the planner faked at the gateway."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from jobs.models import Job

from .conftest import (
    PLAN,
    a_plan_with,
    broll,
    broll_labels,
    chat,
    handoffs,
    paid_for,
    picture,
    results_of,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)

# What the planner asks when the pour scene needs the tea's colour and no photo shows it:
# the two answers, and no word of which scenes show the product.
MISSING_PHOTO = (
    "To show tea being poured into the mug, I need to see it filled. Could you attach a "
    "photo of the mug with tea in it, or should I go ahead without one? Then I'll tell you "
    "how I'd show it, for you to approve or change."
)

PROPOSED = (
    "Without a photo, I'd show the empty mug on a workbench while a hand lifts it by the "
    "handle. Is that OK, or would you like it changed?"
)

NO_CLEAR_PHOTO = (
    "None of the photos clearly shows the mug. Could you attach one where it can be seen "
    "on its own?"
)


def asking(question: str, reason: str) -> dict[str, Any]:
    return {"decision": "ask", "reason": reason, "question": question, "plan": None}


def broll_plan(shows: str, needs: list[dict[str, Any]], colour_photos: list[int]) -> dict[str, Any]:
    """The mug plan with its second scene showing `shows` and needing `needs`."""
    scenes = [
        broll({**scene, "shows": shows, "needs": needs}) if number == 2 else scene
        for number, scene in enumerate(PLAN["plan"]["scenes"], start=1)
    ]
    return a_plan_with(scenes=scenes, colour_photos=colour_photos)


@pytest.fixture
def asked(fake_model: FakeModel, page_read: str, say: Callable[..., None]) -> None:
    """A chat whose planner asked for a photo of the mug filled with tea."""
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says=MISSING_PHOTO))
    fake_model.respond("plan_ad", asking(MISSING_PHOTO, "No photo shows the mug with tea in it."))
    say("Plan it")


def test_a_missing_photo_is_asked_about_and_the_job_waits_for_the_answer(
    fake_model: FakeModel, page_read: str, say: Callable[..., None], api: Any, session_id: str
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("plan_ad", {})]),
        # Planning again before the shop owner answers would pay for the same question.
        turn(calls=[("plan_ad", {})]),
        turn(says=MISSING_PHOTO),
    )
    fake_model.respond("plan_ad", asking(MISSING_PHOTO, "No photo shows the mug with tea in it."))

    say("Plan it")

    asked, again = results_of("plan_ad")
    assert MISSING_PHOTO in asked
    # The producer is told how to take either answer: a photo is added before planning.
    assert "use_photos" in asked
    assert again.startswith("Refused: the shop owner hasn't answered")
    assert paid_for().count("plan_ad") == 1
    assert not Job.objects.get().scenes.exists()
    assert chat(api, session_id)[-1] == ("agent", MISSING_PHOTO)


def test_the_shop_owners_answer_in_words_reaches_the_next_planning_call_as_theirs(
    fake_model: FakeModel, asked: None, say: Callable[..., None]
) -> None:
    # They go ahead without a photo: the planner proposes the scene in words.
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says=PROPOSED))
    fake_model.respond("plan_ad", asking(PROPOSED, "They chose to go ahead without a photo."))
    say("Go ahead without one")

    # They approve it with a change: that is the plan's to use.
    shows = "The empty mug on a workbench, a hand lifting it by the handle and turning it."
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))
    fake_model.respond("plan_ad", broll_plan(shows, [], [1]))
    say("Yes, but have the hand turn it too")

    *_, proposing, planning = handoffs("plan_ad")
    assert proposing["conversation"][-2:] == [
        {"by": "producer", "text": MISSING_PHOTO},
        {"by": "user", "text": "Go ahead without one"},
    ]
    assert planning["conversation"][-2:] == [
        {"by": "producer", "text": PROPOSED},
        {"by": "user", "text": "Yes, but have the hand turn it too"},
    ]
    assert Job.objects.get().scenes.get(number=2).shows == shows


def test_a_photo_attached_as_the_answer_is_added_and_can_be_a_needed_photo(
    fake_model: FakeModel, asked: None, say: Callable[..., None]
) -> None:
    filled = picture(300, 400, (120, 70, 30))
    fake_model.respond(
        "produce",
        turn(calls=[("use_photos", {})]),
        turn(calls=[("plan_ad", {})]),
        turn(says="Here's the plan."),
    )
    shows = "Tea poured into the mug on a workbench."
    fake_model.respond(
        "plan_ad", broll_plan(shows, [{"what": "the mug with tea in it", "photos": [3]}], [1])
    )
    fake_model.respond("note_face", {"has_face": False})

    say("Here's one", ("filled.png", filled))

    *_, planning = handoffs("plan_ad")
    assert planning["photo_count"] == 3
    assert planning["conversation"][-1] == {
        "by": "user",
        "text": "Here's one\n\n[Attached 1 picture]",
    }
    assert broll_labels()[1][-1] == [{"what": "the mug with tea in it", "photos": [3]}]


def test_no_photo_clearly_showing_the_product_asks_for_one(
    fake_model: FakeModel, page_read: str, say: Callable[..., None], api: Any, session_id: str
) -> None:
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says=NO_CLEAR_PHOTO))
    fake_model.respond(
        "plan_ad", asking(NO_CLEAR_PHOTO, "The mug is hidden among other mugs in both photos.")
    )
    say("Plan it")

    assert NO_CLEAR_PHOTO in results_of("plan_ad")[0]
    assert not Job.objects.get().scenes.exists()

    # Their photo is the one the ad's colour is shown in.
    fake_model.respond(
        "produce",
        turn(calls=[("use_photos", {})]),
        turn(calls=[("plan_ad", {})]),
        turn(says="Here's the plan."),
    )
    fake_model.respond("note_face", {"has_face": False})
    fake_model.respond("plan_ad", a_plan_with(colour_photos=[3]))
    say("Here", ("mine.png", picture(300, 400, (60, 90, 70))))

    job = Job.objects.get()
    assert job.scenes.count() == 3
    assert list(
        job.photos.filter(shows_product_colour=True).values_list("position", flat=True)
    ) == [3]
