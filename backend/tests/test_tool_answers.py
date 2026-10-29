"""Tool answers (spec #247 points 6-7, 13; ticket #248), driven through the chat: every
producer tool answers the producer with the thing it made, "can't, because ..." with the
reason, or one clarifying question, and nothing a tool raises stops the run; every producer
tool call carries the producer's `why`, kept on the call's row. The producer is faked and
scripted at the gateway, as every other model is."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from agents import loop
from agents.loop import Agent, Asks, Refused, Tool
from agents.models import ToolCall
from agents.producer import PRODUCER
from chat import messages
from chat.models import Message, Session
from gateway.fake import DEFAULT_WHY, FakeModel, turn
from gateway.types import ToolRequest, ToolUse, Turn
from jobs.models import Job

from .conftest import READABLE, chat, given_to_the_producer

pytestmark = pytest.mark.django_db(transaction=True)


def test_every_producer_tool_call_keeps_the_why_the_producer_gave_it(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    why = "The shop owner sent a link, and every fact in the ad comes from its page."
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None, "why": why})]),
        turn(says="I read your mug's page."),
    )
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {product_page_url}")

    call = ToolCall.objects.get()
    assert call.why == why
    # The tool's own arguments, without the why.
    assert call.arguments == {"link": product_page_url, "target_seconds": None}
    # The producer is given its call back as it made it, why and all.
    used = given_to_the_producer(2)[-1]
    assert used["arguments"] == {"link": product_page_url, "target_seconds": None, "why": why}
    # Every producer tool asks for it.
    (asked,) = [each for each in fake_model.turns_taken if each.purpose == "produce"][:1]
    for spec in asked.tools:
        assert "why" in spec.arguments.model_json_schema()["required"], spec.name
    assert len(asked.tools) == len(PRODUCER.tools)


def test_a_scripted_producer_call_gets_a_default_why(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], api: APIClient
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        turn(says="I read your mug's page."),
    )
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {product_page_url}")

    assert ToolCall.objects.get().why == DEFAULT_WHY


def without_a_why(tool: str, arguments: dict[str, Any]) -> Turn:
    """A turn calling `tool` with no `why`, as a model that skipped it would."""
    return Turn(
        says="", calls=(ToolRequest(call_id=f"call_{uuid4().hex}", tool=tool, arguments=arguments),)
    )


def test_a_call_without_a_why_is_refused_once_with_the_reason_and_runs_when_made_again(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    read = {"link": product_page_url, "target_seconds": None}
    fake_model.respond(
        "produce",
        without_a_why("read_page", read),
        without_a_why("read_page", read),
        turn(says="I read your mug's page."),
    )
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {product_page_url}")

    refused, ran = ToolCall.objects.order_by("id")
    # Told why, and nothing was done: nothing paid, no job yet.
    assert refused.answer == "cant"
    assert refused.result.startswith("Refused: the call has no `why`.")
    assert refused.job is None
    # Made again without one, it runs: a missing reason never stops the work, and the chat
    # says so in red, with a warning line.
    assert (ran.answer, ran.why) == ("made", "")
    assert "Kept 2 product photos" in ran.result
    said = "The producer made its read_page call again without saying why"
    assert [text for role, text in chat(api, session_id) if role == "notice" and said in text]
    assert [each for each in Job.objects.get().warnings if said in each["text"]]


class Pick(Tool):
    """Pick a colour. A test tool: it can't tell which colour is meant from a blank one, and
    breaks on "boom"."""

    name = "pick"

    colour: str

    def run(self, call: ToolCall) -> str:
        if not self.colour:
            raise Asks("Which colour, the lid's or the mug's?")
        if self.colour == "boom":
            raise RuntimeError("the database password is hunter2")
        if self.colour == "none":
            raise Refused("there is no colour called none.")
        return f"Picked {self.colour}."


PICKER = Agent(name="picker", purpose="produce", instructions="Pick.", tools=[Pick], asks_why=True)


def test_each_tool_answer_is_made_cant_or_one_question_and_none_stops_the_run(
    fake_model: FakeModel, session_id: str
) -> None:
    session = Session.objects.get(pk=session_id)
    messages.add(session, role=Message.Role.USER, text="Pick a colour.")
    fake_model.respond(
        "produce",
        turn(calls=[("pick", {"colour": ""})]),
        turn(calls=[("pick", {"colour": "none"}), ("pick", {"colour": "boom"})]),
        turn(calls=[("pick", {"colour": "green"})]),
        turn(says="Picked green."),
    )

    loop.run(PICKER, session)

    answers = [
        loop.ToolAnswer.of(call) for call in ToolCall.objects.filter(agent="picker").order_by("id")
    ]
    assert [(answer.kind, answer.text) for answer in answers] == [
        (
            "question",
            "Question: Which colour, the lid's or the mug's? Nothing was done. Answer it from "
            "what you already know and call the tool again with the answer; ask the shop owner "
            "only when nothing you know answers it.",
        ),
        ("cant", "Refused: there is no colour called none. Nothing was done."),
        (
            "cant",
            "Failed: an unexpected error stopped the tool. The details are in the server log.",
        ),
        ("made", "Picked green."),
    ]
    # Each answer reached the agent, which went on to reply.
    assert session.messages.filter(role=Message.Role.AGENT).latest("seq").text == "Picked green."


def last_read_page() -> ToolCall:
    return ToolCall.objects.filter(tool="read_page").latest("id")


# --- The hard list (spec #247 point 13) ------------------------------------------------------


def test_a_second_product_page_in_the_session_is_answered_start_a_new_ad_for_that(
    fake_model: FakeModel, page_read: str, httpserver: HTTPServer, say: Callable[..., None]
) -> None:
    other = httpserver.url_for("/products/teapot")
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": other, "target_seconds": None})]),
        turn(says="That's another product: start a new ad for it."),
    )
    asked_before = len(httpserver.log)

    say(f"Now do my other product: {other}")

    call = last_read_page()
    assert call.answer == "cant"
    assert "start a new ad for that" in call.result
    # Refused before anything is read: the other page isn't even asked for, and the ad's
    # page stays the one read.
    assert len(httpserver.log) == asked_before
    assert Job.objects.get().product_url == page_read


@pytest.mark.parametrize(
    "same",
    [
        pytest.param("{link}?utm_source=newsletter&fbclid=abc", id="tracking added"),
        pytest.param("{link}/", id="trailing slash"),
        pytest.param("{link}#reviews", id="a place on the page"),
    ],
)
def test_the_same_product_at_another_form_of_its_link_is_not_a_second_product(
    fake_model: FakeModel, page_read: str, say: Callable[..., None], same: str
) -> None:
    link = same.format(link=page_read)
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": link, "target_seconds": None})]),
        turn(says="I already have that page."),
    )

    say(f"Here it is again: {link}")

    call = last_read_page()
    assert call.answer == "made"
    assert "was already read for this ad" in call.result


# --- The contract's fixture (spec #247 point 20), for the agent page and the fold ------------

TOOL_ANSWERS = Path(__file__).parent / "fixtures" / "tool_answers.json"


def test_the_fixtures_tool_call_why_cant_and_fold_line_are_the_codes_shapes(
    session_id: str,
) -> None:
    fixture = json.loads(TOOL_ANSWERS.read_text())
    row = {key: value for key, value in fixture["tool_call"].items() if key != "given"}
    call = ToolCall.objects.create(session_id=session_id, **row, fold_line=fixture["fold_line"])

    # The row's why goes back to the agent with its call, as the fixture's given form says.
    given = ToolUse.model_validate(fixture["tool_call"]["given"])
    assert given.arguments == {**call.arguments, "why": call.why}
    assert loop.ToolAnswer.of(call).kind == "made"
    cant = loop.ToolAnswer.from_json(fixture["cant"])
    assert (cant.kind, cant.as_json()) == ("cant", fixture["cant"])
    assert loop.ToolAnswer.kind_of(cant.text) == "cant"
    assert ToolCall.objects.get(pk=call.pk).fold_line == fixture["fold_line"]
