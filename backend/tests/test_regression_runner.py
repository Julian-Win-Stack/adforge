"""Fixed scenarios run through the real chat; only paid outside services are faked."""

import json
import os
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from django.db import connection
from pytest_httpserver import HTTPServer

from evals.regression.budget import SpendLedger
from evals.regression.database import url_for_database
from evals.regression.pages import save_page
from evals.regression.runner import run, run_scenario
from evals.regression.scenarios import Scenario, load_scenarios
from evals.regression.store import Store, read_json
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import firecrawl as page_firecrawl
from jobs.models import Job, ProducedItem, SceneStep
from jobs.work import why_the_checks_havent_passed

from .conftest import READABLE, FakeFirecrawl, facts_ok, photo, shampoo_picked
from .test_bundled_planner_questions import PHOTO_ASK, PHOTO_QUESTION
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
    marker = store.scenario_dir("child", scenario.key) / "done"
    before = marker.stat().st_mtime_ns
    spend = record["spend_usd"]
    assert run(store, [scenario], name="child", yes=True, parallel=2, pacing=False) == 0
    assert marker.stat().st_mtime_ns == before
    assert store.read_run("child")["spend_usd"] == spend
    assert len(firecrawl.requests) == requests_before
    output = capsys.readouterr().out
    assert "Estimate" in output
    assert "skipped" in output


@pytest.mark.parametrize(
    ("without_default", "saved_answer"), [(False, False), (True, False), (True, True)]
)
def test_planner_defaults_are_taken_at_once_and_missing_saved_answers_stop_visibly(
    without_default: bool,
    saved_answer: bool,
    tmp_path: Path,
    shampoo_page_url: str,
    firecrawl: FakeFirecrawl,
    fake_model: FakeModel,
) -> None:
    from agents.models import PlannerQuestion

    store = Store(tmp_path / "store")
    scenario = replace(load_scenarios()[0], link=shampoo_page_url)
    if saved_answer:
        scenario = replace(
            scenario, saved_answers={"page_conflict": "The larger capacity is correct."}
        )
    assert save_page(store, scenario)
    asked = dict(PHOTO_ASK)
    question = PHOTO_QUESTION
    if without_default:
        question += "\n2. The page names two different capacities. Which one is correct?"
        asked = {
            **asked,
            "question": question,
            "question_items": [
                *PHOTO_ASK["question_items"],
                {"kind": "page_conflict", "question": "Which capacity is correct?"},
            ],
        }
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", asked, h3_plan())
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": scenario.link, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("ask_owner", {"question": question, "photos": []})]),
        *([] if without_default and not saved_answer else [turn(calls=[("plan_ad", {})])]),
    )
    ledger = SpendLedger(store.run_dir("questions") / "spend.json", Decimal("5"), pacing=False)
    result = run_scenario(
        store,
        "questions",
        scenario,
        ledger,
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution="question-db",
    )
    waiting = PlannerQuestion.objects.get()
    if without_default and not saved_answer:
        assert result.status == "stopped"
        assert "page_conflict" in result.reason
        assert "Which capacity is correct?" in result.reason
        assert waiting.timed_out_at is None
        assert not store.scenario_done("questions", scenario.key)
    else:
        assert result.status == "done", result.reason
        if saved_answer:
            assert waiting.answered_at is not None
            assert waiting.timed_out_at is None
        else:
            assert waiting.timed_out_at is not None
            assert waiting.shown_message is not None
            assert waiting.timed_out_at < waiting.shown_message.created_at + timedelta(minutes=1)
        assert store.scenario_done("questions", scenario.key)


def test_one_missing_page_does_not_stop_other_scenarios_and_the_unfinished_one_can_retry(
    saved_scenario: tuple[Store, Scenario],
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
) -> None:
    store, scenario = saved_scenario
    missing = replace(scenario, key="missing-page")
    script_plan(fake_model, scenario)
    assert run(store, [missing, scenario], name="recover", yes=True, parallel=2, pacing=False) == 1
    record = store.read_run("recover")
    assert record["results"][scenario.key]["status"] == "done"
    assert "run save-pages first" in record["results"][missing.key]["reason"]
    assert not store.scenario_done("recover", missing.key)
    old_execution = record["results"][missing.key]["execution"]

    assert save_page(store, missing)
    assert run(store, [missing, scenario], name="recover", yes=True, parallel=2, pacing=False) == 0
    current = store.read_run("recover")
    assert current["results"][missing.key]["execution"] != old_execution
    assert store.scenario_done("recover", missing.key)
    assert len(list((store.run_dir("recover") / "attempts").glob("*.json"))) == 3
