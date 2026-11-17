"""Owner-note runs: each case's saved finished ad, copied and fixed from one note."""

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest
from django.db import connection

from evals.regression.budget import SpendLedger
from evals.regression.database import url_for_database
from evals.regression.note_cases import NoteCase, load_note_cases
from evals.regression.notes import run_note_case, run_notes, source_of
from evals.regression.runner import _app_commit, run_scenario
from evals.regression.scenarios import Scenario
from evals.regression.store import Store, read_json, write_json
from gateway.fake import FakeModel, turn
from gateway.types import RefusedForMoney

from .test_regression_full_runner import script_render
from .test_regression_runner import saved_scenario  # noqa: F401

SOURCE = "baseline-2"


def finished_source(store: Store, scenario: Scenario, fake_model: FakeModel) -> None:
    """The source run: a full ad saved in the store, as baseline-2's scenarios are."""
    script_render(fake_model, scenario, full=True)
    made = run_scenario(
        store,
        SOURCE,
        scenario,
        SpendLedger(store.run_dir(SOURCE) / "spend.json", Decimal("30"), pacing=False),
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution=SOURCE,
        to="full",
    )
    assert made.status == "done", made.reason


def a_case(scenario: Scenario, scenes: tuple[int, ...] = (2,), **changes: str) -> NoteCase:
    fields = {
        "id": "shampoo-s2",
        "scenario": scenario.key,
        "source_run": SOURCE,
        "note": "In the second part the bottle looks wrong.",
        "grade": "scene 2 bottle is wrong",
        "graded_from": "test grades",
        **changes,
    }
    return NoteCase(scenes=scenes, **fields)


def fixes(fake_model: FakeModel, scene: int, note: str) -> None:
    fake_model.respond(
        "produce",
        turn(
            calls=[
                ("pass_note", {"scenes": [{"scene": scene, "instruction": note, "quotes": [note]}]})
            ]
        ),
        turn(says="Working on it."),
        turn(calls=[("assemble_ad", {})]),
        turn(says="Your new ad is ready."),
    )


def fingerprint(folder: Path) -> dict[str, str]:
    return {
        str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


@pytest.mark.django_db(transaction=True)
def test_a_fix_of_the_right_scene_passes_every_code_answer_and_a_rerun_skips_it(
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store, scenario = saved_scenario
    finished_source(store, scenario, fake_model)
    case = a_case(scenario)
    fixes(fake_model, 2, case.note)
    source_files = fingerprint(store.scenario_dir(SOURCE, scenario.key))

    assert run_notes(store, [case], name="notes-1", yes=True, parallel=1, pacing=False) == 0

    assert fingerprint(store.scenario_dir(SOURCE, scenario.key)) == source_files
    record = store.read_run("notes-1")
    assert record["kind"] == "notes" and record["cases"] == [case.id]
    assert record["app_commit"] == _app_commit()
    result = record["results"][case.id]
    assert result["status"] == "done" and result["why"] is None
    assert result["cost_usd"] > 0 and result["minutes"] is not None
    answers = store.read_answers("notes-1", case.id)
    verdicts = {name: answer["verdict"] for name, answer in answers["answers"].items()}
    assert verdicts == {
        "note_new_ad": "pass",
        "note_right_scenes": "pass",
        "note_untouched_same": "pass",
    }
    assert [item["item"] for item in answers["answers"]["note_right_scenes"]["items"]] == ["s2"]
    assert answers["cost_usd"] == result["cost_usd"]
    # Only the fix's own calls: the source ad's planning and three clips are not counted.
    calls = read_json(store.run_dir("notes-1") / "spend.json")["calls"].values()
    assert {row["scenario"] for row in calls} == {case.id}
    assert sum(row["purpose"] == "make_broll_clip" for row in calls) == 1
    assert not any(row["purpose"] in {"plan_ad", "check_page", "make_music"} for row in calls)
    saved = read_json(store.scenario_dir("notes-1", case.id) / "case.json")
    assert saved["note"] == case.note and saved["scenes"] == [2]
    source_session = read_json(store.scenario_dir(SOURCE, scenario.key) / "scenario.json")
    assert saved["source"]["run"] == SOURCE and saved["source"]["scenario"] == scenario.key
    assert saved["source"]["session_id"] == source_session["session_id"]
    assert saved["source"]["ad"] and saved["copy_session_id"] != saved["source"]["session_id"]
    exported = store.export_dir("notes-1", case.id) / case.id
    simple = read_json(exported / "regression.json")
    assert simple["notes"] == [
        {
            "number": 1,
            "text": case.note,
            "before": "note-1/before.mp4",
            "after": "note-1/after.mp4",
        }
    ]
    note_case = simple["note_case"]
    assert note_case["id"] == case.id and note_case["expected_scenes"] == [2]
    assert [scene["number"] for scene in note_case["scenes"]] == [1, 2, 3]
    assert [scene["changed"] for scene in note_case["scenes"]] == [False, True, False]
    for scene in note_case["scenes"]:
        assert (exported / scene["before_clip"]).stat().st_size > 0
        assert (exported / scene["after_clip"]).stat().st_size > 0
    assert store.scenario_done("notes-1", case.id)

    capsys.readouterr()
    assert run_notes(store, [case], name="notes-1", yes=True, parallel=1, pacing=False) == 0
    assert "shampoo-s2: skipped (already done)" in capsys.readouterr().out
    assert len(read_json(store.run_dir("notes-1") / "spend.json")["calls"]) == len(list(calls))


@pytest.mark.django_db(transaction=True)
def test_a_fix_of_another_scene_fails_right_scenes_but_keeps_untouched_scenes_same(
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
) -> None:
    store, scenario = saved_scenario
    finished_source(store, scenario, fake_model)
    case = a_case(scenario)
    fixes(fake_model, 3, case.note)

    assert run_notes(store, [case], name="wrong", yes=True, parallel=1, pacing=False) == 0

    answers = store.read_answers("wrong", case.id)["answers"]
    assert answers["note_new_ad"]["verdict"] == "pass"
    assert answers["note_right_scenes"]["verdict"] == "fail"
    assert "no owner fix" in answers["note_right_scenes"]["reason"]
    assert answers["note_untouched_same"]["verdict"] == "pass"
    note_case = read_json(store.export_dir("wrong", case.id) / case.id / "regression.json")[
        "note_case"
    ]
    assert [scene["changed"] for scene in note_case["scenes"]] == [False, False, True]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "says,status",
    [("Sorry, I can't change that part.", "done"), ("Which bottle do you mean?", "stopped")],
    ids=["gives-up", "asks-the-owner"],
)
def test_no_new_ad_fails_new_ad_with_the_apps_words_and_a_question_stops_the_case(
    says: str,
    status: str,
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
) -> None:
    store, scenario = saved_scenario
    finished_source(store, scenario, fake_model)
    case = a_case(scenario)
    fake_model.respond("produce", turn(says=says))

    assert run_notes(store, [case], name="none", yes=True, parallel=1, pacing=False) == int(
        status != "done"
    )

    result = store.read_run("none")["results"][case.id]
    assert result["status"] == status and result["minutes"] is None
    answers = store.read_answers("none", case.id)["answers"]
    assert answers["note_new_ad"]["verdict"] == "fail"
    assert says in answers["note_new_ad"]["reason"]
    assert answers["note_right_scenes"]["verdict"] == "fail"
    assert answers["note_untouched_same"]["verdict"] == "not_reached"
    simple = read_json(store.export_dir("none", case.id) / case.id / "regression.json")
    assert "after" not in simple["notes"][0]
    assert all(
        scene["after_clip"] is None and not scene["changed"]
        for scene in simple["note_case"]["scenes"]
    )
    if status == "stopped":
        assert result["why"] == f"The app asked the owner: {says}"
        assert not store.scenario_done("none", case.id)
        # Carrying on retries the stopped case; it reports only its own attempt's cost.
        assert run_notes(store, [case], name="none", yes=True, parallel=1, pacing=False) == 1
        again = store.read_run("none")["results"][case.id]
        assert again["cost_usd"] == pytest.approx(result["cost_usd"])
        spent = read_json(store.run_dir("none") / "spend.json")["calls"].values()
        assert sum(float(row["cost_usd"]) for row in spent) == pytest.approx(2 * result["cost_usd"])


@pytest.mark.django_db(transaction=True)
def test_a_source_with_no_finished_ad_or_a_busy_chat_stops_before_copying(
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
) -> None:
    from chat.models import Session
    from jobs.models import ProducedItem

    store, scenario = saved_scenario
    finished_source(store, scenario, fake_model)
    case = a_case(scenario)
    source = source_of(store, case)
    original = Session.objects.get(pk=source.session_id)

    def attempt(run: str) -> str | None:
        ended = run_note_case(
            store,
            run,
            case,
            source,
            SpendLedger(store.run_dir(run) / "spend.json", Decimal("4"), pacing=False),
            database_url=url_for_database(connection.settings_dict["NAME"]),
            execution=run,
        )
        assert ended.status == "stopped" and ended.copy_session_id is None
        assert Session.objects.count() == 1
        saved = read_json(store.scenario_dir(run, case.id) / "case.json")
        assert saved["source"]["run"] == SOURCE and saved["copy_session_id"] is None
        return ended.why

    Session.objects.filter(pk=original.pk).update(producer_running=True)
    assert "source chat is not idle" in (attempt("busy") or "")
    Session.objects.filter(pk=original.pk).update(producer_running=False)
    ProducedItem.objects.filter(kind=ProducedItem.Kind.FINISHED_AD).delete()
    assert attempt("no-ad") == f"source {SOURCE} has no finished ad"
    job = original.jobs.get()
    assert [w["kind"] for w in job.warnings][-2:] == ["regression_scenario_failed"] * 2


@pytest.mark.django_db(transaction=True)
def test_a_run_with_any_unusable_source_refuses_to_start_and_spends_nothing(
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store, scenario = saved_scenario
    finished_source(store, scenario, fake_model)
    capsys.readouterr()
    (store.scenario_dir(SOURCE, scenario.key) / "answers.json").unlink()
    cases = [
        a_case(scenario, id="missing-run", source_run="regression-3"),
        a_case(scenario, id="no-answers"),
    ]

    assert run_notes(store, cases, name="stops", yes=True, parallel=2, pacing=False) == 1

    lines = capsys.readouterr().out.splitlines()
    assert any(
        line.startswith("Cannot run: missing-run: source run regression-3 has no finished")
        for line in lines
    )
    assert any(
        line.startswith(f"Cannot run: no-answers: source run {SOURCE} has no finished")
        for line in lines
    )
    assert not any(line.startswith("Estimate") for line in lines)
    assert not store.run_dir("stops").exists()


@pytest.mark.django_db(transaction=True)
def test_a_setup_refusal_mid_run_stops_new_cases_and_writes_stop(
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store, scenario = saved_scenario
    finished_source(store, scenario, fake_model)
    capsys.readouterr()
    first, second = a_case(scenario, id="first"), a_case(scenario, id="second")
    fake_model.respond("produce", RefusedForMoney("our account's quota is used up"))

    assert run_notes(store, [first, second], name="halt", yes=True, parallel=1, pacing=False) == 1

    stop = "STOP: first: RefusedForMoney: our account's quota is used up"
    assert stop in capsys.readouterr().out.splitlines()
    assert (store.run_dir("halt") / "STOP").read_text().splitlines() == [stop]
    record = store.read_run("halt")
    assert set(record["results"]) == {"first"}
    assert "setup" in record["stopped_why"]
    assert not store.scenario_dir("halt", "second").exists()


def test_a_saved_run_is_never_continued_at_another_commit_or_with_other_cases(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    case = a_case(Scenario("ridge-wallet", None, "", "", None), id="ridge-wallet-s3")
    write_json(
        store.run_dir("old") / "run.json",
        {"kind": "notes", "name": "old", "app_commit": "another", "cases": [case.id]},
    )
    with pytest.raises(ValueError, match="another app commit"):
        run_notes(store, [case], name="old", yes=True)
    write_json(
        store.run_dir("other") / "run.json",
        {"kind": "notes", "name": "other", "app_commit": _app_commit(), "cases": ["x"]},
    )
    with pytest.raises(ValueError, match="other cases"):
        run_notes(store, [case], name="other", yes=True)
    write_json(store.run_dir("full") / "run.json", {"name": "full", "to": "full"})
    with pytest.raises(ValueError, match="not a note run"):
        run_notes(store, [case], name="full", yes=True)


def test_the_catalogue_holds_julians_approved_cases() -> None:
    cases = load_note_cases()
    assert len(cases) == 11
    assert len({case.id for case in cases}) == 11
    assert {case.source_run for case in cases} == {"baseline-2", "regression-3"}
    snow = next(case for case in cases if case.id == "snow-s1-s3")
    assert snow.scenes == (1, 2, 3)
    assert all(case.note.strip() and case.grade.strip() for case in cases)


@pytest.mark.parametrize(
    "change,why",
    [
        ({"scenario": "nope"}, "unknown scenario"),
        ({"scenes": []}, "scenes must be"),
        ({"scenes": [0]}, "scenes must be"),
        ({"scenes": ["3"]}, "scenes must be"),
        ({"note": "  "}, "note must be"),
        ({"id": "Bad Id"}, "id must be"),
        ({"extra": 1}, "needs exactly the fields"),
    ],
)
def test_a_malformed_catalogue_entry_is_refused_by_name(
    tmp_path: Path, change: dict[str, object], why: str
) -> None:
    good = load_note_cases()[0].entry()
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([good, {**good, "id": "second-case", **change}]))
    with pytest.raises(ValueError, match=why) as refused:
        load_note_cases(path)
    assert repr(change.get("id", "second-case")) in str(refused.value)
    path.write_text(json.dumps([good, good]))
    with pytest.raises(ValueError, match=f"{good['id']}.*repeated"):
        load_note_cases(path)


def test_the_command_refuses_an_unknown_case_before_starting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from evals.regression.__main__ import main

    assert main(["--store", str(tmp_path), "notes", "try", "--only", "nope", "--yes"]) == 1
    assert "Unknown note cases: nope" in capsys.readouterr().out
    assert not Store(tmp_path).read_run("try")
