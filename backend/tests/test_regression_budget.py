"""The run's paid work stops at its agreed spending limit."""

import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel

from evals.regression.budget import PaidCalls, SpendLedger, SpendLimitReached, below_limit
from evals.regression.runner import estimate, run
from evals.regression.scenarios import load_scenarios
from evals.regression.store import Store
from gateway.call_observer import use_call_observer
from gateway.fake import FakeModel
from gateway.gateway import call_model
from gateway.models import ModelCall
from gateway.types import Handoff, UnusableReply


def test_new_paid_work_stops_as_soon_as_the_run_reaches_one_and_a_half_estimates() -> None:
    assert below_limit(Decimal("14.99"), Decimal("10"))
    assert not below_limit(Decimal("15"), Decimal("10"))
    assert not below_limit(Decimal("15.01"), Decimal("10"))


def test_real_attempts_are_counted_once_and_a_restarted_database_cannot_reuse_old_ids(
    tmp_path: Path,
) -> None:
    path = tmp_path / "spend.json"
    ledger = SpendLedger(path, Decimal("10"), pacing=False)
    ledger.record("mug", "database-one", 1, Decimal("7.50"))
    ledger.record("mug", "database-one", 1, Decimal("7.50"))
    assert ledger.spend_usd == Decimal("7.50")
    assert ledger.can_start()

    resumed = SpendLedger(path, Decimal("10"), pacing=False)
    resumed.record("mug", "database-two", 1, Decimal("7.50"))
    assert resumed.spend_usd == Decimal("15.00")
    assert resumed.scenario_cost("mug") == Decimal("15.00")
    assert resumed.call_ids("database-two") == [1]
    assert not resumed.can_start()


class ToRead(Handoff):
    text: str


class ReadBack(BaseModel):
    text: str


@pytest.mark.django_db
@pytest.mark.parametrize("billed_failure", [False, True])
def test_a_recorded_paid_attempt_stops_the_next_payment_but_the_default_gateway_still_runs(
    billed_failure: bool, tmp_path: Path, fake_model: FakeModel
) -> None:
    fake_model.respond(
        "copy_page_text",
        UnusableReply("Billed but incomplete", input_tokens=100, output_tokens=20)
        if billed_failure
        else {"text": "kept"},
        {"text": "default"},
    )
    ledger = SpendLedger(tmp_path / "spend.json", Decimal("0.000000000001"), pacing=False)

    def read() -> ReadBack:
        return call_model(
            job=None,
            purpose="copy_page_text",
            instructions="Copy the page text.",
            handoff=ToRead(text="kept"),
            output=ReadBack,
        )

    with use_call_observer(PaidCalls(ledger, "mug", "one-database")):
        if billed_failure:
            with pytest.raises(UnusableReply):
                read()
        else:
            assert read().text == "kept"
        with pytest.raises(SpendLimitReached):
            read()
    call = ModelCall.objects.get()
    assert ledger.spend_usd == call.cost_usd
    assert ledger.call_ids("one-database") == [call.pk]
    assert read().text == "default"
    assert ModelCall.objects.count() == 2
    assert ledger.spend_usd == call.cost_usd


def test_estimate_uses_the_latest_matching_real_cost_not_the_run_name(tmp_path: Path) -> None:
    store = Store(tmp_path)
    scenario = load_scenarios()[0]
    for name, started, cost in [
        ("z-old", "2026-11-01T00:00:00+00:00", 9),
        ("a-new", "2026-11-02T00:00:00+00:00", 2),
    ]:
        store.write_run(name, {"name": name, "started_at": started, "to": "plans", "from": None})
        store.write_answers(name, scenario.key, {"cost_usd": cost})
        store.dump_path(name, scenario.key).write_bytes(b"dump")
        store.mark_done(name, scenario.key)
    assert estimate(store, [scenario]) == Decimal("2")


def test_processes_reserve_shared_rate_slots_per_model(tmp_path: Path) -> None:
    ledger = SpendLedger(tmp_path / "spend.json", Decimal("10"))
    assert ledger.reserve_slot("pictures", now=100, gap=3.1) == 100
    another_process = SpendLedger(tmp_path / "spend.json", Decimal("10"))
    assert another_process.reserve_slot("pictures", now=100, gap=3.1) == 103.1
    assert another_process.reserve_slot("text", now=100, gap=0.4) == 100
    assert ledger.reserve_slot("pictures", now=110, gap=3.1) == 110


def test_declining_the_printed_estimate_starts_no_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = Store(tmp_path)

    def decline(prompt: str) -> str:
        assert "Estimate:" in capsys.readouterr().out
        return "no"

    monkeypatch.setattr("builtins.input", decline)
    assert run(store, [load_scenarios()[0]], name="declined") == 1
    assert not store.run_dir("declined").exists()


def test_a_resumed_exhausted_budget_starts_no_more_scenarios(tmp_path: Path) -> None:
    store = Store(tmp_path)
    scenarios = load_scenarios()[:2]
    store.write_run(
        "capped",
        {
            "name": "capped",
            "to": "plans",
            "from": None,
            "app_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "estimate_usd": 10,
            "spend_usd": 15,
            "scenarios": [s.key for s in scenarios],
            "started_at": "2026-11-01T00:00:00+00:00",
            "finished_at": None,
        },
    )
    ledger = SpendLedger(store.run_dir("capped") / "spend.json", Decimal("10"), pacing=False)
    ledger.record(scenarios[0].key, "previous-database", 1, Decimal("15"))
    assert run(store, scenarios, name="capped", yes=True, pacing=False) == 1
    assert "no new scenario" in store.read_run("capped")["stopped_why"]
    assert not list((store.run_dir("capped") / "attempts").glob("*.json"))
