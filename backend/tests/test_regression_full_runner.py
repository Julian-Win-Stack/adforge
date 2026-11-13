"""Picture and full regression scenarios through the actual chat and scene agents."""

import json
import re
from dataclasses import replace
from decimal import Decimal
from threading import Barrier, Lock, get_ident
from typing import Any

import pytest
from django.db import connection

from evals.regression.budget import SpendLedger
from evals.regression.database import isolated_database, url_for_database
from evals.regression.runner import run_scenario
from evals.regression.scenarios import Scenario
from evals.regression.store import Store, read_json
from gateway.fake import FakeModel, turn
from gateway.gateway import running_tool
from gateway.models import ModelCall
from gateway.types import ToolUse, Turn, TurnRequest
from jobs.models import Job, ProducedItem

from .conftest import NO_CHOICES, READABLE, facts_ok
from .test_h3_planning import h3_plan
from .test_regression_runner import saved_scenario  # noqa: F401
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES
from .test_session_copy import snapshot

pytestmark = pytest.mark.django_db(transaction=True)


def scene_turn(request: TurnRequest) -> Turn:
    drawn = [
        event
        for event in request.handoff.conversation
        if isinstance(event, ToolUse) and event.tool == "draw_starting_picture"
    ]
    ruling = json.JSONDecoder().raw_decode(drawn[-1].result.split(": ", 1)[1])[0] if drawn else {}
    redraw = not drawn or ruling.get("status") == "failed"
    return turn(calls=[("draw_starting_picture", PICTURE_FORM)] if redraw else [("film", FILM)])


def script_render(
    fake_model: FakeModel, scenario: Scenario, *, full: bool = False, late_music: bool = False
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", h3_plan())
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.answer_unscripted("scene_agent", scene_turn)
    if full:

        def heard(_: object) -> dict[str, str]:
            call = running_tool()
            assert call is not None
            filmed = ModelCall.objects.filter(tool_call=call, purpose="make_broll_clip").latest(
                "pk"
            )
            said = re.search(r"<d>\[English\] (.*?)</d>", filmed.handoff["prompt"], re.DOTALL)
            assert said is not None
            return {"text": said[1]}

        fake_model.answer_unscripted("transcribe_line", heard)
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": scenario.link, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        *(
            [turn(calls=[("create_music", {"mood": "light upbeat"})])]
            if full and not late_music
            else []
        ),
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
        turn(says="The planned scenes are being made."),
        *(
            [turn(calls=[("create_music", {"mood": "light upbeat"})])]
            if full and late_music
            else []
        ),
        *(
            [turn(calls=[("assemble_ad", {})]), turn(says="Here is your finished ad.")]
            if full
            else []
        ),
    )


@pytest.mark.parametrize("picture_kind", [False, True], ids=["to-pictures", "picture-in-full"])
def test_pictures_stop_after_every_real_picture_check_without_filming_or_owner_failure(
    picture_kind: bool,
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
) -> None:
    store, scenario = saved_scenario
    if picture_kind:
        scenario = replace(scenario, kind="picture")
    script_render(fake_model, scenario)
    ledger = SpendLedger(store.run_dir("pictures") / "spend.json", Decimal("25"), pacing=False)

    result = run_scenario(
        store,
        "pictures",
        scenario,
        ledger,
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution="pictures-db",
        to="full" if picture_kind else "pictures",
    )

    assert result.status == "done", result.reason
    assert result.job_id is not None
    job = Job.objects.get(pk=result.job_id)
    pictures = job.produced.filter(kind=ProducedItem.Kind.STARTING_PICTURE)
    assert pictures.count() == 3
    for picture in pictures:
        assert picture.ruling is not None
        assert picture.ruling["status"] == "passed"
    assert not job.produced.filter(kind=ProducedItem.Kind.CLIP).exists()
    assert fake_model.broll_clips_asked == []
    assert not job.model_calls.filter(purpose="check_move").exists()
    assert not any("gave_up" in warning["kind"] for warning in job.warnings)
    assert store.scenario_done("pictures", scenario.key)
    exported = read_json(
        store.export_dir("pictures", scenario.key) / scenario.key / "regression.json"
    )
    assert all("picture" in scene and "clip" not in scene for scene in exported["scenes"])
    assert (
        store.read_answers("pictures", scenario.key)["answers"]["ad_finishes"]["verdict"]
        == "not_reached"
    )


def test_scene_agents_overlap_at_the_paid_service_with_separate_database_connections(
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
) -> None:
    store, scenario = saved_scenario
    script_render(fake_model, scenario)
    arrived = Barrier(3)
    lock = Lock()
    threads: set[int] = set()
    connections: set[int] = set()

    def simultaneous(request: TurnRequest) -> Turn:
        if not any(
            isinstance(event, ToolUse) and event.tool == "draw_starting_picture"
            for event in request.handoff.conversation
        ):
            with lock:
                threads.add(get_ident())
                assert connection.connection is not None
                connections.add(connection.connection.info.backend_pid)
            arrived.wait(timeout=10)
        return scene_turn(request)

    fake_model.answer_unscripted("scene_agent", simultaneous)
    ledger = SpendLedger(store.run_dir("parallel") / "spend.json", Decimal("25"), pacing=False)
    result = run_scenario(
        store,
        "parallel",
        scenario,
        ledger,
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution="parallel-db",
        to="pictures",
    )
    assert result.status == "done", result.reason
    assert len(threads) == len(connections) == 3


def test_full_scenario_finishes_and_exports_the_same_round_shape_with_real_video_seconds(
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
) -> None:
    store, scenario = saved_scenario
    script_render(fake_model, scenario, full=True)
    # An earlier, stopped attempt of this scenario left a clip in the same export folder.
    stale = store.export_dir("full", scenario.key) / scenario.key / "scene-2" / "remake-2.mp4"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"an earlier attempt's clip")
    ledger = SpendLedger(store.run_dir("full") / "spend.json", Decimal("25"), pacing=False)
    result = run_scenario(
        store,
        "full",
        scenario,
        ledger,
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution="full-db",
        to="full",
    )
    assert result.status == "done", result.reason
    assert result.job_id is not None
    job = Job.objects.get(pk=result.job_id)
    ad = job.produced.get(kind=ProducedItem.Kind.FINISHED_AD)
    assert len(ad.cuts) == 3
    assert len(fake_model.broll_clips_asked) == 3
    assert result.video_seconds == sum(clip["seconds"] for clip in fake_model.broll_clips_asked)
    assert store.read_answers("full", scenario.key)["answers"]["ad_finishes"]["verdict"] == "pass"
    exported = store.export_dir("full", scenario.key)
    results = read_json(exported / "results.json")
    assert set(results) == {f"{scenario.key}:{number}" for number in (1, 2, 3)}
    assert all(row["kept"] for row in results.values())
    # The scene agent filmed one clip per scene: each scene folder holds that one clip once,
    # named as its first, with nothing left from the earlier attempt.
    assert all([t["remake"] for t in row["tries"]] == [None] for row in results.values())
    for number in (1, 2, 3):
        scene_folder = exported / scenario.key / f"scene-{number}"
        assert sorted(p.name for p in scene_folder.glob("*.mp4")) == ["first.mp4"]
        assert sorted(p.name for p in scene_folder.glob("ruling-*.json")) == ["ruling-first.json"]
    assert (exported / scenario.key / "ad.mp4").stat().st_size > 0
    manifest = read_json(store.scenario_dir("full", scenario.key) / "kept-pictures.json")
    assert {row["job_id"] for row in manifest} == {str(job.pk)}
    assert {row["clip"] for row in manifest} == {cut["clip"] for cut in ad.cuts}
    assert all(
        "fact_checked" in row["scene_row"] and "shortened_from" in row["scene_row"]
        for row in manifest
    )
    assert all(row["step_row"]["kind"] == "scene_agent" for row in manifest)
    assert all(row["window"]["starting_pictures"] >= 1 for row in manifest)
    assert all(row["fixing_row"]["finished_at"] is None for row in manifest)
    import psycopg

    with isolated_database(
        store.scenario_dir("full", scenario.key) / "checkpoints/plan/db.dump"
    ) as url:
        with psycopg.connect(url) as copied:
            with copied.cursor() as cursor:
                cursor.execute("SELECT kind FROM jobs_produceditem")
                kinds = {row[0] for row in cursor.fetchall()}
    assert {"portrait", "person_in_room", "room_alone", "music"} <= kinds
    assert not {"starting_picture", "clip", "finished_ad"} & kinds
    prefix = read_json(
        store.scenario_dir("full", scenario.key) / "checkpoints/plan/prefix-state.json"
    )
    assert prefix["job"]["id"] == str(job.pk)
    assert prefix["library"] and prefix["produced_ids"]


@pytest.mark.parametrize("note_count", [1, 2])
def test_owner_notes_run_in_order_on_one_copy_without_changing_the_original(
    note_count: int,
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
) -> None:
    from django.db.models import Sum

    from chat.models import Message, Session

    store, scenario = saved_scenario
    notes = ["Make scene 2's delivery warmer.", "Make scene 3's delivery calmer."][:note_count]
    scenario = replace(scenario, owner_notes=notes)
    script_render(fake_model, scenario, full=True)
    original_rows: list[dict[str, list[dict[str, Any]]]] = []

    for number, note in enumerate(notes, 2):

        def pass_note(number: int = number, note: str = note) -> Turn:
            original = Session.objects.exclude(name__startswith="Copy of ").get()
            if not original_rows:
                original_rows.append(snapshot(original))
            return turn(
                calls=[
                    (
                        "pass_note",
                        {"scenes": [{"scene": number, "instruction": note, "quotes": [note]}]},
                    )
                ]
            )

        fake_model.respond(
            "produce",
            pass_note,
            turn(says="Working on your note."),
            turn(calls=[("assemble_ad", {})]),
            turn(says="Your note is done."),
        )
    ledger = SpendLedger(store.run_dir("notes") / "spend.json", Decimal("30"), pacing=False)
    result = run_scenario(
        store,
        "notes",
        scenario,
        ledger,
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution="notes-db",
        to="full",
    )
    assert result.status == "done", result.reason
    assert result.session_id is not None
    assert result.copy_session_id is not None and result.copy_session_id != result.session_id
    original = Session.objects.get(pk=result.session_id)
    copied = Session.objects.get(pk=result.copy_session_id)
    assert snapshot(original) == original_rows[0]
    assert (
        list(
            copied.messages.filter(role=Message.Role.USER)
            .order_by("seq")
            .values_list("text", flat=True)
        )[-note_count:]
        == notes
    )
    assert original.jobs.get().produced.filter(kind=ProducedItem.Kind.FINISHED_AD).count() == 1
    assert (
        copied.jobs.get().produced.filter(kind=ProducedItem.Kind.FINISHED_AD).count()
        == 1 + note_count
    )
    answers = store.read_answers("notes", scenario.key)
    assert answers["answers"]["note_untouched_same"]["verdict"] == "pass"
    assert len(answers["answers"]["note_untouched_same"]["items"]) == note_count
    metadata = read_json(store.scenario_dir("notes", scenario.key) / "scenario.json")
    assert len(metadata["notes"]) == note_count
    assert metadata["notes"][0]["after_ad"] == metadata["notes"][-1]["before_ad"] or note_count == 1
    total_rows = ModelCall.objects.aggregate(total=Sum("cost_usd"))["total"]
    assert total_rows > Decimal(str(result.cost_usd))
    assert result.cost_usd == pytest.approx(
        sum(
            float(call.cost_usd or 0)
            for call in ModelCall.objects.filter(pk__in=result.model_call_ids)
        )
    )
    exported = store.export_dir("notes", scenario.key) / scenario.key
    assert len(read_json(exported / "regression.json")["notes"]) == note_count
    copy_records = read_json(exported / "owner-notes" / scenario.key / "grading-records.json")
    assert sum(len(scene["clips"]) for scene in copy_records["scenes"]) == 3 + note_count
    manifest = read_json(store.scenario_dir("notes", scenario.key) / "kept-pictures.json")
    assert {row["job_id"] for row in manifest} == {str(original.jobs.get().pk)}
    selections = read_json(store.scenario_dir("notes", scenario.key) / "film-selections.json")
    assert {row["job_id"] for row in selections} == {
        str(original.jobs.get().pk),
        str(copied.jobs.get().pk),
    }


def test_picture_checks_can_redraw_before_the_pre_film_stop(
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
) -> None:
    store, scenario = saved_scenario
    script_render(fake_model, scenario)
    fake_model.respond(
        "check_scene_picture",
        {
            **PICTURE_PASSES,
            "product_same": {"passes": False, "says": "The rim has the wrong shape."},
        },
    )
    ledger = SpendLedger(store.run_dir("redraw") / "spend.json", Decimal("25"), pacing=False)
    result = run_scenario(
        store,
        "redraw",
        scenario,
        ledger,
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution="redraw-db",
        to="pictures",
    )
    assert result.status == "done", result.reason
    assert result.job_id is not None
    job = Job.objects.get(pk=result.job_id)
    pictures = list(job.produced.filter(kind=ProducedItem.Kind.STARTING_PICTURE))
    assert len(pictures) == 4
    assert sum((picture.ruling or {}).get("status") == "failed" for picture in pictures) == 1
    selected = read_json(store.scenario_dir("redraw", scenario.key) / "kept-pictures.json")
    assert len(selected) == 3
    assert all(
        (ProducedItem.objects.get(pk=row["picture"]).ruling or {}).get("status") == "passed"
        for row in selected
    )
    assert fake_model.broll_clips_asked == []


def test_spend_cap_keeps_already_paid_clips_without_starting_their_next_paid_check(
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
) -> None:
    from adforge import file_store

    store, scenario = saved_scenario
    script_render(fake_model, scenario, full=True)
    ledger = SpendLedger(store.run_dir("cap") / "spend.json", Decimal("0.25"), pacing=False)
    result = run_scenario(
        store,
        "cap",
        scenario,
        ledger,
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution="cap-db",
        to="full",
    )
    assert result.status == "stopped"
    assert "1.5 times" in result.reason
    assert result.job_id is not None
    job = Job.objects.get(pk=result.job_id)
    clips = list(job.produced.filter(kind=ProducedItem.Kind.CLIP))
    assert clips and all(file_store.exists(clip.file) for clip in clips)
    assert set(fake_model.clips_submitted) == set(fake_model.clips_downloaded)
    assert not job.model_calls.filter(purpose="transcribe_line").exists()
    assert not store.scenario_done("cap", scenario.key)
    assert store.dump_path("cap", scenario.key).is_file()


def test_picture_mode_draws_the_real_owner_part_and_stops_at_its_missing_saved_answer(
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
) -> None:
    store, scenario = saved_scenario
    plan = h3_plan()
    plan["plan"]["scenes"][1]["owner_part"] = {
        "what": "the lid",
        "source": "invented",
        "looks": "A round wooden lid with a small knob on top.",
    }
    question = "May I use the lid shown for scene 2?"
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", plan)
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": scenario.link, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("ask_owner", {"question": question, "photos": []})]),
    )
    ledger = SpendLedger(store.run_dir("owner-part") / "spend.json", Decimal("25"), pacing=False)
    result = run_scenario(
        store,
        "owner-part",
        scenario,
        ledger,
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution="owner-part-db",
        to="pictures",
    )
    assert result.status == "stopped" and question in result.reason
    assert result.job_id is not None
    scene = Job.objects.get(pk=result.job_id).scenes.get(number=2)
    assert scene.owner_part_picture_id is not None
    assert scene.owner_part_approved_at is None
    assert fake_model.broll_clips_asked == []


@pytest.mark.parametrize("pending_part", [False, True])
@pytest.mark.parametrize("exact_saved", [False, True])
def test_owner_part_saved_answer_does_not_answer_an_unrelated_freeform_question(
    pending_part: bool,
    exact_saved: bool,
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
) -> None:
    from chat.models import Message

    store, scenario = saved_scenario
    scenario = replace(scenario, saved_answers={"owner_part": "Yes, that lid looks right."})
    plan = h3_plan()
    if pending_part:
        plan["plan"]["scenes"][1]["owner_part"] = {
            "what": "the lid",
            "source": "invented",
            "looks": "A round wooden lid with a small knob on top.",
        }
    question = "What price should the ad say for the lid?"
    if exact_saved:
        scenario = replace(
            scenario, saved_answers={**scenario.saved_answers, question: "The price is $20."}
        )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", plan)
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": scenario.link, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("ask_owner", {"question": question})]),
        turn(says="The answer is saved."),
    )
    ledger = SpendLedger(store.run_dir("unanswered") / "spend.json", Decimal("25"), pacing=False)
    result = run_scenario(
        store,
        "unanswered",
        scenario,
        ledger,
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution="unanswered-db",
        to="pictures",
    )
    assert result.status == "stopped"
    assert (question in result.reason) is not exact_saved
    assert result.session_id is not None
    assert list(
        Message.objects.filter(session_id=result.session_id, role=Message.Role.USER).values_list(
            "text", flat=True
        )
    ) == [scenario.first_message, *(["The price is $20."] if exact_saved else [])]


@pytest.mark.parametrize(
    "question", ["May I use the lid shown for scene 2?", "Is this how the lid looks?"]
)
@pytest.mark.parametrize("bundled_question", [False, True])
def test_owner_part_saved_approval_reaches_the_real_approval_tool(
    question: str,
    bundled_question: bool,
    saved_scenario: tuple[Store, Scenario],  # noqa: F811
    fake_model: FakeModel,
) -> None:
    from chat.models import Message

    store, scenario = saved_scenario
    answer = "Yes, that lid looks right."
    scenario = replace(scenario, saved_answers={"owner_part": answer})
    plan = h3_plan()
    plan["plan"]["scenes"][1]["owner_part"] = {
        "what": "the lid",
        "source": "invented",
        "looks": "A round wooden lid with a small knob on top.",
    }
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", plan)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": scenario.link, "target_seconds": None})]),
        turn(
            calls=[
                ("plan_ad", {}),
                *([("ask_owner", {"question": question})] if bundled_question else []),
            ]
        ),
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        *([] if bundled_question else [turn(calls=[("ask_owner", {"question": question})])]),
        turn(calls=[("approve_owner_part", {"scene": 2})]),
        turn(says="The lid is approved."),
    )
    ledger = SpendLedger(store.run_dir("approved") / "spend.json", Decimal("25"), pacing=False)
    result = run_scenario(
        store,
        "approved",
        scenario,
        ledger,
        database_url=url_for_database(connection.settings_dict["NAME"]),
        execution="approved-db",
        to="pictures",
    )
    # No scene agents were requested; this checks the genuine approval flow itself.
    assert result.status == "stopped" and "No saved answer" not in result.reason
    assert result.session_id is not None
    assert result.job_id is not None
    scene = Job.objects.get(pk=result.job_id).scenes.get(number=2)
    assert scene.owner_part_approved_at is not None
    assert list(
        Message.objects.filter(session_id=result.session_id, role=Message.Role.USER).values_list(
            "text", flat=True
        )
    ) == [scenario.first_message, answer]
