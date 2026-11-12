"""Fixed scenarios run through the real chat; only paid outside services are faked."""

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from django.db import connection
from pytest_httpserver import HTTPServer

from evals.regression.budget import SpendLedger
from evals.regression.database import url_for_database
from evals.regression.pages import save_page
from evals.regression.runner import run_scenario
from evals.regression.scenarios import load_scenarios
from evals.regression.store import Store, read_json
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import firecrawl as page_firecrawl
from jobs.models import Job, ProducedItem, SceneStep
from jobs.work import why_the_checks_havent_passed

from .conftest import READABLE, FakeFirecrawl, facts_ok, photo, shampoo_picked
from .test_h3_planning import h3_plan

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_saved_link_reaches_a_checked_plan_and_durable_outputs_before_any_picture(
    tmp_path: Path,
    shampoo_page_url: str,
    firecrawl: FakeFirecrawl,
    fake_model: FakeModel,
    monkeypatch: pytest.MonkeyPatch,
    httpserver: HTTPServer,
) -> None:
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
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", h3_plan())
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    # The real producer is allowed to request the normal create-person next. It must
    # never run once the real plan checks have passed at the requested terminal stage.
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": scenario.link, "target_seconds": None})]),
        turn(calls=[("plan_ad", {}), ("create_person", {})]),
    )
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
    job = Job.objects.get(pk=result.job_id)
    assert why_the_checks_havent_passed(job) is None
    assert job.scenes.count() == 3
    assert not ProducedItem.objects.filter(kind=ProducedItem.Kind.PORTRAIT).exists()
    assert not SceneStep.objects.exists()
    assert not ModelCall.objects.filter(model__startswith="gpt-image").exists()
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
