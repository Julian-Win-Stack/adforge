"""Re-answer a lasting scenario dump, with no app service or store side effects."""

import shutil
from collections.abc import Callable
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest
from django.db import connection
from pytest_django import Settings

from evals.regression import database
from evals.regression.__main__ import main
from evals.regression.store import Store, write_json
from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs.models import Job

from .conftest import HeldSteps
from .test_producer_assemble import assemble, finish


@pytest.mark.django_db(transaction=True)
def test_reanswering_a_dump_only_rewrites_answers_and_excludes_replayed_costs(
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
    tmp_path: Path,
    settings: Settings,
) -> None:
    finish(fake_model, steps, say)
    assemble(fake_model, say)
    job = Job.objects.get()
    calls = list(ModelCall.objects.values_list("pk", flat=True))
    ModelCall.objects.update(cost_usd=0)
    ModelCall.objects.filter(pk=calls[0]).update(cost_usd=Decimal("0.5"))
    ModelCall.objects.create(
        job=job,
        purpose="old-baseline-call",
        provider="fake",
        model="fake",
        attempt=1,
        handoff={},
        outcome=ModelCall.Outcome.SUCCEEDED,
        cost_usd=99,
        duration_ms=1,
    )
    store = Store(tmp_path / "store")
    store.write_run("new", {"name": "new", "to": "full", "scenarios": ["mug"]})
    scenario = store.scenario_dir("new", "mug")
    write_json(
        scenario / "scenario.json",
        {"job_id": str(job.pk), "kind": "full", "model_call_ids": calls, "notes": []},
    )
    shutil.copytree(settings.MEDIA_ROOT, store.media_dir("new", "mug"))
    database.dump_database(
        database.url_for_database(str(connection.settings_dict["NAME"])),
        store.dump_path("new", "mug"),
    )
    store.write_answers("new", "mug", {"stale": True})
    store.mark_done("new", "mug")
    before = {
        str(p.relative_to(store.root)): (sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in store.root.rglob("*")
        if p.is_file() and p.name != "answers.json"
    }

    assert main(["--store", str(store.root), "answer", "new", "--only", "mug"]) == 0

    answered = store.read_answers("new", "mug")
    assert answered["cost_usd"] == 0.5
    assert answered["answers"]["ad_finishes"]["verdict"] == "pass"
    assert answered["first_try"] == {"scenes": 3, "first_passed": 3, "clips": 3}
    after = {
        str(p.relative_to(store.root)): (sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in store.root.rglob("*")
        if p.is_file() and p.name != "answers.json"
    }
    assert after == before
