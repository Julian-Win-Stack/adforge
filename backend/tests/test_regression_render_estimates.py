"""Catalogue estimates stay proportional to the selected terminal stage."""

from decimal import Decimal
from pathlib import Path

from evals.regression import runner
from evals.regression.scenarios import load_scenarios
from evals.regression.store import Store


def test_fixed_set_estimates_grow_with_each_stage_and_count_every_scenario_video(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    scenarios = load_scenarios()
    plans = runner.estimate(store, scenarios, to="plans")
    pictures = runner.estimate(store, scenarios, to="pictures")
    full = runner.estimate(store, scenarios, to="full")
    assert Decimal("5") < plans < Decimal("20")
    assert Decimal("20") < pictures < Decimal("40")
    assert Decimal("120") < full < Decimal("190")
    assert plans < pictures < full
    assert runner.estimated_video_seconds(store, scenarios, to="full") == 825
    assert runner.estimated_video_seconds(store, scenarios, to="pictures") == 0


def test_recent_real_cost_and_video_seconds_replace_same_stage_estimates(tmp_path: Path) -> None:
    store = Store(tmp_path)
    scenario = load_scenarios()[0]
    store.write_run(
        "old",
        {
            "name": "old",
            "to": "full",
            "from": None,
            "started_at": "2026-11-12T00:00:00Z",
            "scenarios": [scenario.key],
            "results": {scenario.key: {"video_seconds": 41}},
        },
    )
    store.write_answers("old", scenario.key, {"cost_usd": 3.25})
    store.dump_path("old", scenario.key).write_bytes(b"dump")
    store.mark_done("old", scenario.key)
    assert runner.estimate(store, [scenario], to="full") == Decimal("3.25")
    assert runner.estimated_video_seconds(store, [scenario], to="full") == 41
    assert runner.estimated_video_seconds(store, [scenario], to="pictures") == 0
