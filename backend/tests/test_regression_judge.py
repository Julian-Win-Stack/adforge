"""Ask the eval judge through the regression command, on real saved run files."""

import json
import shutil
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from pytest_django import Settings

from evals.regression.__main__ import main
from evals.regression.store import Store, write_json
from gateway.fake import FakeModel
from gateway.gateway import use_model
from gateway.types import BlockedBySafetyFilter, ModelReply, ModelRequest

from .conftest import openai_answer, picture


def answer(verdict: str = "pass", *, product: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "sees": "The handle crosses the hand."
        if verdict == "fail"
        else "The mug is held normally.",
        "when": "the starting picture" if verdict == "fail" else "",
        "on_what": "hand" if verdict == "fail" else "nothing",
        "verdict": verdict,
    }
    if product:
        result.update(photo_sides=[], shown_sides=[])
    return result


@pytest.fixture
def saved_run(tmp_path: Path) -> Store:
    store = Store(tmp_path / "store")
    store.write_run(
        "baseline-2",
        {"name": "baseline-2", "scenarios": ["mug"], "finished_at": "2026-11-16T01:00:00Z"},
    )
    base = store.export_dir("baseline-2", "mug") / "mug"
    (base / "scene-1").mkdir(parents=True)
    (base / "scene-1/start-picture.png").write_bytes(picture(40, 60, (10, 90, 40)))
    (base / "page-photo-2.png").write_bytes(picture(40, 50, (90, 10, 40)))
    (base / "page.txt").write_text("A stoneware mug with a handle.")
    write_json(
        base / "regression.json",
        {
            "page_files": ["page.txt", "page-photo-2.png"],
            "scenes": [
                {
                    "number": 1,
                    "line": "Lift your coffee.",
                    "shows": "The mug in her hand.",
                    "picture": "scene-1/start-picture.png",
                }
            ],
        },
    )
    write_json(
        base / "grading-records.json",
        {
            "product": "Stoneware mug",
            "page_text": "A stoneware mug with a handle.",
            "library": [{"number": 2, "label": "The mug's front and handle."}],
            "producer": [{"instructions": "APP PROMPT"}],
            "warnings": ["APP VERDICT"],
        },
    )
    return store


def run_judge(store: Store, *args: str) -> int:
    return main(["--store", str(store.root), "judge", "baseline-2", *args])


def test_a_picture_failed_twice_is_saved_with_every_ask(
    saved_run: Store, fake_model: FakeModel, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_model.respond("eval_judge_P1", answer(product=True))
    fake_model.respond("eval_judge_P4", answer("fail"), answer(), answer("fail"))

    assert run_judge(saved_run) == 0

    result = saved_run.read_judge_answers("baseline-2")["mug"]["P4"]["s1"]
    assert result["verdict"] == "fail"
    assert [ask["verdict"] for ask in result["asks"]] == ["fail", "pass", "fail"]
    assert all(
        ask["input_tokens"] == 1000 and ask["output_tokens"] == 100 for ask in result["asks"]
    )
    assert all(ask["cost_usd"] > 0 for ask in result["asks"])
    assert "cost" in capsys.readouterr().out.lower()
    request = next(request for request in fake_model.asked if request.purpose == "eval_judge_P1")
    assert len(request.images) == 2
    assert "The mug's front and handle." in request.images[0].label
    assert request.handoff.model_dump()["spoken_line"] == "Lift your coffee."
    assert request.handoff.model_dump()["product_name"] == "Stoneware mug"
    assert "APP PROMPT" not in request.handoff.model_dump_json()
    assert "APP VERDICT" not in request.handoff.model_dump_json()
    before, after = request.handoff.around_images()
    assert "Lift your coffee." in before
    assert after is not None and list(json.loads(after)) == ["question"]


def test_a_one_off_false_alarm_passes_both_reasks_and_is_not_failed(
    saved_run: Store, fake_model: FakeModel
) -> None:
    fake_model.respond("eval_judge_P4", answer("fail"), answer(), answer())

    assert run_judge(saved_run, "--questions", "P4") == 0

    result = saved_run.read_judge_answers("baseline-2")["mug"]["P4"]["s1"]
    assert result["verdict"] == "pass"
    assert [ask["verdict"] for ask in result["asks"]] == ["fail", "pass", "pass"]


def test_a_finished_ad_can_be_answered_doesnt_apply(
    saved_run: Store, fake_model: FakeModel
) -> None:
    base = saved_run.export_dir("baseline-2", "mug") / "mug"
    video = fake_model.download(url="https://fake.fal/video-1.mp4")
    (base / "ad.mp4").write_bytes(video)
    (base / "scene-1/first.mp4").write_bytes(video)
    record = json.loads((base / "regression.json").read_text())
    record["scenes"][0]["clip"] = "scene-1/first.mp4"
    write_json(base / "regression.json", record)
    fake_model.respond("eval_judge_A11", answer("doesnt_apply"))

    assert run_judge(saved_run, "--questions", "A11") == 0

    result = saved_run.read_judge_answers("baseline-2")["mug"]["A11"]["ad"]
    assert result["verdict"] == "doesnt_apply"
    assert len(result["asks"]) == 1
    assert len(fake_model.asked[0].images) == 2  # Page photo and this scene's still.


def test_a_refused_paid_call_keeps_its_bill_and_leaves_the_item_unjudged(
    saved_run: Store,
    fake_model: FakeModel,
    sentry_events: list[dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_model.respond(
        "eval_judge_P4",
        BlockedBySafetyFilter(
            "Refused the answer", input_tokens=1000, output_tokens=100, cached_tokens=500
        ),
    )

    assert run_judge(saved_run, "--questions", "P4") == 1

    result = saved_run.read_judge_answers("baseline-2")["mug"]["P4"]["s1"]
    assert "verdict" not in result and result["asks"] == []
    assert "Refused the answer" in result["error"]
    assert result["failed_asks"][0]["input_tokens"] == 1000
    assert result["failed_asks"][0]["cached_tokens"] == 500
    assert result["failed_asks"][0]["cost_usd"] == pytest.approx(0.0021)
    assert any(event["tags"]["kind"] == "eval_judge_unavailable" for event in sentry_events)
    assert "not judged" in capsys.readouterr().err
    fake_model.respond("eval_judge_P4", answer())
    assert run_judge(saved_run, "--questions", "P4") == 0
    result = saved_run.read_judge_answers("baseline-2")["mug"]["P4"]["s1"]
    assert result["verdict"] == "pass"
    assert result["failed_asks"][0]["cost_usd"] > 0


def test_resuming_asks_nothing_twice_and_completes_only_the_missing_reasks(
    saved_run: Store, fake_model: FakeModel
) -> None:
    fake_model.respond("eval_judge_P4", answer("fail"), answer("fail"), answer())
    assert run_judge(saved_run, "--questions", "P4") == 0
    assert len(fake_model.asked) == 3

    assert run_judge(saved_run, "--questions", "P4") == 0
    assert len(fake_model.asked) == 3

    answers = saved_run.read_judge_answers("baseline-2")
    answers["mug"]["P4"]["s1"]["asks"] = answers["mug"]["P4"]["s1"]["asks"][:1]
    answers["mug"]["P4"]["s1"].pop("verdict")
    saved_run.write_judge_answers("baseline-2", answers)
    fake_model.respond("eval_judge_P4", answer("fail"), answer())

    assert run_judge(saved_run, "--questions", "P4") == 0
    assert len(fake_model.asked) == 5
    assert len(saved_run.read_judge_answers("baseline-2")["mug"]["P4"]["s1"]["asks"]) == 3


def test_changing_the_question_asks_a_new_version_without_losing_the_old_spend(
    saved_run: Store, fake_model: FakeModel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evals.judge import prompts
    from evals.judge.records import answer_cost

    fake_model.respond("eval_judge_P4", answer(), answer())
    assert run_judge(saved_run, "--questions", "P4") == 0
    old = saved_run.read_judge_answers("baseline-2")["mug"]["P4"]["s1"]
    questions = tmp_path / "questions"
    shutil.copytree(prompts.QUESTION_DIR, questions)
    with (questions / "P4.md").open("a") as changed:
        changed.write("\nAn approved wording revision.\n")
    monkeypatch.setattr(prompts, "QUESTION_DIR", questions)

    assert run_judge(saved_run, "--questions", "P4") == 0

    current = saved_run.read_judge_answers("baseline-2")["mug"]["P4"]["s1"]
    assert current["judge"] != old["judge"]
    assert current["history"][0]["judge"] == old["judge"]
    assert answer_cost(current) == pytest.approx(2 * answer_cost(old))
    assert len(fake_model.asked) == 2


def test_each_paid_ask_keeps_cached_tokens_and_prints_its_discounted_cost(
    saved_run: Store, capsys: pytest.CaptureFixture[str]
) -> None:
    class CachedModel(FakeModel):
        def complete[Out: BaseModel](
            self, request: ModelRequest[Out], *, on_openai: bool = False
        ) -> ModelReply[Out]:
            return replace(
                super().complete(request, on_openai=on_openai),
                cached_tokens=500,
                cache_write_tokens=30,
            )

    model = CachedModel()
    model.respond("eval_judge_P4", answer())
    with use_model(model):
        assert run_judge(saved_run, "--questions", "P4") == 0

    ask = saved_run.read_judge_answers("baseline-2")["mug"]["P4"]["s1"]["asks"][0]
    assert ask["cached_tokens"] == 500 and ask["cache_write_tokens"] == 30
    assert ask["cost_usd"] == pytest.approx(0.0021)
    assert "$0.002100" in capsys.readouterr().out


def test_a_run_stopped_at_plans_has_nothing_to_judge(
    saved_run: Store, fake_model: FakeModel
) -> None:
    base = saved_run.export_dir("baseline-2", "mug") / "mug"
    record = json.loads((base / "regression.json").read_text())
    record["scenes"][0].pop("picture")
    write_json(base / "regression.json", record)

    assert run_judge(saved_run) == 0
    assert fake_model.asked == []


def test_the_judge_refuses_to_start_without_its_azure_account(
    saved_run: Store, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    settings.OPENAI_TEXT_ON_AZURE = False
    with use_model(None):
        assert run_judge(saved_run, "--questions", "P4") == 1
    assert "Cannot run: AZUREE_OPENAI_KEY" in capsys.readouterr().out
    assert not (saved_run.run_dir("baseline-2") / "judge-answers.json").exists()


@pytest.mark.parametrize("outcome", ["pass", "filtered", "malformed"])
def test_usage_from_the_api_survives_even_a_filtered_or_malformed_reply(
    saved_run: Store,
    openai_server: Callable[..., None],
    outcome: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reply = openai_answer(answer() if outcome != "malformed" else {"verdict": "pass"})
    reply["usage"]["input_tokens_details"] = {"cached_tokens": 500, "cache_write_tokens": 30}
    if outcome == "filtered":
        reply["status"] = "incomplete"
        reply["incomplete_details"] = {"reason": "content_filter"}
    openai_server(reply)

    assert run_judge(saved_run, "--questions", "P4") == (0 if outcome == "pass" else 1)

    result = saved_run.read_judge_answers("baseline-2")["mug"]["P4"]["s1"]
    asked = result["asks"] if outcome == "pass" else result["failed_asks"]
    assert len(asked) == 1
    assert asked[0]["cached_tokens"] == 500
    assert asked[0]["cache_write_tokens"] == 30
    assert asked[0]["input_tokens"] == 1200 and asked[0]["output_tokens"] == 300
    assert asked[0]["cost_usd"] == pytest.approx(0.0045)
    assert "$0.004500" in capsys.readouterr().out
