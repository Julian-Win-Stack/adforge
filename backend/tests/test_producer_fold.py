"""The producer's context (spec #247 point 15; ticket #253), driven through the chat: the
producer's conversation holds everything in full until its last turn's input tokens, as the
model reported them, pass the fold point, half the model's window; then its oldest tool calls
fold to one line each, newest kept whole, and the line is kept on the call's row. A snapshot
of the ad as it stands is always given last. The producer and every other model are faked at
the gateway; the fake producer reports a turn's input tokens by the size of what it was
given, as a model does."""

import dataclasses
import json
from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest
from django.db.models.signals import post_save
from django.utils import timezone
from pytest_django import Settings
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from agents.fold import fold_if_past, fold_point
from agents.models import ToolCall
from agents.money import dollars
from agents.tasks import restart_dead_producers
from chat.models import Session
from gateway import catalog
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import TurnReply, TurnRequest
from jobs.library import keep_sent_list
from jobs.models import Job, ProducedItem, Scene

from .conftest import (
    PLAN,
    a_producer_last_beat,
    chat,
    given_to_the_producer,
    producer_turns,
    results_of,
    sent_list_in_the_store,
)
from .test_producer_clip import clipped, pictured, ready, spoken  # noqa: F401 (fixtures)
from .test_producer_plan import planning_through_openai
from .test_producer_restarts import WorkerStopped, the_worker_stops

pytestmark = pytest.mark.django_db(transaction=True)


def size_of(request: TurnRequest) -> int:
    """About how many tokens a turn's handoff is, as a model would count it."""
    return len(json.dumps(request.handoff.model_dump(mode="json"))) // 4


@pytest.fixture
def sized(fake_model: FakeModel, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake producer reports each turn's input tokens by the size of what it was given."""
    taken = fake_model.take_turn

    def take_turn(request: TurnRequest) -> TurnReply:
        return dataclasses.replace(taken(request), input_tokens=size_of(request))

    monkeypatch.setattr(fake_model, "take_turn", take_turn)


def last_turn_tokens() -> int:
    tokens = ModelCall.objects.filter(purpose="produce").order_by("id").last()
    assert tokens is not None and tokens.input_tokens is not None
    return tokens.input_tokens


def fold_past(settings: Settings, tokens: int) -> None:
    """Set the fold point to `tokens` of the producer model's window."""
    settings.FOLD_PAST_SHARE_OF_WINDOW = tokens / catalog.max_input_tokens(catalog.PRODUCER_MODEL)


def call(tool: str) -> ToolCall:
    return ToolCall.objects.get(tool=tool)


@pytest.fixture
def past_the_fold_point(sized: None, checked: None, settings: Settings) -> Iterator[int]:
    """A planned and checked ad, four tool calls made, whose producer's next turn passes the
    fold point: its last turn was as big as the fold point. Gives the fold point."""
    point = last_turn_tokens()
    fold_past(settings, point)
    yield point


def test_past_the_fold_point_the_oldest_calls_fold_to_a_line_and_the_next_turn_fits(
    api: APIClient,
    fake_model: FakeModel,
    session_id: str,
    say: Callable[..., None],
    past_the_fold_point: int,
) -> None:
    before = chat(api, session_id)
    fake_model.respond("produce", turn(says="The script is checked and ready."))

    say("How is it going?")

    # The oldest half of the calls given in full folded, as each row keeps it: tool,
    # outcome, cost.
    folded = call("read_page")
    assert folded.fold_line.startswith("read_page: made")
    assert "$" in folded.fold_line and "\n" not in folded.fold_line
    # What its own work cost: reading and checking the page.
    assert folded.fold_line.endswith("; cost $0.0126.")
    assert call("plan_ad").fold_line.startswith("plan_ad: made")
    # Nothing is deleted: the row keeps its whole answer.
    assert "Kept 2 product photos" in folded.result
    # Newest kept whole.
    assert call("create_person").fold_line == ""
    assert call("run_planning_checks").fold_line == ""

    fake_model.respond("produce", turn(says="Nothing yet."))
    say("Anything new?")

    given = given_to_the_producer(producer_turns())
    (read,) = [each for each in given if each.get("tool") == "read_page"]
    assert read["result"].startswith(folded.fold_line)
    assert "pictures" not in read
    (person,) = [each for each in given if each.get("tool") == "create_person"]
    assert person["result"] == call("create_person").result
    # The turn after the fold fits, so nothing more is folded.
    assert last_turn_tokens() < past_the_fold_point
    assert call("create_person").fold_line == ""
    # The shop owner sees nothing of it.
    assert chat(api, session_id) == [
        *before,
        ("user", "How is it going?"),
        ("agent", "The script is checked and ready."),
        ("user", "Anything new?"),
        ("agent", "Nothing yet."),
    ]


def test_a_producer_that_folded_replied_then_died_takes_no_turn_when_started_again(
    api: APIClient,
    fake_model: FakeModel,
    session_id: str,
    say: Callable[..., None],
    past_the_fold_point: int,
) -> None:
    fake_model.respond("produce", turn(says="The script is checked and ready."))
    say("How is it going?")
    assert call("read_page").fold_line
    turns = producer_turns()
    # It died after replying, before it turned its flag off.
    a_producer_last_beat(session_id, seconds_before_it_counts_as_dead=-1)
    # What it would say again, were it wrongly given a turn.
    fake_model.respond("produce", turn(says="The script is checked and ready."))

    restart_dead_producers()

    assert producer_turns() == turns
    assert chat(api, session_id)[-1] == ("agent", "The script is checked and ready.")
    assert not Session.objects.get(pk=session_id).producer_running


def test_a_turn_paid_for_past_the_fold_point_before_the_worker_stopped_isnt_paid_again(
    api: APIClient,
    fake_model: FakeModel,
    session_id: str,
    say: Callable[..., None],
    past_the_fold_point: int,
) -> None:
    turns = producer_turns()
    fake_model.respond("produce", turn(calls=[("read_ad", {"scene": 2})]))
    turn_paid_for = the_worker_stops(
        post_save, ModelCall, when=lambda paid: paid.purpose == "produce"
    )
    with turn_paid_for, pytest.raises(WorkerStopped):
        say("What is scene 2?")
    # Nothing was kept of the turn, so nothing folded yet.
    assert not ToolCall.objects.exclude(fold_line="").exists()
    a_producer_last_beat(session_id, seconds_before_it_counts_as_dead=-1)
    # Only the turn after it is scripted: paying for the stopped turn again would take this.
    fake_model.respond("produce", turn(says="Scene 2 is planned."))

    restart_dead_producers()

    # The turn paid for before the worker stopped, and the one after it.
    assert producer_turns() == turns + 2
    assert chat(api, session_id)[-1] == ("agent", "Scene 2 is planned.")
    assert len(results_of("read_ad")) == 1
    # Folded once, for the turn kept: the oldest half of the four calls it was given whole.
    assert list(
        ToolCall.objects.exclude(fold_line="").order_by("id").values_list("tool", flat=True)
    ) == ["read_page", "plan_ad"]
    # Folded with the turn kept, so the turn after it was given them folded.
    (read,) = [each for each in given_to_the_producer(turns + 2) if each.get("tool") == "read_page"]
    assert read["result"].startswith(call("read_page").fold_line)


def test_a_turn_paid_for_before_the_worker_stopped_isnt_paid_again_after_work_changed_the_ad(
    api: APIClient,
    fake_model: FakeModel,
    session_id: str,
    say: Callable[..., None],
    past_the_fold_point: int,
) -> None:
    turns = producer_turns()
    fake_model.respond("produce", turn(calls=[("read_ad", {"scene": 2})]))
    turn_paid_for = the_worker_stops(
        post_save, ModelCall, when=lambda paid: paid.purpose == "produce"
    )
    with turn_paid_for, pytest.raises(WorkerStopped):
        say("What is scene 2?")
    # Work running in the background changes the ad meanwhile, as a fix filming scene 2
    # again with a new line does, so the ad as it stands is not what the paid turn was given.
    given = snapshot_given(producer_turns())
    Scene.objects.filter(number=2).update(line="A new line for scene two, filmed again.")
    a_producer_last_beat(session_id, seconds_before_it_counts_as_dead=-1)
    # Only the turn after it is scripted: paying for the stopped turn again would take this.
    fake_model.respond("produce", turn(says="Scene 2 is being filmed again."))

    restart_dead_producers()

    assert producer_turns() == turns + 2
    assert chat(api, session_id)[-1] == ("agent", "Scene 2 is being filmed again.")
    # The paid turn was handed back, and the call it asked for ran.
    assert len(results_of("read_ad")) == 1
    # The turn after it is given the ad as it stands now.
    assert snapshot_given(producer_turns()) != given
    assert "A new line for scene two, filmed again." in snapshot_given(producer_turns())


def snapshot_given(number: int) -> str:
    """The snapshot of the ad the producer was given last on its `number`th turn (from 1)."""
    calls = ModelCall.objects.filter(purpose="produce").order_by("created_at", "id")
    snapshot: str = calls[number - 1].handoff.get("snapshot", "")
    return snapshot


@pytest.fixture
def sent(clipped: None) -> None:  # noqa: F811 (the fixture imported above)
    """Scene 1's clip carries the sent list its video API got."""
    clip = Job.objects.get().produced.get(kind=ProducedItem.Kind.CLIP, scene__number=1)
    keep_sent_list(clip, sent_list_in_the_store())


def test_the_ad_as_it_stands_is_given_last_and_any_part_is_read_again_in_full(
    fake_model: FakeModel, say: Callable[..., None], sent: None
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("read_ad", {"scene": 1}), ("read_ad", {"scene": None})]),
        turn(says="Scene 1 is made."),
    )

    say("Where are we?")

    snapshot = snapshot_given(producer_turns())
    # The plan: each scene's line.
    for line in Job.objects.get().scenes.values_list("line", flat=True):
        assert line in snapshot
    # Per scene its versions, what its clip was sent, and what it cost.
    assert "Scene 1" in snapshot and "v1 first version" in snapshot
    assert "library items 14, 4, 9, 11, 12, 13, the person in the room, 2, 3" in snapshot
    assert "left out: library item 5" in snapshot
    assert "It cost $0.213694." in snapshot
    assert "Scene 2: no version yet. It cost $0.00." in snapshot
    # And all the chat has spent before that turn: every model call, its own turns too.
    given_with = ModelCall.objects.filter(purpose="produce").order_by("created_at", "id")
    before = ModelCall.objects.filter(created_at__lt=list(given_with)[-1].created_at)
    spent = sum((each.cost_usd or Decimal(0) for each in before), Decimal(0))
    assert f"Spent in this chat so far, your own turns included: {dollars(spent)}." in snapshot
    # Not in the conversation itself, which stays as it happened.
    assert snapshot.splitlines()[0] not in json.dumps(given_to_the_producer(producer_turns()))
    # A scene read in full: its sent list's labels, tiers and pieces, and its prompt.
    scene, whole = results_of("read_ad")
    assert "Image 2 (tier 2, for in-use): A hand squeezing the press over a bowl." in scene
    assert "Video 2: page video 2, moment 1, 3-13 s (pieces_over_limit)" in scene
    assert "Left out: The garlic press, closed, seen from the top. (tier 4)" in scene
    # The whole ad: the plan in full and every scene.
    assert "The product's name, said to camera: Stoneware Mug" in whole
    assert "Scene 3" in whole


def test_a_folded_calls_whole_answer_is_read_again_by_its_call_id(
    fake_model: FakeModel, say: Callable[..., None], past_the_fold_point: int
) -> None:
    fake_model.respond("produce", turn(says="The script is checked and ready."))
    say("How is it going?")
    folded = call("read_page")
    fake_model.respond(
        "produce",
        turn(calls=[("read_earlier_call", {"call_id": folded.call_id})]),
        turn(says="Your page had 2 photos."),
    )
    say("How many photos did the page have?")

    # The turn after the fold is given it folded, with how to read it again.
    (read,) = [
        each
        for each in given_to_the_producer(producer_turns() - 1)
        if each.get("tool") == "read_page"
    ]
    assert folded.call_id in read["result"]
    assert results_of("read_earlier_call") == [f"Your read_page call answered:\n{folded.result}"]


def test_the_snapshot_reaches_the_model_last_after_the_conversation(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    planning_through_openai(openai_server, product_page_url, PLAN)

    say(f"Make an ad for {product_page_url}")

    turns = [
        json.loads(request.get_data())
        for request, _ in httpserver.log
        if request.path == "/v1/responses" and "tools" in json.loads(request.get_data())
    ]
    # Planned only by the second turn: the last one, after the plan, is given the ad.
    *_, given = turns[-1]["input"]
    assert given.get("role") == "developer"
    assert given["content"].startswith("From the app, not the shop owner: the ad as it stands")
    assert "Meet the Stoneware Mug from Kiln & Co." in given["content"]
    assert all("ad as it stands" not in json.dumps(each) for each in turns[0]["input"])


@pytest.mark.parametrize(
    ("azure", "window"), [(0, 922_000), (300_000, 300_000), (2_000_000, 922_000)]
)
def test_the_producers_window_is_its_models_unless_the_azure_deployments_is_lower(
    settings: Settings, azure: int, window: int
) -> None:
    settings.AZURE_OPENAI_MAX_INPUT_TOKENS = azure
    settings.FOLD_PAST_SHARE_OF_WINDOW = 0.5

    assert fold_point("produce") == window // 2


def test_the_whole_window_is_the_input_the_models_page_gives(settings: Settings) -> None:
    settings.AZURE_OPENAI_MAX_INPUT_TOKENS = 0
    settings.FOLD_PAST_SHARE_OF_WINDOW = 1.0

    # gpt-5.6-sol takes at most 922,000 tokens in (its model page, 2026-10-29).
    assert fold_point("produce") == 922_000


# --- The fold rule (unit, in support) ---------------------------------------------------------


def test_short_oldest_calls_never_hold_back_long_newer_ones_from_folding(
    settings: Settings,
) -> None:
    session = Session.objects.create()
    # Ten calls whose one-sentence answers would only grow as a line, then four long ones.
    for number in range(14):
        long = number >= 10
        ToolCall.objects.create(
            session=session,
            agent="producer",
            tool="read_ad" if long else "make_line_audio",
            call_id=f"call_{number}",
            arguments={} if long else {"scene": 1},
            result=("A long answer. " * 13_000) if long else "Started in the background.",
            answer="made",
            finished_at=timezone.now(),
        )
    point = fold_point("produce")
    ModelCall.objects.create(
        session=session,
        purpose="produce",
        provider="fake",
        model=catalog.MODEL_FOR_PURPOSE["produce"],
        attempt=1,
        handoff={},
        output={},
        outcome=ModelCall.Outcome.SUCCEEDED,
        input_tokens=point + 1,
        duration_ms=10,
    )

    # Past the point turn after turn, the long calls fold, oldest first; the short stay whole.
    folded = [len(fold_if_past(session, "producer", "produce")) for _ in range(3)]

    assert folded == [2, 1, 1]
    assert set(ToolCall.objects.exclude(fold_line="").values_list("tool", flat=True)) == {"read_ad"}
