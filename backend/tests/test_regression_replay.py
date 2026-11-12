"""Later-stage regression runs reuse real saved prefixes, never paid stand-ins."""

from decimal import Decimal
from pathlib import Path
from typing import Literal

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
    # Later-stage runs need the baseline outputs, even after the raw page snapshot is archived.
    import shutil

    shutil.rmtree(store.page_dir(scenario.key))

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


def test_from_pictures_replaces_scene_work_and_keeps_shared_plan_outputs(
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
) -> None:
    from jobs.models import Job

    store, scenario = saved_scenario
    script_render(fake_model, scenario, full=True, late_music=True)
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
    shared = set(
        job.produced.filter(scene__isnull=True)
        .exclude(kind="finished_ad")
        .values_list("file", flat=True)
    )
    prefix = read_json(
        store.scenario_dir("baseline", scenario.key) / "checkpoints/plan/prefix-state.json"
    )
    assert job.produced.get(kind="music").pk not in prefix["produced_ids"]
    # Exercise the actual lazy cut-out cache created by scene picture input preparation.
    from agents.models import ToolCall
    from evals.regression.database import dump_database
    from evals.regression.runner import _save_media
    from gateway.gateway import charged_to
    from jobs import library, product_only

    photo = job.library.first()
    assert photo is not None
    photo.alone = "no"
    photo.save(update_fields=["alone"])
    assert baseline.session_id is not None
    draw = ToolCall.objects.filter(
        session_id=baseline.session_id, tool="draw_starting_picture"
    ).first()
    assert draw is not None
    with charged_to(draw):
        product_only.for_clip(job, library.entry(photo))
    assert job.library.count() > len(prefix["library"])
    dump_database(
        url_for_database(connection.settings_dict["NAME"]),
        store.dump_path("baseline", scenario.key),
    )
    _save_media(store.media_dir("baseline", scenario.key))
    store.write_baselines({scenario.key: "baseline"})
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "start_scene_agent",
                    {
                        "scene": number,
                        "instruction": "Film the planned scene.",
                        "quotes": [scenario.first_message],
                    },
                )
                for number in (1, 2, 3)
            ]
        ),
        turn(says="Making scenes."),
        turn(calls=[("assemble_ad", {})]),
        turn(says="Done."),
    )
    assert (
        run(
            store,
            [scenario],
            name="new-pictures",
            yes=True,
            parallel=1,
            pacing=False,
            to="full",
            from_stage="pictures",
        )
        == 0
    )
    calls = list(read_json(store.run_dir("new-pictures") / "spend.json")["calls"].values())
    assert sum(row["purpose"] == "draw_scene_picture" for row in calls) == 3
    assert sum(row["purpose"] == "make_broll_clip" for row in calls) == 3
    assert not any(
        row["purpose"]
        in {
            "plan_ad",
            "draw_person",
            "make_person_in_room",
            "make_room_alone",
            "make_music",
            "check_page",
        }
        for row in calls
    )
    records = read_json(
        store.export_dir("new-pictures", scenario.key) / scenario.key / "grading-records.json"
    )
    assert shared <= {row["file"] for row in records["shared"]}

    assert [
        {key: value for key, value in row.items() if key != "created_at"}
        for row in records["library"]
    ] == [
        {key: value for key, value in row.items() if key != "created_at"}
        for row in prefix["library"]
    ]


@pytest.mark.parametrize("note_count", [0, 1, 2])
def test_from_notes_only_changes_one_copy_and_counts_new_work(
    note_count: int,
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
) -> None:
    from dataclasses import replace

    from chat.models import Session

    from .test_session_copy import snapshot

    store, scenario = saved_scenario
    script_render(fake_model, scenario, full=True)
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
    assert baseline.session_id is not None
    original = snapshot(Session.objects.get(pk=baseline.session_id))
    store.write_baselines({scenario.key: "baseline"})
    notes = ["Make scene 2 warmer.", "Make scene 3 calmer."][:note_count]
    scenario = replace(scenario, owner_notes=notes)
    for number, text in enumerate(notes, 2):
        fake_model.respond(
            "produce",
            turn(
                calls=[
                    (
                        "pass_note",
                        {"scenes": [{"scene": number, "instruction": text, "quotes": [text]}]},
                    )
                ]
            ),
            turn(says="Working."),
            turn(calls=[("assemble_ad", {})]),
            turn(says="Done."),
        )
    # Run directly in this disposable restored baseline DB to compare every original row.
    from evals.regression.replay import source_for

    result = run_scenario(
        store,
        "new-notes",
        scenario,
        SpendLedger(store.run_dir("new-notes") / "spend.json", Decimal("30"), pacing=False),
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution="new-notes",
        to="full",
        replay=source_for(store, scenario, "notes"),
    )
    assert result.status == "done", result.reason
    assert snapshot(Session.objects.get(pk=baseline.session_id)) == original
    assert len(result.notes) == note_count
    calls = list(read_json(store.run_dir("new-notes") / "spend.json").get("calls", {}).values())
    assert sum(row["purpose"] == "make_broll_clip" for row in calls) == note_count
    assert not any(
        row["purpose"] in {"plan_ad", "check_page", "make_music", "draw_person"} for row in calls
    )
    answer = store.read_answers("new-notes", scenario.key)["answers"]["note_untouched_same"]
    assert answer["verdict"] == ("pass" if note_count else "not_reached")
    assert result.cost_usd == pytest.approx(sum(float(row["cost_usd"]) for row in calls))
    if not note_count:
        assert (
            run(
                store,
                [scenario],
                name="empty-notes",
                yes=True,
                parallel=1,
                pacing=False,
                to="full",
                from_stage="notes",
            )
            == 0
        )
        assert store.read_run("empty-notes")["estimate_usd"] == 0


def test_missing_baseline_stops_before_any_paid_work(
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
) -> None:
    store, scenario = saved_scenario
    assert (
        run(
            store,
            [scenario],
            name="missing",
            yes=True,
            parallel=1,
            pacing=False,
            to="full",
            from_stage="clips",
        )
        == 1
    )
    result = store.read_run("missing")["results"][scenario.key]
    assert result["status"] == "stopped"
    assert result["reason"] == "no baseline; run it from the start first"
    assert result["cost_usd"] == 0
    assert not read_json(store.run_dir("missing") / "spend.json").get("calls")


def test_suffix_estimates_and_empty_notes_are_free(saved_scenario: tuple[Store, Scenario]) -> None:  # noqa: F811
    from dataclasses import replace

    from evals.regression.estimates import estimate, estimated_video_seconds

    store, scenario = saved_scenario
    estimates = [
        estimate(store, [scenario], to="full", from_stage=stage)
        for stage in (None, "plan", "pictures", "clips", "notes")
    ]
    assert all(before > after for before, after in zip(estimates, estimates[1:], strict=False))
    assert estimates[-1] == 0
    notes = replace(scenario, owner_notes=["Make scene 2 warmer."])
    assert 0 < estimate(store, [notes], to="full", from_stage="notes") < estimates[-2]
    assert estimated_video_seconds(store, [scenario], to="full", from_stage="notes") == 0
    assert estimated_video_seconds(store, [notes], to="full", from_stage="notes") == 12
    invalid: list[tuple[Literal["pictures", "clips", "notes"], Literal["plans", "pictures"]]] = [
        ("pictures", "plans"),
        ("clips", "pictures"),
        ("notes", "pictures"),
    ]
    for stage, to in invalid:
        with pytest.raises(ValueError, match="--from"):
            run(store, [scenario], name="invalid", yes=True, to=to, from_stage=stage)


@pytest.mark.parametrize("full_baseline", [False, True], ids=["picture-baseline", "ad-baseline"])
def test_clip_replay_preserves_edited_ancestors_end_pair_and_exact_scene_window(
    full_baseline: bool,
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
) -> None:
    from evals.regression.database import dump_database
    from jobs.models import Job

    from .test_scene_end_pictures import END_PICTURE

    store, scenario = saved_scenario
    # Full scripts supply the music/assemble continuation needed after picture replay.
    script_render(fake_model, scenario, full=True)

    def chain(request: TurnRequest) -> Turn:
        calls = {event.tool for event in request.handoff.conversation if isinstance(event, ToolUse)}
        if "draw_starting_picture" not in calls:
            return turn(calls=[("draw_starting_picture", PICTURE_FORM)])
        if "edit_starting_picture" not in calls:
            return turn(
                calls=[
                    (
                        "edit_starting_picture",
                        {
                            "keep": ["the room", "the mug"],
                            "change": "Move the hand slightly left",
                            "anything_else": "not needed here",
                            "picks": PICTURE_FORM["picks"],
                        },
                    )
                ]
            )
        if "make_end_picture" not in calls:
            return turn(calls=[("make_end_picture", END_PICTURE)])
        return turn(calls=[("film", FILM)])

    fake_model.answer_unscripted("scene_agent", chain)
    baseline = run_scenario(
        store,
        "baseline",
        scenario,
        SpendLedger(store.run_dir("baseline") / "spend.json", Decimal("30"), pacing=False),
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution="baseline",
        to="full" if full_baseline else "pictures",
    )
    assert baseline.status == "done", baseline.reason
    assert baseline.job_id is not None
    job = Job.objects.get(pk=baseline.job_id)
    kept = read_json(store.scenario_dir("baseline", scenario.key) / "kept-pictures.json")
    assert all(row["end_picture"] for row in kept)
    assert all((job.produced.get(pk=row["picture"]).sent or {}).get("edited_from") for row in kept)
    from datetime import datetime, timedelta

    from evals.regression.store import write_json
    from jobs.models import SceneAgentRun

    # A saved later window must survive the worker's unconditional ensure-window-1.
    for row in kept:
        SceneAgentRun.objects.filter(pk=row["window"]["id"]).update(number=2)
        row["window"]["number"] = 2
        first = SceneAgentRun.objects.create(
            agent_fix_id=row["fixing"],
            number=1,
            instruction="Earlier completed work",
            finished_at=datetime.fromisoformat(row["at"]),
            clips=6,
            starting_pictures=6,
        )
        SceneAgentRun.objects.filter(pk=first.pk).update(
            created_at=datetime.fromisoformat(row["window"]["created_at"]) - timedelta(seconds=1)
        )
        SceneAgentRun.objects.create(
            agent_fix_id=row["fixing"],
            number=3,
            instruction="Later unused window",
            clips=6,
            starting_pictures=6,
        )
    write_json(store.scenario_dir("baseline", scenario.key) / "kept-pictures.json", kept)
    for scene in job.scenes.all():
        # A later unused output and changed scene must not displace the actual ad inputs.
        old = scene.produced.filter(kind="starting_picture").latest("created_at", "pk")
        old.pk = None
        old.version += 1
        old.save()
        scene.line = "A later unused scene edit."
        scene.save(update_fields=["line"])
    dump_database(
        url_for_database(connection.settings_dict["NAME"]),
        store.dump_path("baseline", scenario.key),
    )
    store.write_baselines({scenario.key: "baseline"})
    if full_baseline:
        fake_model.respond("produce", turn(calls=[("assemble_ad", {})]), turn(says="Done."))
    assert (
        run(
            store,
            [scenario],
            name="chains",
            yes=True,
            parallel=1,
            pacing=False,
            to="full",
            from_stage="clips",
        )
        == 0
    )
    calls = list(read_json(store.run_dir("chains") / "spend.json")["calls"].values())
    assert not any(
        row["purpose"] in {"draw_scene_picture", "edit_scene_picture", "make_end_picture"}
        for row in calls
    )
    assert sum(row["purpose"] == "make_broll_clip" for row in calls) == 3
    new = read_json(store.scenario_dir("chains", scenario.key) / "kept-pictures.json")
    for before, after in zip(
        sorted(kept, key=lambda row: row["scene"]),
        sorted(new, key=lambda row: row["scene"]),
        strict=True,
    ):
        assert before["picture"] == after["picture"]
        assert before["end_picture"] == after["end_picture"]
        assert before["scene_row"]["line"] == after["scene_row"]["line"]
        assert before["window"]["starting_pictures"] == after["window"]["starting_pictures"]
        assert before["window"]["end_pictures"] == after["window"]["end_pictures"]
        assert after["window"]["number"] == 2


def test_missing_saved_prefix_or_media_stops_before_payment(
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
) -> None:

    from evals.regression.store import write_json

    store, scenario = saved_scenario
    script_render(fake_model, scenario, full=True)
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
    store.write_baselines({scenario.key: "baseline"})
    folder = store.scenario_dir("baseline", scenario.key)
    kept = read_json(folder / "kept-pictures.json")
    from jobs.models import ProducedItem

    picture = ProducedItem.objects.get(pk=kept[0]["picture"])
    missing: list[tuple[Literal["plan", "pictures", "clips", "notes"], Path, str]] = [
        ("plan", folder / "checkpoints/page/done", "page checkpoint"),
        ("pictures", folder / "checkpoints/plan/prefix-state.json", "plan prefix"),
        ("clips", folder / "kept-pictures.json", "kept-picture manifest"),
        ("clips", folder / "media" / picture.file, "missing saved media"),
    ]
    for number, (stage, path, reason) in enumerate(missing):
        saved = path.read_bytes()
        path.unlink()
        try:
            name = f"missing-{number}"
            assert (
                run(
                    store,
                    [scenario],
                    name=name,
                    yes=True,
                    parallel=1,
                    pacing=False,
                    to="full",
                    from_stage=stage,
                )
                == 1
            )
            result = store.read_run(name)["results"][scenario.key]
            assert result["status"] == "stopped"
            assert reason in result["reason"]
            assert result["cost_usd"] == 0
            assert not read_json(store.run_dir(name) / "spend.json").get("calls")
        finally:
            path.write_bytes(saved)
    write_json(
        folder / "kept-pictures.json",
        [{key: value for key, value in row.items() if key != "ad"} for row in kept],
    )
    assert (
        run(
            store,
            [scenario],
            name="missing-ad",
            yes=True,
            parallel=1,
            pacing=False,
            to="full",
            from_stage="notes",
        )
        == 1
    )
    assert "no finished ad" in store.read_run("missing-ad")["results"][scenario.key]["reason"]


def test_notes_cli_defaults_to_full_scenarios_and_rejects_explicit_picture_keys(
    tmp_path: Path,
) -> None:
    from evals.regression.__main__ import main
    from evals.regression.scenarios import load_scenarios

    store = Store(tmp_path)
    assert (
        main(
            [
                "--store",
                str(tmp_path),
                "run",
                "--to",
                "full",
                "--from",
                "notes",
                "--name",
                "default-notes",
                "--yes",
            ]
        )
        == 1
    )
    scenarios = load_scenarios()
    record = store.read_run("default-notes")
    assert set(record["scenarios"]) == {row.key for row in scenarios if row.kind == "full"}
    assert all(
        row["reason"] == "no baseline; run it from the start first"
        for row in record["results"].values()
    )
    picture = next(row.key for row in scenarios if row.kind == "picture")
    assert (
        main(
            [
                "--store",
                str(tmp_path),
                "run",
                "--to",
                "full",
                "--from",
                "notes",
                "--name",
                "explicit-picture",
                "--only",
                picture,
                "--yes",
            ]
        )
        == 1
    )
    assert not store.read_run("explicit-picture")


def test_picture_prefix_without_music_cannot_silently_buy_it_for_an_ad_suffix(
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
) -> None:
    store, scenario = saved_scenario
    script_render(fake_model, scenario)
    baseline = run_scenario(
        store,
        "pictures",
        scenario,
        SpendLedger(store.run_dir("pictures") / "spend.json", Decimal("30"), pacing=False),
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution="pictures",
        to="pictures",
    )
    assert baseline.status == "done", baseline.reason
    store.write_baselines({scenario.key: "pictures"})
    assert (
        run(
            store,
            [scenario],
            name="missing-music",
            yes=True,
            parallel=1,
            pacing=False,
            to="full",
            from_stage="clips",
        )
        == 1
    )
    result = store.read_run("missing-music")["results"][scenario.key]
    assert result["status"] == "stopped"
    assert "no saved music" in result["reason"]
    assert result["cost_usd"] == 0
    assert not read_json(store.run_dir("missing-music") / "spend.json").get("calls")
