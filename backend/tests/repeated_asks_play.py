"""The repeated-ask eval's session player (evals/repeated_asks): plays one scripted owner
session through the real chat and saves what happened. Not a test of its own: CI never
collects it (no test_ prefix). `python -m evals.repeated_asks run` runs it with pytest, which
gives every parallel worker its own database; the eval's tests call `play` with the producer
scripted.

Each session: a made-up shop's page is served on this machine, and a finished three-scene ad
is made for it with every model faked. Then the owner's scripted messages are sent one by one,
each once the producer's turn and every scene step it started have finished. From there only
the producer's own turns may be real (REPEATED_ASKS_REAL=1): its scene agents film or give up
as the script says, every check passes, and Look answers the script's sentence. Nothing calls
a paid picture, video, voice or page service."""

import importlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.db.models import Sum
from pytest_django import Settings as SettingsWrapper
from pytest_httpserver import HTTPServer

from adforge import settings as app_settings
from agents.models import SystemNote, ToolCall
from agents.scene_agent import PENDING_NOTE_DELIVERED
from chat.models import Session
from evals.repeated_asks.cases import Case, Shop, load_cases
from gateway import gateway
from gateway.fake import FakeModel, turn
from gateway.gateway import running_tool
from gateway.models import ModelCall
from gateway.types import ToolUse, Turn, TurnReply, TurnRequest
from jobs.models import Job, ProducedItem, SceneAgentRun

from .conftest import NO_CHOICES, READABLE, HeldSteps, a_plan_with, broll, facts_ok, picture
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

pytestmark = pytest.mark.django_db(transaction=True)

# Scene steps one owner message may lead to before the session is stopped as runaway.
MOST_STEPS_PER_MESSAGE = 15
# Turns the faked scene agent takes in one run before it is stopped as stuck.
MOST_TURNS_PER_RUN = 8
# The run's quote keys that tell code's own runs apart (agents/scene_recovery.py).
RETRY = "producer_recovery"
TALKING = "talking_recovery"

PAGE = """<!doctype html>
<html>
<head>
  <title>{product} | {brand}</title>
  <meta property="og:image" content="{side}">
  <script type="application/ld+json">
    {{"@context": "https://schema.org", "@type": "Product", "name": "{product}",
      "image": ["/cdn/front.png", "{side}"],
      "brand": {{"@type": "Brand", "name": "{brand}"}},
      "offers": {{"@type": "Offer", "price": "{price}", "priceCurrency": "USD",
                  "availability": "https://schema.org/InStock"}}}}
  </script>
</head>
<body>
  <h1>{product}</h1>
  <p class="price">${price}</p>
  <p>{page_text}</p>
</body>
</html>
"""


@dataclass
class Script:
    """The owner message being worked on, which every faked step follows."""

    case: Case
    now: int = 0

    @property
    def message(self) -> Any:
        return self.case.messages[self.now]


@dataclass
class Before:
    seq: int
    tool_call: int
    note: int
    run: int
    model_call: int


@dataclass
class Played:
    """What one session saved, as written to record.json."""

    record: dict[str, Any] = field(default_factory=dict)


def serve_shop(httpserver: HTTPServer, shop: Shop) -> str:
    """The shop's product page and its two photos, served here. Gives the page's link."""
    side = httpserver.url_for(f"/cdn/{shop.key}-side.png")
    page = PAGE.format(
        side=side,
        **{k: getattr(shop, k) for k in ("product", "brand", "price")},
        page_text=shop.page_text,
    )
    httpserver.expect_request(f"/products/{shop.key}").respond_with_data(
        page, content_type="text/html; charset=utf-8"
    )
    httpserver.expect_request("/cdn/front.png").respond_with_data(
        picture(400, 300, (190, 120, 140)), content_type="image/png"
    )
    httpserver.expect_request(f"/cdn/{shop.key}-side.png").respond_with_data(
        picture(1600, 1200, (220, 220, 210)), content_type="image/png"
    )
    return httpserver.url_for(f"/products/{shop.key}")


def shop_plan(shop: Shop) -> dict[str, Any]:
    """The planner's answer for the shop: talking, B-roll, talking."""
    scenes = [
        {
            "line": shop.lines[0],
            "kind": "talking",
            "shows": shop.shows[0],
            "talking_style": "selfie",
            "product_state": "out of the box",
            "shows_to_lens": True,
        },
        broll(
            {
                "line": shop.lines[1],
                "shows": shop.shows[1],
                "kind_why": "This close view cannot be shown at arm's length.",
            }
        ),
        {
            "line": shop.lines[2],
            "kind": "talking",
            "shows": shop.shows[2],
            "talking_style": "propped",
            "product_state": "in use",
            "shows_to_lens": False,
        },
    ]
    return a_plan_with(
        scenes=scenes,
        product_name=shop.product,
        product_colour=shop.colour,
        real_size=shop.real_size,
        person_looks=shop.person,
        product_behaviour=shop.behaviour,
        brand=shop.brand,
        end_card_line=shop.page_text,
        end_card_price=f"${shop.price}",
    )


def _in_words(form: dict[str, Any], shop: Shop) -> dict[str, Any]:
    """A test form about the mug, about the shop's product instead."""
    changed: dict[str, Any] = json.loads(json.dumps(form).replace("mug", shop.product))
    return changed


def _current_run() -> SceneAgentRun | None:
    return (
        SceneAgentRun.objects.filter(finished_at__isnull=True)
        .exclude(reason=PENDING_NOTE_DELIVERED)
        .order_by("-pk")
        .first()
    )


def fake_the_rest(fake_model: FakeModel, script: Script) -> None:
    """Every model but the producer: scene agents draw and film, or give up as the script
    says; checks pass; Look answers the script's sentence."""
    shop = script.case.shop
    picture_form = _in_words(PICTURE_FORM, shop)
    film = _in_words(FILM, shop)

    turns: dict[int, int] = {}
    broken: set[int] = set()

    def scene_agent(request: TurnRequest) -> Turn:
        run = _current_run()
        code_run = run is not None and any(TALKING in quote for quote in run.quotes)
        ending = (
            script.message.ending_on(run.agent_fix.step.scene.number)
            if script.now >= 0 and run is not None
            else script.message.ending
            if script.now >= 0
            else "passes"
        )
        if not code_run and ending == "breaks" and script.now not in broken:
            # The message's first run fails for a technical reason, before any clip.
            broken.add(script.now)
            raise RuntimeError("Faked technical failure: the video service stopped answering.")
        if not code_run and ending in ("gives_up", "breaks"):
            return turn(calls=[("give_up", {"reason": script.message.reason})])
        drawn = [
            event
            for event in request.handoff.conversation
            if isinstance(event, ToolUse) and event.tool == "draw_starting_picture"
        ]
        turns[run.pk if run else 0] = turns.get(run.pk if run else 0, 0) + 1
        if turns[run.pk if run else 0] > MOST_TURNS_PER_RUN:
            last = request.handoff.conversation[-1]
            raise RuntimeError(f"the faked scene agent is stuck: {getattr(last, 'result', last)}")
        return turn(calls=[("film", film)] if drawn else [("draw_starting_picture", picture_form)])

    def look(_: object) -> dict[str, Any]:
        said = script.message.look
        fault = any(script.message.ending_on(n) != "passes" for n in (1, 2, 3))
        return {
            "sees": said,
            "wrong": said if fault else "Nothing",
            "root_cause": "",
            "direction": "",
        }

    def heard(_: object) -> dict[str, str]:
        call = running_tool()
        assert call is not None
        filmed = ModelCall.objects.filter(tool_call=call, purpose="make_broll_clip").latest("pk")
        said = re.search(r"<d>\[English\] (.*?)</d>", filmed.handoff["prompt"], re.DOTALL)
        assert said is not None
        return {"text": said[1]}

    def checked_lines(request: Any) -> dict[str, Any]:
        lines = request.handoff.model_dump().get("lines") or []
        return facts_ok(*[line["scene"] for line in lines])

    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.answer_unscripted("scene_agent", scene_agent)
    fake_model.answer_unscripted("look", look)
    fake_model.answer_unscripted("transcribe_line", heard)
    fake_model.answer_unscripted("fact_check", checked_lines)
    fake_model.answer_unscripted("check_page", lambda _: READABLE)


def make_the_ad(
    fake_model: FakeModel, say: Callable[..., None], steps: HeldSteps, link: str, shop: Shop
) -> None:
    """The finished ad the owner's messages are about, every model faked."""
    fake_model.respond("plan_ad", shop_plan(shop))
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": link, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(calls=[("create_music", {"mood": "light upbeat"})]),
        turn(
            calls=[
                (
                    "start_scene_agent",
                    {"scene": number, "instruction": "Film the planned scene.", "quotes": [link]},
                )
                for number in (1, 2, 3)
            ]
        ),
        turn(says="The planned scenes are being made."),
        turn(calls=[("assemble_ad", {})]),
        turn(says="Here is your finished ad."),
    )
    say(f"Make an ad for {link}")
    steps.run_held()
    job = Job.objects.get()
    assert job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).exists(), "no finished ad"
    left = fake_model.scripts_left()
    assert not left, f"the ad was made without using {left}"


def use_the_real_producer(fake_model: FakeModel, settings: SettingsWrapper) -> None:
    """From now on the producer's turns go to the real model, with the app's own settings
    for its nudges and retries."""
    from adforge.settings import openai_account

    key, address = openai_account(os.environ)
    settings.OPENAI_API_KEY = key
    settings.OPENAI_BASE_URL = address
    settings.OPENAI_TEXT_ON_AZURE = bool(os.environ.get("AZUREE_OPENAI_KEY"))
    # The app's fallback when Azure refuses a producer turn goes to OpenAI's own API, as in
    # the live app; without it the turn stopped with "I had to stop for a moment" (v2 run).
    settings.OPENAI_PICTURES_KEY = os.environ.get("OPENAI_API_KEY", "")
    settings.OPENAI_PICTURES_BASE_URL = os.environ.get("OPENAI_BASE_URL", "")
    settings.PRODUCER_MOST_NUDGES = app_settings.PRODUCER_MOST_NUDGES
    settings.PRODUCER_FILTER_RETRY_SECONDS = app_settings.PRODUCER_FILTER_RETRY_SECONDS
    gateway._openai.cache_clear()
    faked = fake_model.take_turn

    def take_turn(request: TurnRequest, *, on_openai: bool = False) -> TurnReply:
        if request.purpose == "produce":
            return gateway._openai().take_turn(request, on_openai=on_openai)
        return faked(request, on_openai=on_openai)

    fake_model.take_turn = take_turn  # type: ignore[method-assign]


def _last(model: Any) -> int:
    return int(model.objects.order_by("-pk").values_list("pk", flat=True).first() or 0)


def _before(session: Session) -> Before:
    seq = session.messages.order_by("-seq").values_list("seq", flat=True).first() or 0
    return Before(
        seq=seq,
        tool_call=_last(ToolCall),
        note=_last(SystemNote),
        run=_last(SceneAgentRun),
        model_call=_last(ModelCall),
    )


class Runaway(Exception):
    """One owner message led to more scene steps than any sane remake would."""


def _run_steps(steps: HeldSteps) -> None:
    ran = 0
    while steps.held:
        ran += 1
        if ran > MOST_STEPS_PER_MESSAGE:
            raise Runaway(f"more than {MOST_STEPS_PER_MESSAGE} scene steps for one message")
        steps.run_next()


def play(
    case: Case,
    *,
    repeat: int,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    say: Callable[..., None],
    steps: HeldSteps,
    after_the_ad: Callable[[], None],
) -> dict[str, Any]:
    """Play `case` and give its record. Once the ad is made, `after_the_ad` readies the
    producer for the owner's messages: the real model, or a test's scripted turns."""
    script = Script(case, now=-1)
    fake_the_rest(fake_model, script)
    link = serve_shop(httpserver, case.shop)
    make_the_ad(fake_model, say, steps, link, case.shop)
    after_the_ad()
    session = Session.objects.get()
    start = _before(session)
    asks: list[dict[str, Any]] = []
    error = ""
    for number, message in enumerate(case.messages):
        script.now = number
        before = _before(session)
        try:
            say(message.say)
            _run_steps(steps)
        except Exception as failed:  # a crash is a finding too: kept, the session stops
            error = f"{type(failed).__name__}: {failed}"
        asks.append(_ask(session, number + 1, message.say, before))
        if error:
            break
    return {
        "case": case.id,
        "kind": case.kind,
        "shop": case.shop.key,
        "repeat": repeat,
        "asks": asks,
        "problems": _problems(session),
        "cost_usd": float(_spent(start)),
        "error": error,
    }


def _ask(session: Session, number: int, said: str, before: Before) -> dict[str, Any]:
    messages = session.messages.filter(seq__gt=before.seq).order_by("seq")
    calls = ToolCall.objects.filter(
        session=session, agent="producer", pk__gt=before.tool_call
    ).order_by("pk")
    notes = SystemNote.objects.filter(session=session, pk__gt=before.note).order_by("pk")
    runs = (
        SceneAgentRun.objects.filter(pk__gt=before.run)
        .exclude(reason=PENDING_NOTE_DELIVERED)
        .exclude(reason="Superseded by a new owner note.")
        .order_by("pk")
    )
    return {
        "number": number,
        "said": said,
        "chat": [
            {"role": message.role, "text": message.text} for message in messages if message.text
        ],
        "tool_calls": [
            {"tool": call.tool, "arguments": call.arguments, "why": call.why, "result": call.result}
            for call in calls
        ],
        "system_notes": [{"kind": note.kind, "text": note.text} for note in notes],
        "runs": [_run(run) for run in runs],
        # Every "I had to stop for a moment" cut-off, with its developer reason.
        "cut_offs": _cut_offs(session, before),
    }


def _cut_offs(session: Session, before: Before) -> list[str]:
    from chat.models import Message

    stopped = session.messages.filter(seq__gt=before.seq, role=Message.Role.NOTICE).filter(
        text__startswith="I had to stop for a moment"
    )
    if not stopped.exists():
        return []
    job = session.jobs.first()
    since = session.messages.filter(seq=before.seq + 1).values_list("created_at", flat=True)
    start = since.first()
    why = [
        str(warning.get("text", ""))
        for warning in (job.warnings if job is not None else [])
        if warning.get("kind") == "producer_gave_up"
        and (start is None or str(warning.get("at", "")) >= start.isoformat())
    ]
    return why or ["(no developer reason recorded)"] * stopped.count()


def _run(run: SceneAgentRun) -> dict[str, Any]:
    quotes = run.quotes or []
    if any(TALKING in quote for quote in quotes):
        origin = "code"
    elif any(quote.get("owner_asked") for quote in quotes):
        origin = "owner"
    else:
        origin = "producer"
    handed_by = next(
        (quote["handed_by"] for quote in quotes if isinstance(quote.get("handed_by"), int)), None
    )
    tool = (
        ToolCall.objects.filter(pk=handed_by).values_list("tool", flat=True).first()
        if handed_by is not None
        else run.agent_fix.step.tool_call.tool
    )
    problem = next(
        (
            quote.get("thing", quote.get("problem"))
            for quote in quotes
            if isinstance(quote.get("thing", quote.get("problem")), int)
        ),
        None,
    )
    try:
        broke = importlib.import_module("agents.owner_problems").broke(run)
    except ImportError:
        broke = False
    return {
        "scene": run.agent_fix.step.scene.number,
        "broke": broke,
        "instruction": run.instruction,
        "origin": origin,
        "retry": any(RETRY in quote for quote in quotes),
        "tool": tool,
        "clips": run.clips,
        "ended": _ended(run),
        "problem": problem,
    }


def _ended(run: SceneAgentRun) -> str:
    fixing = run.agent_fix
    last = fixing.scene_runs.order_by("-pk").first()
    current = last is not None and last.pk == run.pk
    if run.reason:
        return run.reason
    if current and fixing.gave_up:
        return f"gave up: {fixing.gave_up}"
    if current and fixing.passed_id is not None:
        return "made a clip that passed its check"
    return "ended without a passing clip"


def _problems(session: Session) -> list[dict[str, Any]] | None:
    """The stored problem list, when the app under test keeps one."""
    try:
        # Imported by name: the module exists only on the prototype branch.
        owner_problems = importlib.import_module("agents.owner_problems")
    except ImportError:
        return None
    listed: list[dict[str, Any]] = owner_problems.summary(session)
    return listed


def _spent(start: Before) -> Decimal:
    cost = ModelCall.objects.filter(pk__gt=start.model_call, purpose="produce").aggregate(
        cost=Sum("cost_usd")
    )["cost"]
    return cost or Decimal(0)


# ---------------------------------------------------------------- the eval's runs


def _sessions() -> list[tuple[str, int]]:
    asked = os.environ.get("REPEATED_ASKS_SESSIONS")
    if not asked:
        return []
    return [(each["case"], each["repeat"]) for each in json.loads(asked)]


@pytest.mark.skipif(not os.environ.get("REPEATED_ASKS_OUT"), reason="run by the eval only")
@pytest.mark.parametrize(("case_id", "repeat"), _sessions())
def test_session(
    case_id: str,
    repeat: int,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    say: Callable[..., None],
    steps: HeldSteps,
    settings: SettingsWrapper,
) -> None:
    out = Path(os.environ["REPEATED_ASKS_OUT"])
    case = load_cases(Path(os.environ["REPEATED_ASKS_CASES"]))[case_id]
    real = os.environ.get("REPEATED_ASKS_REAL") == "1"
    if not real:
        pytest.skip("the eval's runs play the real producer; tests call play()")
    record = play(
        case,
        repeat=repeat,
        fake_model=fake_model,
        httpserver=httpserver,
        say=say,
        steps=steps,
        after_the_ad=lambda: use_the_real_producer(fake_model, settings),
    )
    folder = out / "sessions" / f"{case_id}--{repeat}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "record.json").write_text(json.dumps(record, indent=1, default=str))
