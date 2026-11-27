"""Fixed scenarios run through the real chat; only paid outside services are faked."""

import itertools
import json
import os
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from django.db import connection
from pytest_httpserver import HTTPServer

from evals.regression.budget import SpendLedger
from evals.regression.database import url_for_database
from evals.regression.grading import build_page
from evals.regression.items import graded_items, scenario_context
from evals.regression.pages import save_page
from evals.regression.runner import run, run_scenario
from evals.regression.scenarios import Scenario, load_scenarios
from evals.regression.store import Store, read_json
from gateway import fake
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import RefusedForMoney
from jobs import firecrawl as page_firecrawl
from jobs.models import Job, ProducedItem, SceneStep
from jobs.work import why_the_checks_havent_passed

from .conftest import READABLE, FakeFirecrawl, facts_ok, photo, shampoo_picked
from .test_h3_planning import h3_plan

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def saved_scenario(
    tmp_path: Path,
    shampoo_page_url: str,
    firecrawl: FakeFirecrawl,
    fake_model: FakeModel,
    httpserver: HTTPServer,
) -> tuple[Store, Scenario]:
    store = Store(tmp_path / "store")
    scenario = replace(load_scenarios()[0], link=shampoo_page_url)
    second = httpserver.url_for("/second-product-photo.jpg")
    httpserver.expect_request("/second-product-photo.jpg").respond_with_data(
        photo(7), content_type="image/jpeg"
    )
    marks = page_firecrawl.marks_in(firecrawl.marked["data"])
    for image in marks["imgs"]:
        if image["n"] == 16:
            image.update(src=second, srcset="", dataSrc="")
    firecrawl.marked["data"]["actions"]["javascriptReturns"] = [{"value": json.dumps(marks)}]
    fake_model.answer_unscripted("pick_photos", lambda _: shampoo_picked(copies=[]))
    assert save_page(store, scenario)
    return store, scenario


def script_plan(fake_model: FakeModel, scenario: Scenario, *, owner_part: bool = False) -> None:
    fake_model.respond("check_page", READABLE)
    plan = h3_plan()
    if owner_part:
        plan["plan"]["scenes"][1]["owner_part"] = {
            "what": "the lid",
            "source": "invented",
            "looks": "A round wooden lid with a small knob on top.",
        }
    fake_model.respond("plan_ad", plan)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    # The real producer is allowed to request the normal create-person next. It must
    # never run once the real plan checks have passed at the requested terminal stage.
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": scenario.link, "target_seconds": None})]),
        turn(calls=[("plan_ad", {}), ("create_person", {})]),
    )


@pytest.mark.parametrize("owner_part", [False, True])
def test_a_saved_link_reaches_a_checked_plan_and_durable_outputs_before_any_picture(
    owner_part: bool,
    saved_scenario: tuple[Store, Scenario],
    firecrawl: FakeFirecrawl,
    fake_model: FakeModel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, scenario = saved_scenario
    script_plan(fake_model, scenario, owner_part=owner_part)
    calls_before = len(firecrawl.requests)

    def offline(*args: object, **kwargs: object) -> None:
        pytest.fail("A regression scenario must read only its saved page")

    monkeypatch.setattr(httpx, "stream", offline)
    monkeypatch.setattr(httpx, "post", offline)
    ledger = SpendLedger(store.run_dir("first") / "spend.json", Decimal("5"), pacing=False)
    result = run_scenario(
        store,
        "first",
        scenario,
        ledger,
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution="test-database",
    )
    assert result.status == "done", result.reason
    assert result.job_id is not None
    job = Job.objects.get(pk=result.job_id)
    assert why_the_checks_havent_passed(job) is None
    assert job.scenes.count() == 3
    assert not ProducedItem.objects.filter(kind=ProducedItem.Kind.PORTRAIT).exists()
    assert not SceneStep.objects.exists()
    assert not ModelCall.objects.filter(model__startswith="gpt-image").exists()
    assert job.session is not None
    assert not job.session.producer_running
    assert len(firecrawl.requests) == calls_before
    assert store.dump_path("first", scenario.key).stat().st_size > 0
    assert any(store.media_dir("first", scenario.key).rglob("*.jpg"))
    assert store.scenario_done("first", scenario.key)
    answers = store.read_answers("first", scenario.key)
    assert answers["answers"]["plan_has_demo"]["verdict"] == "pass"
    assert answers["answers"]["ad_finishes"]["verdict"] == "not_reached"
    metadata = read_json(store.scenario_dir("first", scenario.key) / "scenario.json")
    assert metadata["model_call_ids"] == ledger.call_ids("test-database")
    assert answers["cost_usd"] == float(ledger.scenario_cost(scenario.key))
    assert (store.scenario_dir("first", scenario.key) / "checkpoints/page/db.dump").is_file()
    exported = store.export_dir("first", scenario.key)
    assert (exported / "results.json").is_file()
    assert (exported / "run.json").is_file()
    assert (exported / scenario.key / "ad.json").is_file()
    items = graded_items(store, "first", scenario.key)
    assert {item.doc_id for item in items} == {f"{scenario.key}__page", f"{scenario.key}__plan"}
    assert all(file.is_file() for item in items for file in item.files)
    context = scenario_context(store, "first", scenario.key)
    assert (context["link"], context["situation"]) == (scenario.link, scenario.situation)
    plan = read_json(exported / scenario.key / "regression.json")
    assert len(plan["scenes"]) == 3
    assert plan["scenes"][1]["line"] == h3_plan()["plan"]["scenes"][1]["line"]
    assert not any("clip" in scene or "picture" in scene for scene in plan["scenes"])


def test_the_runner_uses_a_fresh_child_database_and_skips_finished_scenarios_on_resume(
    saved_scenario: tuple[Store, Scenario],
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store, scenario = saved_scenario
    script_plan(fake_model, scenario)
    requests_before = len(firecrawl.requests)
    parent_database = connection.settings_dict["NAME"]

    def offline(*args: object, **kwargs: object) -> None:
        pytest.fail("Child scenario reached the network")

    monkeypatch.setattr(httpx, "stream", offline)
    monkeypatch.setattr(httpx, "post", offline)
    assert run(store, [scenario], name="child", yes=True, parallel=2, pacing=False) == 0
    assert connection.settings_dict["NAME"] == parent_database
    assert not Job.objects.exists()  # The chat was made in the child's own database.
    record = store.read_run("child")
    assert record["scenarios"] == [scenario.key]
    assert record["to"] == "plans"
    assert record["app_commit"]
    assert record["finished_at"]
    metadata = read_json(store.scenario_dir("child", scenario.key) / "scenario.json")
    assert metadata["pid"] != os.getpid()
    assert metadata["execution"].startswith("reg_eval_")
    assert metadata["scenes"] == 3
    assert store.scenario_done("child", scenario.key)
    page = build_page(store, "child")
    assert page.is_file()
    assert f"{scenario.key}__plan" in page.read_text()
    marker = store.scenario_dir("child", scenario.key) / "done"
    before = marker.stat().st_mtime_ns
    spend = record["spend_usd"]
    assert run(store, [scenario], name="child", yes=True, parallel=2, pacing=False) == 0
    assert marker.stat().st_mtime_ns == before
    assert store.read_run("child")["spend_usd"] == spend
    assert len(firecrawl.requests) == requests_before
    output = capsys.readouterr().out
    assert "Estimate" in output
    assert "0 owner questions" in output
    assert "skipped" in output


def test_one_missing_page_refuses_the_whole_run_until_it_is_saved(
    saved_scenario: tuple[Store, Scenario],
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store, scenario = saved_scenario
    missing = replace(scenario, key="missing-page")
    capsys.readouterr()

    assert run(store, [missing, scenario], name="recover", yes=True, parallel=2, pacing=False) == 1

    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("Cannot run: missing-page: ") for line in lines)
    assert "run save-pages first" in "\n".join(lines)
    assert not store.run_dir("recover").exists()

    script_plan(fake_model, scenario)
    script_plan(fake_model, scenario)
    assert save_page(store, missing)
    assert run(store, [missing, scenario], name="recover", yes=True, parallel=2, pacing=False) == 0
    assert store.scenario_done("recover", missing.key)
    assert store.scenario_done("recover", scenario.key)


def test_an_export_failure_is_saved_explicitly_and_cannot_leave_a_done_marker(
    saved_scenario: tuple[Store, Scenario], fake_model: FakeModel
) -> None:
    store, scenario = saved_scenario
    script_plan(fake_model, scenario)
    target = store.export_dir("bad-export", scenario.key)
    target.parent.mkdir(parents=True)
    target.write_text("A file is blocking the output directory.")
    ledger = SpendLedger(store.run_dir("bad-export") / "spend.json", Decimal("5"), pacing=False)
    result = run_scenario(
        store,
        "bad-export",
        scenario,
        ledger,
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution="export-db",
    )
    assert result.status == "failed"
    assert "export" in result.reason.lower()
    assert not store.scenario_done("bad-export", scenario.key)
    saved = read_json(store.scenario_dir("bad-export", scenario.key) / "result.json")
    assert saved["reason"] == result.reason
    assert store.dump_path("bad-export", scenario.key).is_file()
    assert result.job_id is not None
    assert any(
        warning["kind"] == "regression_scenario_failed"
        for warning in Job.objects.get(pk=result.job_id).warnings
    )


def test_the_run_summary_includes_questions_from_an_unfinished_scenario(
    saved_scenario: tuple[Store, Scenario],
    fake_model: FakeModel,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store, scenario = saved_scenario
    question = "Which size should I choose?"
    fake_model.respond("check_page", READABLE)
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": scenario.link, "target_seconds": None})]),
        turn(says=question),
    )
    assert run(store, [scenario], name="count-questions", yes=True, pacing=False) == 1
    result = store.read_run("count-questions")["results"][scenario.key]
    assert result["owner_questions"] == [question]
    assert "1 owner questions" in capsys.readouterr().out


# What fal answered once its balance ran out (regression-4, 2026-11-17).
EXHAUSTED = "fal refused it for money (403): User is locked. Reason: Exhausted balance"


def script_out_of_money_while_planning(fake_model: FakeModel, scenario: Scenario) -> None:
    """The page is read, then a planning call is refused for money. A producer told so
    would take another turn: the scripted one after it, which must never be taken."""
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", h3_plan())
    fake_model.respond("fact_check", RefusedForMoney(EXHAUSTED, service="fal"))
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": scenario.link, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("plan_ad", {})]),
    )


def test_an_account_out_of_money_stops_the_scenario_at_once_and_saves_where_it_was(
    saved_scenario: tuple[Store, Scenario], fake_model: FakeModel
) -> None:
    store, scenario = saved_scenario
    script_out_of_money_while_planning(fake_model, scenario)
    ledger = SpendLedger(store.run_dir("broke") / "spend.json", Decimal("5"), pacing=False)

    result = run_scenario(
        store,
        "broke",
        scenario,
        ledger,
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution="broke-db",
    )

    assert result.status == "stopped"
    assert "Exhausted balance" in result.reason
    assert fake_model.left("produce") == 1  # Not one more paid turn after the refusal.
    assert "Exhausted balance" in ledger.stops()[scenario.key]
    assert not store.scenario_done("broke", scenario.key)
    assert store.dump_path("broke", scenario.key).is_file()


def test_an_account_out_of_money_stops_every_other_running_scenario_at_its_next_paid_call(
    saved_scenario: tuple[Store, Scenario], fake_model: FakeModel
) -> None:
    store, scenario = saved_scenario
    script_plan(fake_model, scenario)
    ledger = SpendLedger(store.run_dir("broke") / "spend.json", Decimal("5"), pacing=False)
    ledger.stop("other-product", f"RefusedForMoney: {EXHAUSTED}", at_once=True)

    result = run_scenario(
        store,
        "broke",
        scenario,
        ledger,
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution="broke-db",
    )

    assert result.status == "stopped"
    assert "other-product" in result.reason
    assert ledger.call_ids("broke-db") == []


@pytest.mark.parametrize("killed", [False, True])
def test_running_the_same_name_again_resumes_an_unfinished_scenario_where_it_stopped(
    killed: bool,
    saved_scenario: tuple[Store, Scenario],
    fake_model: FakeModel,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, scenario = saved_scenario
    script_out_of_money_while_planning(fake_model, scenario)

    assert run(store, [scenario], name="resume", yes=True, pacing=False) == 1

    output = capsys.readouterr().out
    assert f"STOP: {scenario.key}: RefusedForMoney: {EXHAUSTED}" in output
    first = read_json(store.scenario_dir("resume", scenario.key) / "result.json")
    if killed:
        # The process was killed before it could save where it ended: only the checkpoint
        # taken after the page was read is left.
        (store.scenario_dir("resume", scenario.key) / "result.json").unlink()
        store.dump_path("resume", scenario.key).unlink()
    # The account is topped up. The child is forked from this process, so it starts with
    # only what is scripted now: no page check, so a fresh start would fail.
    fake_model._scripts.clear()
    monkeypatch.setattr(fake, "_call_ids", itertools.count(1000))
    if killed:
        fake_model.respond("produce", turn(calls=[("plan_ad", {})]))
    fake_model.respond("plan_ad", h3_plan())
    fake_model.respond("fact_check", facts_ok(1, 2, 3))

    assert run(store, [scenario], name="resume", yes=True, pacing=False) == 0

    output = capsys.readouterr().out
    point = "the page checkpoint" if killed else "where its last attempt ended"
    assert f"{scenario.key}: resuming from {point}" in output
    assert store.scenario_done("resume", scenario.key)
    second = read_json(store.scenario_dir("resume", scenario.key) / "result.json")
    assert second["session_id"] == first["session_id"]
    assert second["resumed_from"] == point
    purposes = [
        call["purpose"]
        for call in read_json(store.run_dir("resume") / "spend.json")["calls"].values()
        if call["execution"] == second["execution"]
    ]
    assert "check_page" not in purposes
    assert "fact_check" in purposes


PLANS = [
    {
        "style": "person_talking",
        "seconds": seconds,
        "title": title,
        "built_on": f"{title} is what sets it apart.",
        "opens": f"Opens on {title}.",
        "doubt": "Does it work?",
    }
    for title, seconds in (("Softer hair", 33), ("One wash", 35), ("No sulphates", 34))
]
CARD = {
    "opening": "Here are 3 plans.",
    "product_option": None,
    "option_named": "",
    "plans": PLANS,
    "plans_pick": 1,
}


def script_card_then_plan(fake_model: FakeModel, scenario: Scenario) -> None:
    """The producer reads the page and shows the plan card, then plans once it is answered."""
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", h3_plan())
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": scenario.link, "target_seconds": None})]),
        turn(calls=[("show_plan_card", {**CARD, "other": None})]),
        turn(calls=[("plan_ad", {}), ("create_person", {})]),
    )


def _run(store: Store, scenario: Scenario, **more: object) -> object:
    ledger = SpendLedger(store.run_dir("first") / "spend.json", Decimal("5"), pacing=False)
    return run_scenario(
        store,
        "first",
        scenario,
        ledger,
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution="test-database",
        **more,  # type: ignore[arg-type]
    )


@pytest.mark.plan_card
@pytest.mark.parametrize(("pick", "seconds"), [(None, 33), (2, 35)], ids=["picks", "second"])
def test_a_run_answers_the_plan_card_itself_and_reaches_the_plan(
    pick: int | None,
    seconds: int,
    saved_scenario: tuple[Store, Scenario],
    fake_model: FakeModel,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store, scenario = saved_scenario
    script_card_then_plan(fake_model, scenario)

    result = _run(store, scenario, plan=pick)

    assert result.status == "done", result.reason  # type: ignore[attr-defined]
    job = Job.objects.get()
    assert job.target_seconds == seconds
    assert job.scenes.count() == 3
    said = capsys.readouterr().out
    assert "plan card: 1. Softer hair, 2. One wash, 3. No sulphates" in said
    assert ("answered: the agent's picks" if pick is None else "answered: plan 2") in said
    # Regression runs pin the person-talking style in the first message.
    first = job.session.messages.order_by("seq").first()  # type: ignore[union-attr]
    assert first is not None and first.text.endswith("Make it a person talking ad.")


@pytest.mark.plan_card
def test_a_run_whose_producer_never_shows_a_plan_card_stops_with_a_notice(
    saved_scenario: tuple[Store, Scenario], fake_model: FakeModel
) -> None:
    store, scenario = saved_scenario
    fake_model.respond("check_page", READABLE)
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": scenario.link, "target_seconds": None})]),
        turn(says="I read the page."),
    )

    result = _run(store, scenario)

    assert result.status == "stopped"  # type: ignore[attr-defined]
    assert "no plan card" in result.reason  # type: ignore[attr-defined]
    kinds = [warning["kind"] for warning in Job.objects.get().warnings]
    assert "no_plan_card" in kinds
    assert "no plan card" in (store.run_dir("first") / "spend.json").read_text()
