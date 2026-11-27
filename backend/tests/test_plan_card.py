"""The plan card (spec #768, ticket #777): after the page is read the producer shows a card
with 3 plans, nothing is made until the shop owner answers it, and the ad is made of the
chosen plan's style and length. Through the chat, the producer's model faked and scripted."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from agents import plan_card
from agents.models import SystemNote
from gateway.fake import turn
from jobs.models import Job, ProducedItem

from .conftest import (
    PLAN,
    READABLE,
    ScriptedProducer,
    chat,
    given_to_the_producer,
    handoffs,
    results_of,
    shampoo_picked,
)
from .test_picker_labels import label, labelled_page  # noqa: F401 (a fixture)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.plan_card]


def a_plan(title: str, *, seconds: int = 20, style: str = "person_talking") -> dict[str, Any]:
    return {
        "style": style,
        "seconds": seconds,
        "title": title,
        "built_on": f"What makes {title} different.",
        "opens": f"Opens on {title}.",
        "doubt": "Can I trust it?",
    }


PLANS = [a_plan("Hot all morning"), a_plan("Fits the cup holder"), a_plan("Ten-year guarantee")]

CARD: dict[str, Any] = {
    "opening": "I read the page and looked at its photos. Here are 3 plans.",
    "product_option": None,
    "option_named": "",
    "plans": PLANS,
    "plans_pick": 1,
    "other": None,
}


def _card_shown(producer: ScriptedProducer, link: str, *after: Any, card: Any = None) -> None:
    producer.fake_model.respond("check_page", READABLE)
    producer.told(
        f"Make an ad for {link}",
        ("read_page", {"link": link, "target_seconds": None, "why": "To start."}),
        # The card ends the producer's turn.
        turn(calls=[("show_plan_card", {**(card or CARD), "why": "The page is read."})]),
        *after,
    )


def _messages(api: APIClient, session_id: str) -> list[dict[str, Any]]:
    got: list[dict[str, Any]] = api.get(f"/api/sessions/{session_id}/messages/").json()
    return got


def _the_card(api: APIClient, session_id: str) -> dict[str, Any]:
    [card] = [m["plan_card"] for m in _messages(api, session_id) if m.get("plan_card")]
    shown: dict[str, Any] = card
    return shown


def test_a_link_gets_a_plan_card_with_3_plans_and_nothing_is_made_while_it_is_open(
    producer: ScriptedProducer, product_page_url: str, api: APIClient, session_id: str
) -> None:
    _card_shown(producer, product_page_url)

    card = _the_card(api, session_id)
    assert card["opening"] == CARD["opening"]
    [plans] = [q for q in card["questions"] if q["key"] == "plan"]
    assert [choice["title"] for choice in plans["choices"]] == [
        "Hot all morning",
        "Fits the cup holder",
        "Ten-year guarantee",
    ]
    assert plans["choices"][0]["style"] == "Person talking"
    assert plans["choices"][0]["seconds"] == 20
    # The doubt is kept with the job, never shown to the shop owner.
    assert "doubt" not in plans["choices"][0]
    assert plans["pick"] == 0
    assert card["state"] == "open"
    job = Job.objects.get()
    assert not job.scenes.exists()
    assert not job.produced.filter(kind=ProducedItem.Kind.MUSIC).exists()


def _kinds(*names: str) -> list[str]:
    return [w["kind"] for w in Job.objects.get().warnings if w.get("kind") in names]


def test_a_producer_that_plans_before_the_card_is_refused_and_then_shows_the_card(
    producer: ScriptedProducer, product_page_url: str, api: APIClient, session_id: str
) -> None:
    producer.fake_model.respond("check_page", READABLE)
    producer.told(
        f"Make an ad for {product_page_url}",
        ("read_page", {"link": product_page_url, "target_seconds": None, "why": "To start."}),
        ("plan_ad", {"why": "The page is read."}),
        ("create_person", {"why": "Then the person."}),
        turn(calls=[("show_plan_card", {**CARD, "why": "The tool said to."})]),
    )

    [refused] = results_of("plan_ad")
    assert plan_card.LOCKED in refused
    assert plan_card.LOCKED in results_of("create_person")[0]
    assert _kinds("plan_card_locked") == ["plan_card_locked", "plan_card_locked"]
    assert _the_card(api, session_id)["state"] == "open"
    assert not Job.objects.get().scenes.exists()


@pytest.mark.parametrize(
    ("change", "fix"),
    [
        ({"plans": PLANS[:2]}, "it has 2 plans, and a card has exactly 3"),
        ({"plans_pick": 4}, "the plans' pick is 4"),
        (
            {"plans": [a_plan("Shine", seconds=40), *PLANS[1:]]},
            "plan 1 is 40 seconds, and a plan is 1 to 35.",
        ),
        (
            {"other": {"asks": "Which colour?", "choices": ["Red"], "pick": 1}},
            "the other question has 1 choices",
        ),
    ],
    ids=["two plans", "pick out of range", "too long", "one choice"],
)
def test_a_card_that_cant_be_shown_is_refused_with_what_to_fix(
    change: dict[str, Any],
    fix: str,
    producer: ScriptedProducer,
    product_page_url: str,
    api: APIClient,
    session_id: str,
) -> None:
    _card_shown(producer, product_page_url, "I'll fix the card.", card={**CARD, **change})

    [refused] = results_of("show_plan_card")
    assert fix in refused
    assert "Fix it and call it again" in refused
    assert _kinds("plan_card_invalid") == ["plan_card_invalid"]
    assert not any(m.get("plan_card") for m in _messages(api, session_id))


def _chosen() -> dict[str, Any]:
    chosen = Job.objects.get().chosen_plan
    assert chosen is not None
    kept: dict[str, Any] = chosen
    return kept


def _answer(api: APIClient, session_id: str, card: int, **sent: Any) -> Any:
    return api.post(f"/api/sessions/{session_id}/plan-cards/{card}/answer/", sent, format="json")


@pytest.mark.parametrize(
    ("sent", "title", "seconds", "says"),
    [
        (
            {"use_picks": True},
            "Hot all morning",
            20,
            "Use the agent's picks.\nWhich plan? “Hot all morning” (Person talking, 20 s)",
        ),
        (
            {"choices": {"plan": 2}},
            "Ten-year guarantee",
            25,
            "Which plan? “Ten-year guarantee” (Person talking, 25 s)",
        ),
    ],
    ids=["the agent's picks", "make this ad"],
)
def test_an_answer_unlocks_and_the_ad_is_planned_at_the_chosen_plans_length(
    sent: dict[str, Any],
    title: str,
    seconds: int,
    says: str,
    producer: ScriptedProducer,
    product_page_url: str,
    api: APIClient,
    session_id: str,
) -> None:
    card = {**CARD, "plans": [*PLANS[:2], a_plan("Ten-year guarantee", seconds=25)]}
    _card_shown(producer, product_page_url, card=card)
    card_id = _the_card(api, session_id)["id"]
    producer.fake_model.respond("plan_ad", PLAN)
    producer.fake_model.respond(
        "produce", turn(calls=[("plan_ad", {"why": "Answered."})]), turn(says="Here's the plan.")
    )

    answered = _answer(api, session_id, card_id, **sent)

    assert answered.status_code == 201, answered.json()
    assert chat(api, session_id)[-2] == ("user", says)
    job = Job.objects.get()
    assert _chosen()["plan"]["title"] == title
    assert _chosen()["plan"]["doubt"] == "Can I trust it?"
    assert job.target_seconds == seconds
    assert job.scenes.count() == 3
    shown = _the_card(api, session_id)
    assert shown["state"] == "answered"
    # The producer reads the whole chosen plan with the owner's answer.
    assert f"built on: What makes {title} different." in str(given_to_the_producer(3))
    # And the planner is handed it as fixed input (#778).
    [handoff] = handoffs("plan_ad")
    assert handoff["chosen_plan"] == (
        f"Title: {title}. Built on: What makes {title} different. Opens: Opens on {title}. "
        f"Length: {seconds} seconds."
    )


def test_a_card_takes_one_answer(
    producer: ScriptedProducer, product_page_url: str, api: APIClient, session_id: str
) -> None:
    _card_shown(producer, product_page_url)
    card_id = _the_card(api, session_id)["id"]
    producer.fake_model.respond("produce", turn(says="On it."))
    assert _answer(api, session_id, card_id, use_picks=True).status_code == 201

    again = _answer(api, session_id, card_id, choices={"plan": 1})

    assert again.status_code == 409
    assert _chosen()["plan"]["title"] == "Hot all morning"


def test_a_message_in_the_chat_never_unlocks_and_no_nudge_is_sent_while_the_card_is_open(
    producer: ScriptedProducer, product_page_url: str, session_id: str, settings: Any
) -> None:
    settings.PRODUCER_MOST_NUDGES = 2
    _card_shown(producer, product_page_url)
    producer.told("ok go", ("plan_ad", {"why": "They said go."}), "Pick a plan on the card.")

    assert plan_card.LOCKED in results_of("plan_ad")[0]
    assert Job.objects.get().chosen_plan is None
    assert not SystemNote.objects.filter(kind=SystemNote.Kind.NUDGE).exists()


def test_a_question_while_the_card_is_open_gets_an_answer_and_the_card_stays(
    producer: ScriptedProducer, product_page_url: str, api: APIClient, session_id: str
) -> None:
    _card_shown(producer, product_page_url)
    producer.told(
        "What's the difference between the first two?",
        "The first is about heat, the second about fitting the cup holder.",
    )

    assert chat(api, session_id)[-1] == (
        "agent",
        "The first is about heat, the second about fitting the cup holder.",
    )
    assert _the_card(api, session_id)["state"] == "open"
    assert Job.objects.get().model_calls.filter(purpose="plan_ad").count() == 0


def _cards(api: APIClient, session_id: str) -> list[dict[str, Any]]:
    return [m["plan_card"] for m in _messages(api, session_id) if m.get("plan_card")]


def test_a_changed_plan_replaces_the_card_and_only_the_newest_takes_an_answer(
    producer: ScriptedProducer, product_page_url: str, api: APIClient, session_id: str
) -> None:
    _card_shown(producer, product_page_url)
    longer = {**CARD, "plans": [{**plan, "seconds": 30} for plan in PLANS]}
    producer.told(
        "Make them 30 seconds",
        turn(calls=[("show_plan_card", {**longer, "why": "They want 30 seconds."})]),
    )

    old, new = _cards(api, session_id)
    assert old["state"] == "replaced"
    assert new["state"] == "open"
    assert [plan["seconds"] for plan in new["questions"][0]["choices"]] == [30, 30, 30]

    stale = _answer(api, session_id, old["id"], use_picks=True)

    assert stale.status_code == 409
    assert chat(api, session_id)[-1] == (
        "notice",
        "That card was replaced. Please answer the newest one.",
    )
    assert _kinds("plan_card_replaced") == ["plan_card_replaced"]
    assert Job.objects.get().chosen_plan is None

    producer.fake_model.respond("produce", turn(says="On it."))
    assert _answer(api, session_id, new["id"], use_picks=True).status_code == 201
    assert Job.objects.get().target_seconds == 30


IDEA_PLAN = {
    "style": "person_talking",
    "seconds": 25,
    "title": "Morning commute",
    "built_on": "It keeps coffee hot for the whole commute.",
    "opens": "Opens on a train platform at dawn.",
    "doubt": "Will it really stay hot?",
}


def test_an_idea_typed_on_the_card_becomes_the_plan_and_unlocks_once_the_producer_takes_it(
    producer: ScriptedProducer, product_page_url: str, api: APIClient, session_id: str
) -> None:
    _card_shown(producer, product_page_url)
    card_id = _the_card(api, session_id)["id"]
    producer.fake_model.respond("plan_ad", PLAN)
    producer.fake_model.respond(
        "produce",
        # Before it takes the idea, nothing can be planned.
        turn(calls=[("plan_ad", {"why": "Too soon."})]),
        turn(calls=[("take_owners_idea", {**IDEA_PLAN, "why": "Their idea."})]),
        turn(calls=[("plan_ad", {"why": "The plan is theirs."})]),
        turn(says="Here's the plan."),
    )

    answered = _answer(
        api, session_id, card_id, choices={}, idea="Someone drinking it on the morning train"
    )

    assert answered.status_code == 201, answered.json()
    assert chat(api, session_id)[-2] == (
        "user",
        "Which plan? My own idea: Someone drinking it on the morning train",
    )
    first, second = results_of("plan_ad")
    # The refusal points at their idea, not at a new card that would drop it.
    assert plan_card.LOCKED_FOR_THE_IDEA in first
    assert _chosen()["idea"] == "Someone drinking it on the morning train"
    assert _chosen()["plan"]["title"] == "Morning commute"
    assert Job.objects.get().target_seconds == 25
    assert Job.objects.get().scenes.count() == 3
    assert "their own idea" in str(given_to_the_producer(3))


def test_an_idea_that_cant_be_made_gets_a_reason_and_a_new_card(
    producer: ScriptedProducer, product_page_url: str, api: APIClient, session_id: str
) -> None:
    _card_shown(producer, product_page_url)
    card_id = _the_card(api, session_id)["id"]
    producer.fake_model.respond(
        "produce",
        turn(
            says="I can't make a step-by-step demo yet, so here are 3 plans that can.",
            calls=[("show_plan_card", {**CARD, "why": "The demo can't be made."})],
        ),
    )

    _answer(api, session_id, card_id, choices={}, idea="Show someone using it step by step")

    old, new = _cards(api, session_id)
    assert old["state"] == "answered"
    assert new["state"] == "open"
    assert Job.objects.get().chosen_plan is None
    assert not Job.objects.get().model_calls.filter(purpose="plan_ad").exists()


def test_an_answer_with_neither_a_plan_nor_an_idea_is_refused(
    producer: ScriptedProducer, product_page_url: str, api: APIClient, session_id: str
) -> None:
    _card_shown(producer, product_page_url)
    card_id = _the_card(api, session_id)["id"]

    refused = _answer(api, session_id, card_id, choices={}, idea="   ")

    assert refused.status_code == 400
    assert Job.objects.get().chosen_plan is None


def test_a_plan_of_scenes_that_couldnt_keep_the_chosen_plan_is_told(
    producer: ScriptedProducer, product_page_url: str, api: APIClient, session_id: str
) -> None:
    _card_shown(producer, product_page_url)
    card_id = _the_card(api, session_id)["id"]
    why = "The page shows no cup holder, so the ad opens on the mug in a hand instead."
    producer.fake_model.respond("plan_ad", {**PLAN, "plan_not_kept": why})
    producer.fake_model.respond(
        "produce", turn(calls=[("plan_ad", {"why": "Answered."})]), turn(says="Planned.")
    )

    _answer(api, session_id, card_id, choices={"plan": 1})

    assert _kinds("plan_not_kept") == ["plan_not_kept"]
    assert f"Tell the shop owner in one short sentence: {why}" in results_of("plan_ad")[0]


# A page selling two stones (#780): the picker names the options and labels each photo with
# the one it shows. I8 shows jade, I101 rose quartz, I102 both stones together.
def _with_options(options: list[str]) -> dict[str, Any]:
    pictures = [
        {**label("I8", 1, "side", "front", "The jade pendant from the front."), "option": "Jade"},
        {
            **label("I101", 2, "side", "back", "The rose quartz pendant from the back."),
            "option": "Rose quartz",
        },
        {**label("I102", 3, "side", "side", "Both pendants side by side."), "option": "all"},
    ]
    return {
        **shampoo_picked({"gallery_images": [8, 101, 102], "options": options}),
        "pictures": pictures,
    }


def _option_card(option: dict[str, Any] | None, named: str = "") -> dict[str, Any]:
    return {**CARD, "product_option": option, "option_named": named}


WHICH_STONE = {
    "asks": "Which stone?",
    "options": ["Jade", "Rose quartz"],
    "all_of_them": "Both stones",
    "pick": 1,
}


def _read_with_options(
    producer: ScriptedProducer, link: str, options: list[str], *calls: Any
) -> None:
    producer.fake_model.respond("check_page", READABLE)
    producer.fake_model.respond("pick_photos", _with_options(options))
    producer.told(
        f"Make an ad for {link}",
        ("read_page", {"link": link, "target_seconds": None, "why": "To start."}),
        *calls,
    )


def _numbers(items: list[Any]) -> list[str]:
    return [item.label for item in items]


def test_a_page_with_two_options_asks_which_and_the_ad_is_offered_only_that_options_photos(
    producer: ScriptedProducer,
    labelled_page: str,  # noqa: F811 (the fixture)
    api: APIClient,
    session_id: str,  # noqa: F811
) -> None:
    from jobs import library_items, library_sheets
    from jobs.work import end_card_photo

    _read_with_options(
        producer,
        labelled_page,
        ["Jade", "Rose quartz"],
        turn(calls=[("show_plan_card", {**_option_card(WHICH_STONE), "why": "Read."})]),
    )

    assert "The page sells these product options: Jade; Rose quartz." in results_of("read_page")[0]
    job = Job.objects.get()
    assert job.product_options == ["Jade", "Rose quartz"]
    # Each photo records the option it shows; one of both stones shows no single one.
    assert sorted(job.library.values_list("label", "option")) == [
        ("Both pendants side by side.", ""),
        ("The jade pendant from the front.", "Jade"),
        ("The rose quartz pendant from the back.", "Rose quartz"),
    ]
    card = _the_card(api, session_id)
    asked = card["questions"][0]
    assert asked["key"] == "product_option"
    assert asked["choices"] == ["Jade", "Rose quartz", "Both stones"]
    assert asked["pick"] == 0
    producer.fake_model.respond("produce", turn(says="On it."))

    answered = _answer(api, session_id, card["id"], choices={"product_option": 0, "plan": 0})

    assert answered.status_code == 201, answered.json()
    assert chat(api, session_id)[-2] == (
        "user",
        "Which stone? Jade\nWhich plan? “Hot all morning” (Person talking, 20 s)",
    )
    job.refresh_from_db()
    assert _chosen()["product_option"] == "Jade"
    # The planner's and scene agents' list, the senders' originals, the side picker's photos
    # and the end card: only jade and both stones, never rose quartz.
    offered = ["The jade pendant from the front.", "Both pendants side by side."]
    assert _numbers(library_items.originals(job)) == offered
    assert "rose quartz" not in library_items.library_text(job)
    assert "rose quartz" not in str(library_sheets.sheets_of(job))
    end_photo = end_card_photo(job)
    assert end_photo is not None and end_photo.option in ("", "Jade")


def test_all_of_the_options_narrows_nothing(
    producer: ScriptedProducer,
    labelled_page: str,  # noqa: F811 (the fixture)
    api: APIClient,
    session_id: str,  # noqa: F811
) -> None:
    from jobs import library_items

    _read_with_options(
        producer,
        labelled_page,
        ["Jade", "Rose quartz"],
        turn(calls=[("show_plan_card", {**_option_card(WHICH_STONE), "why": "Read."})]),
    )
    card = _the_card(api, session_id)
    producer.fake_model.respond("produce", turn(says="On it."))

    _answer(api, session_id, card["id"], choices={"product_option": 2, "plan": 0})

    job = Job.objects.get()
    assert _chosen()["product_option"] == "all"
    assert len(library_items.originals(job)) == 3


def test_a_card_must_ask_the_option_on_a_page_with_two_and_never_on_a_page_with_one(
    producer: ScriptedProducer,
    labelled_page: str,  # noqa: F811 (the fixture)
    api: APIClient,
    session_id: str,  # noqa: F811
) -> None:
    from jobs import library_items

    _read_with_options(
        producer,
        labelled_page,
        ["Jade", "Rose quartz"],
        ("show_plan_card", {**CARD, "why": "Read."}),
        # They named the stone in their first message, so the card doesn't ask it.
        turn(calls=[("show_plan_card", {**_option_card(None, "rose quartz"), "why": "Named."})]),
    )

    [refused] = results_of("show_plan_card")[:1]
    assert "the page sells 2 product options (Jade; Rose quartz)" in refused
    card = _the_card(api, session_id)
    assert [q["key"] for q in card["questions"]] == ["plan"]
    producer.fake_model.respond("produce", turn(says="On it."))
    _answer(api, session_id, card["id"], use_picks=True)
    job = Job.objects.get()
    assert _chosen()["product_option"] == "rose quartz"
    assert _numbers(library_items.originals(job)) == [
        "The rose quartz pendant from the back.",
        "Both pendants side by side.",
    ]


def test_a_page_with_one_option_gets_no_option_question(
    producer: ScriptedProducer, product_page_url: str, api: APIClient, session_id: str
) -> None:
    _card_shown(
        producer,
        product_page_url,
        turn(calls=[("show_plan_card", {**CARD, "why": "Fixed."})]),
        card=_option_card(WHICH_STONE),
    )

    assert "the page sells one product option" in results_of("show_plan_card")[0]
    assert [q["key"] for q in _the_card(api, session_id)["questions"]] == ["plan"]


def test_an_option_no_photo_shows_is_told_and_a_new_card_shown_with_nothing_made(
    producer: ScriptedProducer,
    labelled_page: str,  # noqa: F811 (the fixture)
    api: APIClient,
    session_id: str,  # noqa: F811
) -> None:
    three = {**WHICH_STONE, "options": ["Jade", "Rose quartz", "Onyx"], "all_of_them": ""}
    _read_with_options(
        producer,
        labelled_page,
        ["Jade", "Rose quartz", "Onyx"],
        turn(calls=[("show_plan_card", {**_option_card(three), "why": "Read."})]),
    )
    card = _the_card(api, session_id)
    producer.fake_model.respond(
        "produce",
        turn(calls=[("plan_ad", {"why": "Answered."})]),
        turn(calls=[("show_plan_card", {**_option_card(WHICH_STONE), "why": "No onyx photo."})]),
    )

    answered = _answer(api, session_id, card["id"], choices={"product_option": 2, "plan": 0})

    assert answered.status_code == 201, answered.json()
    assert "none of the product's photos shows it" in str(given_to_the_producer(3))
    assert plan_card.LOCKED in results_of("plan_ad")[0]
    # The shop owner is told, after their answer.
    assert chat(api, session_id)[2:4] == [
        ("user", "Which stone? Onyx\nWhich plan? “Hot all morning” (Person talking, 20 s)"),
        (
            "notice",
            "None of the page's photos shows Onyx, so I can't make the ad of it yet. Pick "
            "another option on the new card, or send a photo of it.",
        ),
    ]
    job = Job.objects.get()
    assert job.model_calls.filter(purpose="plan_ad").count() == 0
    assert not job.scenes.exists()
    cards = [m["plan_card"] for m in _messages(api, session_id) if m.get("plan_card")]
    assert [c["state"] for c in cards] == ["answered", "open"]


def test_a_product_whose_photos_show_no_single_option_is_never_held_for_one(
    producer: ScriptedProducer,
    labelled_page: str,  # noqa: F811 (the fixture)
    api: APIClient,
    session_id: str,
) -> None:
    from jobs import library_items

    # Sizes that look alike: the picker labels every photo as all of them.
    sizes = _with_options(["Small", "Large"])
    sizes["pictures"] = [{**picture, "option": "all"} for picture in sizes["pictures"]]
    producer.fake_model.respond("check_page", READABLE)
    producer.fake_model.respond("pick_photos", sizes)
    which = {"asks": "Which size?", "options": ["Small", "Large"], "all_of_them": "", "pick": 1}
    producer.told(
        f"Make an ad for {labelled_page}",
        ("read_page", {"link": labelled_page, "target_seconds": None, "why": "To start."}),
        turn(calls=[("show_plan_card", {**_option_card(which), "why": "Read."})]),
    )
    card = _the_card(api, session_id)
    producer.fake_model.respond("produce", turn(says="On it."))

    _answer(api, session_id, card["id"], choices={"product_option": 0, "plan": 0})

    assert _chosen()["plan"]["title"] == "Hot all morning"
    assert "option_without_photos" not in _chosen()
    assert len(library_items.originals(Job.objects.get())) == 3


def test_an_idea_for_an_option_no_photo_shows_cant_be_taken(
    producer: ScriptedProducer,
    labelled_page: str,  # noqa: F811 (the fixture)
    api: APIClient,
    session_id: str,
) -> None:
    three = {**WHICH_STONE, "options": ["Jade", "Rose quartz", "Onyx"], "all_of_them": ""}
    _read_with_options(
        producer,
        labelled_page,
        ["Jade", "Rose quartz", "Onyx"],
        turn(calls=[("show_plan_card", {**_option_card(three), "why": "Read."})]),
    )
    card = _the_card(api, session_id)
    producer.fake_model.respond(
        "produce",
        turn(calls=[("take_owners_idea", {**IDEA_PLAN, "why": "Their idea."})]),
        turn(calls=[("show_plan_card", {**_option_card(WHICH_STONE), "why": "No onyx."})]),
    )

    _answer(
        api, session_id, card["id"], choices={"product_option": 2}, idea="On a black velvet tray"
    )

    assert "none of the photos shows Onyx" in results_of("take_owners_idea")[0]
    # The new card waits for a new answer.
    assert Job.objects.get().chosen_plan is None
    assert not Job.objects.get().scenes.exists()


SHOTS_PLAN: dict[str, Any] = {
    "story": "The mug from every side.",
    "built_on": "It is hand-thrown.",
    "scenes": [{"shows": "The mug.", "photo": 1, "move": "push in", "overlay": "", "seconds": 4}],
    "music_mood": "calm piano",
    "product_name": "Stoneware Mug",
    "product_colour": "green",
    "end_card_line": "Hand-thrown.",
    "end_card_price": "",
}


def test_a_product_shots_plan_is_locked_till_answered_and_then_planned_with_its_own_tool(
    producer: ScriptedProducer, product_page_url: str, api: APIClient, session_id: str
) -> None:
    shots = {**CARD, "plans": [a_plan("Shine", style="product_shots", seconds=15), *PLANS[1:]]}
    producer.fake_model.respond("check_page", READABLE)
    producer.told(
        f"Make an ad for {product_page_url}",
        ("read_page", {"link": product_page_url, "target_seconds": None, "why": "To start."}),
        ("plan_product_shots", {**SHOTS_PLAN, "why": "Too soon."}),
        turn(calls=[("show_plan_card", {**shots, "why": "The page is read."})]),
    )
    [refused] = results_of("plan_product_shots")
    assert plan_card.LOCKED in refused
    card = _the_card(api, session_id)
    assert card["questions"][0]["choices"][0]["style"] == "Product shots"
    producer.fake_model.respond("produce", turn(says="On it."))

    _answer(api, session_id, card["id"], use_picks=True)

    assert _chosen()["plan"]["style"] == "product_shots"
    assert Job.objects.get().target_seconds == 15
    assert "Plan the ad from it now with plan_product_shots." in str(given_to_the_producer(4))
