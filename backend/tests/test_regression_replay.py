"""Later-stage regression runs reuse real saved prefixes, never paid stand-ins."""

from decimal import Decimal

import httpx
import pytest
from django.db import connection

from evals.regression.budget import SpendLedger
from evals.regression.database import url_for_database
from evals.regression.runner import run, run_scenario
from evals.regression.scenarios import Scenario
from evals.regression.store import Store, read_json
from gateway.fake import FakeModel, turn
from gateway.types import ToolUse, Turn, TurnRequest

from .conftest import READABLE, facts_ok
from .test_clip_judge import product
from .test_h3_planning import h3_plan
from .test_regression_full_runner import script_render
from .test_regression_runner import saved_scenario  # noqa: F401
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM

pytestmark = pytest.mark.django_db(transaction=True)


def test_from_plan_restores_the_page_but_takes_a_new_plan_and_only_new_costs(
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, scenario = saved_scenario
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", h3_plan())
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    # The page checkpoint also contains these unstarted same-turn future calls.
    fake_model.respond(
        "produce",
        turn(
            calls=[
                ("read_page", {"link": scenario.link, "target_seconds": None}),
                ("plan_ad", {}),
                ("create_person", {}),
            ]
        ),
    )
    baseline = run_scenario(
        store,
        "baseline",
        scenario,
        SpendLedger(store.run_dir("baseline") / "spend.json", Decimal("25"), pacing=False),
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution="baseline",
    )
    assert baseline.status == "done", baseline.reason
    store.write_baselines({scenario.key: "baseline"})

    def offline(*args: object, **kwargs: object) -> None:
        pytest.fail("A later-stage run cannot read the live page")

    monkeypatch.setattr(httpx, "stream", offline)
    monkeypatch.setattr(httpx, "post", offline)
    changed = h3_plan()
    changed["plan"]["scenes"][0]["line"] = changed["plan"]["scenes"][0]["line"].replace(
        "Meet", "Try"
    )
    fake_model.respond("plan_ad", changed)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    fake_model.respond("produce", turn(calls=[("plan_ad", {}), ("create_person", {})]))

    assert (
        run(
            store,
            [scenario],
            name="new-plan",
            yes=True,
            parallel=1,
            pacing=False,
            to="plans",
            from_stage="plan",
        )
        == 0
    )

    record = store.read_run("new-plan")
    assert record["from"] == "plan"
    assert record["results"][scenario.key]["status"] == "done"
    exported = read_json(
        store.export_dir("new-plan", scenario.key) / scenario.key / "regression.json"
    )
    assert exported["scenes"][0]["line"].startswith("Try the Stoneware Mug")
    calls = read_json(store.run_dir("new-plan") / "spend.json")["calls"].values()
    assert all(
        call["purpose"] not in {"check_page", "copy_page_text", "pick_photos", "label_photo"}
        for call in calls
    )
    metadata = read_json(store.scenario_dir("new-plan", scenario.key) / "scenario.json")
    assert metadata["replayed_from"] == {"run": "baseline", "stage": "plan"}
    assert store.read_answers("new-plan", scenario.key)["cost_usd"] == pytest.approx(
        sum(float(call["cost_usd"]) for call in calls)
    )


def repair_turn(request: TurnRequest) -> Turn:
    calls = [event for event in request.handoff.conversation if isinstance(event, ToolUse)]
    last_draw = max(
        (number for number, call in enumerate(calls) if call.tool == "draw_starting_picture"),
        default=-1,
    )
    last_film = max(
        (number for number, call in enumerate(calls) if call.tool == "film"), default=-1
    )
    return turn(
        calls=[("draw_starting_picture", PICTURE_FORM)]
        if last_draw < 0 or last_film > last_draw
        else [("film", FILM)]
    )


@pytest.mark.parametrize("new_failure", [False, True])
def test_from_clips_films_the_kept_redraw_and_only_redraws_after_a_new_failed_check(
    new_failure: bool,
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jobs.models import Job, ProducedItem

    store, scenario = saved_scenario
    script_render(fake_model, scenario, full=True)
    fake_model.answer_unscripted("scene_agent", repair_turn)
    fake_model.respond("check_clip_product", product("fail", "The rim changes shape."))
    baseline = run_scenario(
        store,
        "baseline",
        scenario,
        SpendLedger(store.run_dir("baseline") / "spend.json", Decimal("30"), pacing=False),
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution="baseline",
        to="full",
    )
    assert baseline.status == "done", baseline.reason
    assert baseline.job_id is not None
    job = Job.objects.get(pk=baseline.job_id)
    kept = read_json(store.scenario_dir("baseline", scenario.key) / "kept-pictures.json")
    assert any(
        selected["picture"]
        != job.scenes.get(pk=selected["scene_id"])
        .produced.filter(kind=ProducedItem.Kind.STARTING_PICTURE)
        .earliest("created_at", "pk")
        .pk
        for selected in kept
    )
    store.write_baselines({scenario.key: "baseline"})

    def offline(*args: object, **kwargs: object) -> None:
        pytest.fail("A clip replay cannot read the live page")

    monkeypatch.setattr(httpx, "stream", offline)
    monkeypatch.setattr(httpx, "post", offline)
    if new_failure:
        fake_model.respond("check_clip_product", product("fail", "The handle floats."))
    fake_model.respond("produce", turn(calls=[("assemble_ad", {})]), turn(says="New ad."))
    assert (
        run(
            store,
            [scenario],
            name="new-clips",
            yes=True,
            parallel=1,
            pacing=False,
            to="full",
            from_stage="clips",
        )
        == 0
    )
    calls = list(read_json(store.run_dir("new-clips") / "spend.json")["calls"].values())
    assert sum(call["purpose"] == "make_broll_clip" for call in calls) == 3 + int(new_failure)
    assert sum(call["purpose"] == "draw_scene_picture" for call in calls) == int(new_failure)
    assert not any(
        call["purpose"]
        in {"plan_ad", "draw_person", "make_person_in_room", "make_room_alone", "make_music"}
        for call in calls
    )
    new_kept = read_json(store.scenario_dir("new-clips", scenario.key) / "kept-pictures.json")
    same = sum(
        selected["picture"]
        == next(row["picture"] for row in kept if row["scene"] == selected["scene"])
        for selected in new_kept
    )
    assert same == 3 - int(new_failure)
    assert (
        store.read_answers("new-clips", scenario.key)["answers"]["ad_finishes"]["verdict"] == "pass"
    )
