"""Paid runs check every key, source and service before starting, and spot setup refusals."""

from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone
from pytest_django import Settings

from adforge import paid_runs
from evals.judge.command import main as judge_main
from evals.regression.note_cases import NoteCase
from evals.regression.notes import run_notes
from evals.regression.store import Store
from gateway.fake import FakeModel
from gateway.gateway import use_model


def test_each_stage_names_every_unset_key_it_needs_and_a_fake_needs_none(
    settings: Settings,
) -> None:
    assert paid_runs.missing_keys("plans") == ["GEMINI_API_KEY is not set"]
    assert paid_runs.missing_keys("full") == [
        "GEMINI_API_KEY is not set",
        "OPENAI_API_KEY (pictures are drawn on OpenAI's own API) is not set",
        "CREATIFY_API_ID is not set",
        "CREATIFY_API_KEY is not set",
        "FAL_KEY is not set",
        "ELEVENLABS_API_KEY is not set",
    ]
    settings.AZURE_PICTURES_KEY, settings.AZURE_PICTURES_BASE_URL = "", ""
    settings.OPENAI_TEXT_ON_AZURE = False
    assert paid_runs.missing_keys("judge") == [
        "AZUREE_OPENAI_KEY and AZUREE_OPENAI_BASE_URL are not set (the judge runs on Azure)"
    ]
    with use_model(FakeModel()):
        assert paid_runs.missing_keys("full") == []


@pytest.mark.parametrize(
    ("error", "setup"),
    [
        ("NotSetUp: Videos are watched on Gemini's API, and GEMINI_API_KEY is not set", True),
        ("RefusedForMoney: You exceeded your current quota", True),
        ("AuthenticationError: Error code: 401 - invalid key", True),
        ("HTTPStatusError: Client error '403 Forbidden' for url 'https://rest.fal.ai/'", True),
        ("HTTPStatusError: Client error '402 Payment Required' for url 'https://x/'", True),
        ("OutsideServiceDown: The model provider was still down after 5 tries", False),
        ("BlockedBySafetyFilter: refused", False),
        ("HTTPStatusError: Client error '404 Not Found' for url 'https://x/'", False),
        (None, False),
    ],
)
def test_a_setup_refusal_is_a_missing_or_wrong_key_or_no_money(
    error: str | None, setup: bool
) -> None:
    assert paid_runs.refused_for_setup(error) is setup


def test_a_note_run_without_keys_refuses_to_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = Store(tmp_path)
    case = NoteCase(
        id="ridge-s3",
        scenario="ridge-wallet",
        source_run="baseline-2",
        note="n",
        grade="g",
        graded_from="t",
        scenes=(3,),
    )

    assert run_notes(store, [case], name="keys", yes=True, pacing=False) == 1

    lines = capsys.readouterr().out.splitlines()
    assert "Cannot run: CREATIFY_API_KEY is not set" in lines
    assert any(line.startswith("Cannot run: ridge-s3: source run baseline-2") for line in lines)
    assert not store.run_dir("keys").exists()


def test_the_judge_refuses_to_start_without_its_azure_account(
    tmp_path: Path, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    settings.OPENAI_TEXT_ON_AZURE = False
    assert judge_main(["some-run"], store=Store(tmp_path)) == 1
    assert capsys.readouterr().out.startswith("Cannot run: AZUREE_OPENAI_KEY")


@pytest.mark.django_db
def test_an_ad_run_refuses_while_another_ad_is_being_made(settings: Settings) -> None:
    from chat.models import Session

    for name in ("GEMINI_API_KEY", "OPENAI_PICTURES_KEY", "CREATIFY_API_ID"):
        setattr(settings, name, "set")
    for name in ("CREATIFY_API_KEY", "FAL_KEY", "ELEVENLABS_API_KEY"):
        setattr(settings, name, "set")
    assert paid_runs.ad_run_problems(idle=True) == []
    Session.objects.create(producer_running=True, producer_seen_at=timezone.now())
    Session.objects.create(
        producer_running=True, producer_seen_at=timezone.now() - timedelta(hours=1)
    )
    assert paid_runs.ad_run_problems(idle=True) == [
        "1 producers and 0 steps are already making ads"
    ]
    assert paid_runs.ad_run_problems(idle=False) == []
