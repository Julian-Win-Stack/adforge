"""The run's paid work stops at its agreed spending limit."""

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel

from evals.regression.budget import PaidCalls, SpendLedger, SpendLimitReached, below_limit
from gateway.call_observer import use_call_observer
from gateway.fake import FakeModel
from gateway.gateway import call_model
from gateway.models import ModelCall
from gateway.types import Handoff


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
def test_a_recorded_paid_attempt_stops_the_next_payment_but_the_default_gateway_still_runs(
    tmp_path: Path, fake_model: FakeModel
) -> None:
    fake_model.respond("copy_page_text", {"text": "kept"}, {"text": "default"})
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
        assert read().text == "kept"
        with pytest.raises(SpendLimitReached):
            read()
    call = ModelCall.objects.get()
    assert ledger.spend_usd == call.cost_usd
    assert ledger.call_ids("one-database") == [call.pk]
    assert read().text == "default"
    assert ModelCall.objects.count() == 2
    assert ledger.spend_usd == call.cost_usd
