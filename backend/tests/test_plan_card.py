"""The plan card (spec #768, ticket #777): after the page is read the producer shows a card
with 3 plans, nothing is made until the shop owner answers it, and the ad is made of the
chosen plan's style and length. Through the chat, the producer's model faked and scripted."""

from typing import Any

import pytest
from rest_framework.test import APIClient

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
)

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
    assert "plan card must be shown and answered first" in refused
    assert "plan card must be shown and answered first" in results_of("create_person")[0]
    assert _kinds("plan_card_locked") == ["plan_card_locked", "plan_card_locked"]
    assert _the_card(api, session_id)["state"] == "open"
    assert not Job.objects.get().scenes.exists()


@pytest.mark.parametrize(
    ("change", "fix"),
    [
        ({"plans": PLANS[:2]}, "it has 2 plans, and a card has exactly 3"),
        ({"plans_pick": 4}, "the plans' pick is 4"),
        (
            {"plans": [a_plan("Shine", style="product_shots", seconds=15), *PLANS[1:]]},
            'plan 1 is "product_shots", which the app can\'t make yet',
        ),
        (
            {"other": {"asks": "Which colour?", "choices": ["Red"], "pick": 1}},
            "the other question has 1 choices",
        ),
    ],
    ids=["two plans", "pick out of range", "a style the app can't make", "one choice"],
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

    assert "plan card must be shown and answered first" in results_of("plan_ad")[0]
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
    assert "plan card must be shown and answered first" in first
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
