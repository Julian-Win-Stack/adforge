"""Re-ask saved labeled cases through the regression command, with model calls faked."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from evals.cases import half_for, load
from evals.regression.__main__ import main
from gateway.fake import FakeModel
from gateway.types import BlockedBySafetyFilter

from .conftest import picture


def answer(verdict: str = "pass") -> dict[str, Any]:
    return {
        "sees": "The hand crosses the handle."
        if verdict == "fail"
        else "The hand holds it normally.",
        "when": "the picture" if verdict == "fail" else "",
        "on_what": "hand" if verdict == "fail" else "nothing",
        "verdict": verdict,
    }


@pytest.fixture
def cases_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PROJECT_FILES", str(tmp_path))
    (tmp_path / "picture.png").write_bytes(picture(40, 60, (10, 90, 40)))
    (tmp_path / "photo.png").write_bytes(picture(50, 60, (90, 10, 40)))
    folder = tmp_path / "cases"
    folder.mkdir()
    return folder


def case(
    case_id: str, *, stage: str = "pictures", files: list[str] | None = None
) -> dict[str, Any]:
    return {
        "id": case_id,
        "kind": "regression",
        "half": half_for(case_id),
        "label": "pass",
        "reason": "",
        "inputs": {
            "handoff": {
                "stage": stage,
                "item": "s1" if stage != "ad" else "ad",
                "line": "Lift your coffee.",
                "shows": "A hand holds the mug.",
                "product_name": "Stoneware mug",
                "product_text": "A stoneware mug with a handle.",
                "page_photo_labels": ["The mug's front and handle."],
            },
            "files": files or ["project-files:picture.png", "project-files:photo.png"],
        },
        "source": {"round": "baseline-2", "ad": "mug", "graded_at": "", "from": "grades.json"},
        "verdicts": [],
    }


def test_saved_case_gets_the_counted_verdict_and_other_lines_stay_byte_for_byte(
    cases_dir: Path, fake_model: FakeModel, capsys: pytest.CaptureFixture[str]
) -> None:
    path = cases_dir / "P4.jsonl"
    other = case("untouched")
    other["reason"] = "A saved note with a Unicode separator: \u2028 unchanged."
    untouched = json.dumps(other, separators=(", ", ": "), ensure_ascii=False).encode() + b"\r\n"
    target = case("target")
    target["inputs"]["handoff"]["app_verdict"] = "APP VERDICT MUST NOT BE SHOWN"
    target["inputs"]["handoff"]["app_prompt"] = "APP PROMPT MUST NOT BE SHOWN"
    path.write_bytes(json.dumps(target).encode() + b"\n\n" + untouched)
    fake_model.respond("eval_judge_P4", answer("fail"), answer(), answer("fail"))

    assert main(["judge", "--cases", str(cases_dir), "--ids", "target"]) == 0

    saved = load(path)
    assert saved[0].verdicts[-1].verdict == "fail"
    assert [ask.verdict for ask in saved[0].verdicts[-1].asks or ()] == ["fail", "pass", "fail"]
    assert path.read_bytes().endswith(b"\n\n" + untouched)
    assert saved[1].verdicts == ()
    request = fake_model.asked[0]
    assert request.handoff.model_dump()["spoken_line"] == "Lift your coffee."
    assert len(request.images) == 2
    assert "APP VERDICT" not in request.handoff.model_dump_json()
    assert "APP PROMPT" not in request.handoff.model_dump_json()
    output = capsys.readouterr().out
    assert "1 cases" in output and "cost" in output.lower()


def test_case_mode_accepts_an_equals_option_without_a_regression_store(
    cases_dir: Path, fake_model: FakeModel
) -> None:
    path = cases_dir / "P4.jsonl"
    path.write_text(json.dumps(case("picture")) + "\n")
    fake_model.respond("eval_judge_P4", answer())

    assert main(["judge", f"--cases={cases_dir}"]) == 0

    assert load(path)[0].verdicts[-1].verdict == "pass"


def test_a_completed_case_is_not_asked_again_but_new_wording_has_its_own_verdict(
    cases_dir: Path, fake_model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evals.judge import prompts

    wording = cases_dir / "wording"
    shutil.copytree(prompts.QUESTION_DIR, wording)
    monkeypatch.setattr(prompts, "QUESTION_DIR", wording)
    path = cases_dir / "P4.jsonl"
    path.write_text(json.dumps(case("picture")) + "\n")
    fake_model.respond("eval_judge_P4", answer())
    command = ["judge", "--cases", str(cases_dir)]
    assert main(command) == 0
    first = load(path)[0].verdicts[-1]
    assert main(command) == 0
    assert len(fake_model.asked) == 1

    question = wording / "P4.md"
    question.write_text(question.read_text() + "\nAn additional general clarification.\n")
    fake_model.respond("eval_judge_P4", answer())
    assert main(command) == 0

    verdicts = load(path)[0].verdicts
    assert len(verdicts) == 2 and verdicts[0] == first
    assert verdicts[0].judge != verdicts[1].judge
    assert len(fake_model.asked) == 2


def test_a_crash_after_the_first_fail_resumes_only_its_missing_votes(
    cases_dir: Path, fake_model: FakeModel, capsys: pytest.CaptureFixture[str]
) -> None:
    path = cases_dir / "P4.jsonl"
    path.write_text(json.dumps(case("picture")) + "\n")
    fake_model.respond("eval_judge_P4", answer("fail"), KeyboardInterrupt())
    command = ["judge", "--cases", str(cases_dir)]
    with pytest.raises(KeyboardInterrupt):
        main(command)
    assert load(path)[0].verdicts == ()
    checkpoint = json.loads((cases_dir / ".judge-answers.json").read_text())
    assert len(checkpoint["P4"]["picture"]["asks"]) == 1

    fake_model.respond("eval_judge_P4", answer(), answer("fail"))
    assert main(command) == 0

    verdict = load(path)[0].verdicts[-1]
    assert verdict.verdict == "fail"
    assert [ask.verdict for ask in verdict.asks or ()] == ["fail", "pass", "fail"]
    assert len(fake_model.asked) == 4  # One interrupted call, three saved answers.
    checkpoint = json.loads((cases_dir / ".judge-answers.json").read_text())
    assert all(ask["cost_usd"] > 0 for ask in checkpoint["P4"]["picture"]["asks"])
    assert "will ask 1" in capsys.readouterr().out


def test_a_refused_call_posts_its_notice_and_saves_cost_without_a_case_verdict(
    cases_dir: Path,
    fake_model: FakeModel,
    sentry_events: list[dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = cases_dir / "P4.jsonl"
    original = json.dumps(case("picture")) + "\n"
    path.write_text(original)
    fake_model.respond(
        "eval_judge_P4",
        BlockedBySafetyFilter("Refused this picture", input_tokens=1000, output_tokens=100),
    )

    assert main(["judge", "--cases", str(cases_dir)]) == 1

    assert path.read_text() == original
    checkpoint = json.loads((cases_dir / ".judge-answers.json").read_text())["P4"]["picture"]
    assert "Refused this picture" in checkpoint["error"]
    assert checkpoint["failed_asks"][0]["cost_usd"] > 0
    assert any(event["tags"]["kind"] == "eval_judge_unavailable" for event in sentry_events)
    assert "picture" in capsys.readouterr().err


def test_unreadable_media_is_named_and_skipped_without_a_model_call(
    cases_dir: Path, fake_model: FakeModel, capsys: pytest.CaptureFixture[str]
) -> None:
    path = cases_dir / "P4.jsonl"
    original = (
        json.dumps(case("remote", files=["azure:media/picture.png", "project-files:photo.png"]))
        + "\n"
    )
    path.write_text(original)

    assert main(["judge", "--cases", str(cases_dir)]) == 1

    assert path.read_text() == original
    assert fake_model.asked == []
    assert "azure:media/picture.png" in capsys.readouterr().err


def test_question_and_id_filters_leave_other_cases_and_item_only_files_alone(
    cases_dir: Path, fake_model: FakeModel
) -> None:
    paths = {question: cases_dir / f"{question}.jsonl" for question in ("P1", "P4")}
    original = json.dumps(case("chosen")) + "\n" + json.dumps(case("other")) + "\n"
    for path in paths.values():
        path.write_text(original)
    (cases_dir / "judge_picture_item.jsonl").write_text("not a judge question file\n")
    fake_model.respond("eval_judge_P4", answer())

    assert main(["judge", "--cases", str(cases_dir), "--questions", "P4", "--ids", "chosen"]) == 0

    assert paths["P1"].read_text() == original
    assert load(paths["P4"])[0].verdicts[-1].verdict == "pass"
    assert load(paths["P4"])[1].verdicts == ()
    assert [request.purpose for request in fake_model.asked] == ["eval_judge_P4"]


def test_a_clips_saved_questions_share_the_same_frames_and_run_one_after_another(
    cases_dir: Path, fake_model: FakeModel
) -> None:
    video = fake_model.download(url="https://fake.fal/video-1.mp4")
    (cases_dir.parent / "clip.mp4").write_bytes(video)
    for question in ("C6", "C7"):
        row = case(
            f"clip-{question}",
            stage="clips",
            files=["project-files:clip.mp4", "project-files:photo.png"],
        )
        (cases_dir / f"{question}.jsonl").write_text(json.dumps(row) + "\n")
    fake_model.respond("eval_judge_C6", answer("fail"), answer("fail"), answer())
    fake_model.respond("eval_judge_C7", answer())

    assert main(["judge", "--cases", str(cases_dir)]) == 0

    assert [request.purpose for request in fake_model.asked] == [
        "eval_judge_C6",
        "eval_judge_C6",
        "eval_judge_C6",
        "eval_judge_C7",
    ]
    first = fake_model.asked[0]
    assert len(first.images) > 1
    assert "The mug's front and handle." in first.images[0].label
    assert all(
        [(image.label, image.data) for image in request.images]
        == [(image.label, image.data) for image in first.images]
        for request in fake_model.asked
    )
    assert load(cases_dir / "C6.jsonl")[0].verdicts[-1].verdict == "fail"


def test_saved_ad_stills_are_used_as_scene_stills_without_a_live_run(
    cases_dir: Path, fake_model: FakeModel
) -> None:
    path = cases_dir / "A11.jsonl"
    path.write_text(
        json.dumps(
            case(
                "ad",
                stage="ad",
                files=[
                    "project-files:picture.png",
                    "project-files:picture.png",
                    "project-files:photo.png",
                ],
            )
        )
        + "\n"
    )
    fake_model.respond("eval_judge_A11", answer("doesnt_apply"))

    assert main(["judge", "--cases", str(cases_dir)]) == 0

    assert load(path)[0].verdicts[-1].verdict == "doesnt_apply"
    assert len(fake_model.asked[0].images) == 3  # Page photo followed by both saved scene stills.


def test_a_completed_checkpoint_is_published_without_loading_missing_media(
    cases_dir: Path, fake_model: FakeModel, capsys: pytest.CaptureFixture[str]
) -> None:
    path = cases_dir / "P4.jsonl"
    original = json.dumps(case("picture")) + "\n"
    path.write_text(original)
    fake_model.respond("eval_judge_P4", answer())
    command = ["judge", "--cases", str(cases_dir)]
    assert main(command) == 0
    # A crash can leave the rich checkpoint durable before the case line is published.
    path.write_text(original)
    (cases_dir.parent / "picture.png").unlink()

    assert main(command) == 0

    assert load(path)[0].verdicts[-1].verdict == "pass"
    assert len(fake_model.asked) == 1
    assert "will ask 0" in capsys.readouterr().out


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_judge_help_describes_case_mode_without_a_store_or_model_call(
    flag: str,
    fake_model: FakeModel,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("EVAL_STORE", raising=False)

    with pytest.raises(SystemExit) as stopped:
        main(["judge", flag])

    assert stopped.value.code == 0
    assert "--cases" in capsys.readouterr().out
    assert fake_model.asked == []
