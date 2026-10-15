"""A graded round's record, end to end: the app makes a B-roll clip through the chat, the
clip judge fails it and the auto-redo remakes it; the round is exported from the app into a
run folder, Julian's grading page is built from it, his taps (as the page saves them) become
the run's grades, and the numbers are printed from those files. The producer's model, the
scene models, the video model and the judge are faked at the gateway."""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from evals import grading_page, results, round_export
from gateway.models import ModelCall
from jobs.models import Job, ProducedItem, SceneStep

from .test_producer_auto_fix import FLOATS, SPINS, remade_once, remade_twice  # noqa: F401
from .test_producer_broll import checked, instructed  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db(transaction=True)

AD = "1-teapot"


def exported(tmp_path: Path) -> Path:
    job = Job.objects.get()
    run = tmp_path / "round"
    round_export.export_run({AD: str(job.session_id)}, run, "Round test")
    return run


def tapped(run: Path, taps: dict[str, dict[str, Any]]) -> None:
    """Julian's taps, as the page saves them: one document per tap block, its id the key."""
    blocks = set(re.findall(r'class="grade" data-doc="([^"]+)"', grading_page.page(run)))
    assert set(taps) <= blocks, "every tap is on a block the page shows"
    (run / "grades.json").write_text(json.dumps(taps))


def test_a_remade_scene_is_exported_with_its_first_clip_and_kept_remake(
    remade_once: SceneStep,  # noqa: F811
    tmp_path: Path,
) -> None:
    run = exported(tmp_path)

    first, remake = ProducedItem.objects.filter(kind="clip").order_by("id")
    scene = json.loads((run / "results.json").read_text())[f"{AD}:2"]
    assert [t["remake"] for t in scene["tries"]] == [None, 1]
    assert [t["ruling"]["verdict"] for t in scene["tries"]] == ["fail", "pass"]
    assert scene["tries"][0]["judge_saw"] == [FLOATS]
    assert (scene["kept"]["pk"], scene["kept_is_remake"]) == (remake.pk, True)
    assert (run / AD / "scene-2" / "first.mp4").read_bytes() == _bytes(first)
    assert (run / AD / "scene-2" / "remake-1.mp4").read_bytes() == _bytes(remake)
    assert json.loads((run / AD / "scene-2" / "ruling-first.json").read_text()) == first.ruling
    costs = json.loads((run / "costs.json").read_text())["ads"][AD]
    calls = ModelCall.objects.filter(job=Job.objects.get())
    assert costs["total_usd"] > 0
    assert costs["total_usd"] == pytest.approx(float(sum(c.cost_usd or 0 for c in calls)))
    assert costs["video_usd"] == pytest.approx(
        float(sum(c.cost_usd or 0 for c in calls if c.provider in ("creatify", "heygen")))
    )
    assert 0 < scene["costs"]["total"] < costs["total_usd"]


def test_the_page_grades_the_first_and_the_kept_clip_with_the_judge_in_view(
    remade_once: SceneStep,  # noqa: F811
    tmp_path: Path,
) -> None:
    page = grading_page.page(exported(tmp_path))

    blocks = re.findall(r'class="grade" data-doc="([^"]+)"', page)
    assert blocks == [f"{AD}__whole-ad", f"{AD}__scene-2", f"{AD}__scene-2__kept"]
    assert "judge: fail (unreal motion)" in page and "judge: pass" in page
    assert FLOATS in page
    assert f'src="{AD}/scene-2/first.mp4"' in page and f'src="{AD}/scene-2/remake-1.mp4"' in page
    # One cause tap per clip graded, with no "judge" choice.
    assert page.count('data-field="cause"') == 2
    assert re.findall(
        r'data-v="(\w+)">', page.split('data-field="cause"')[1].split("</div>")[0]
    ) == [
        "planner",
        "start_picture",
        "motion_prompt",
        "video_model",
    ]


def test_the_numbers_of_a_graded_round_come_from_its_files(
    remade_once: SceneStep,  # noqa: F811
    tmp_path: Path,
) -> None:
    run = exported(tmp_path)
    tapped(
        run,
        {
            f"{AD}__whole-ad": {"grade": "pass", "note": "", "cause": None},
            f"{AD}__scene-2": {"grade": "fail", "note": "it floats", "cause": "video_model"},
            f"{AD}__scene-2__kept": {"grade": "pass", "note": "", "cause": None},
        },
    )

    report = results.report(results.load_run(run))

    assert "- Ads graded: 1, good 1, bad 0. These ads hold the clips the auto-redo kept." in report
    assert "- First clips graded: 1, passed 0, failed 1." in report
    assert f"kept a remake in 1 scenes ({AD} s2)" in report
    assert "- Kept remakes graded: 1, passed 1, failed 0." in report
    assert f"The judge caught 1 ({AD} s2), missed 0" in report
    assert "- the video model did the order badly: 1" in report
    assert f"- {AD}: $" in report


def test_a_first_clip_kept_over_its_remakes_has_no_kept_clip_to_grade(
    remade_twice: SceneStep,  # noqa: F811
    tmp_path: Path,
) -> None:
    run = exported(tmp_path)

    scene = json.loads((run / "results.json").read_text())[f"{AD}:2"]
    assert [t["remake"] for t in scene["tries"]] == [None, 1, 2]
    assert (scene["kept"]["remake"], scene["kept_is_remake"]) == (None, False)
    assert scene["tries"][1]["judge_saw"][0] == SPINS
    page = grading_page.page(run)
    assert f"{AD}__scene-2__kept" not in page
    assert page.count("app kept this") == 1


def _bytes(clip: ProducedItem) -> bytes:
    from adforge import file_store

    return file_store.read(clip.file)


# --- The numbers, from hand-made records ----------------------------------------------------


def _run(tmp_path: Path, meta: dict[str, Any], **files: Any) -> results.Run:
    for name, content in files.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(content))
    (tmp_path / "run.json").write_text(json.dumps(meta))
    return results.load_run(tmp_path)


def _try(verdict: str, remake: int | None = None) -> dict[str, Any]:
    return {"remake": remake, "ruling": {"verdict": verdict, "wording": "w1"}}


ROUND_4_LIKE = {
    "name": "R",
    "ad_grade_is": "first",
    "grades": "grades.json",
    "judge": "judge.json",
    "taps": "taps.json",
    "ad_folders": {"8": "8-cleaner", "9": "9-chili"},
}
GRADES = {
    "8-cleaner__whole-ad": {"grade": "fail", "note": ""},
    "8-cleaner__scene-2": {"grade": "fail", "note": "a second bottle"},
    "8-cleaner__scene-3": {"grade": "pass", "note": ""},
    "8-cleaner__scene-4": {"grade": "fail", "note": ""},
    "9-chili__whole-ad": {"grade": "pass", "note": ""},
    "9-chili__scene-2": {"grade": "pass", "note": ""},
    "fix-1__scene-3-clip": {"grade": "pass", "note": ""},
}
JUDGE = {
    "8:2": {"tries": [_try("pass")], "kept": {"remake": None}, "costs": {"total": 0.4}},
    "8:3": {
        "tries": [_try("fail"), _try("pass", 1)],
        "kept": {"remake": 1},
        "costs": {"total": 1.2},
    },
    "8:4": {
        "tries": [_try("fail"), _try("fail", 1), _try("fail", 2)],
        "kept": {"remake": 2},
        "costs": {"total": 2.0},
    },
    "9:2": {"tries": [_try("not_checked")], "kept": {"remake": None}, "costs": {"total": 0.4}},
}
TAPS = {
    "8-cleaner__scene-3": {"fixed_1": "yes", "fix_check_agree": "no"},
    "8-cleaner__scene-4": {"fixed_1": "no", "fixed_2": "partly", "fix_check_agree": "yes"},
}


def test_the_judge_is_scored_against_his_grades_on_first_clips(tmp_path: Path) -> None:
    report = results.report(_run(tmp_path, ROUND_4_LIKE, grades=GRADES, judge=JUDGE, taps=TAPS))

    assert (
        "- Clips he failed: 2. The judge caught 1 (8-cleaner s4), missed 1 (8-cleaner s2), "
        "couldn't check 0." in report
    )
    assert (
        "- Clips he passed: 2. The judge wrongly failed 1 (8-cleaner s3), agreed on 0, "
        "couldn't check 1 (9-chili s2)." in report
    )
    assert "Judge wording: w1." in report


def test_round_4_counts_its_taps_on_the_kept_remake_and_no_kept_grades(tmp_path: Path) -> None:
    report = results.report(_run(tmp_path, ROUND_4_LIKE, grades=GRADES, judge=JUDGE, taps=TAPS))

    assert "These ads hold the first clips (made without the auto-redo)." in report
    assert "- Ads graded: 2, good 1, bad 1." in report
    assert "- Ads where he passed every B-roll first clip: 1 of 2." in report
    # Kept remakes have no pass/fail grade in round 4: the ad's final clips aren't known.
    assert (
        "- Ads where he passed every B-roll final clip (after the auto-redo): 1 of 1 "
        "(1 ads have a clip not graded)." in report
    )
    assert "the auto-redo made 3 remakes and kept a remake in 2 scenes" in report
    assert "- Kept remakes graded pass or fail: none in this run." in report
    # The tap on the remake the app kept: remake 2 of scene 4.
    assert "(his taps): yes 1, partly 1, no 0; not tapped 0." in report
    assert "- He agreed with the fix check 1, disagreed 1, not tapped 0." in report
    assert (
        "- Clips he passed that the auto-redo swapped for a remake: 1 (8-cleaner s3); of those, "
        "kept remakes he failed: 0." in report
    )
    assert (
        "### First step that broke (his tap per failed clip)\n- Not recorded in this run." in report
    )
    assert "- No cost record in this run." in report
    assert "- Judge and remakes, all 4 B-roll scenes: $4.00." in report
    assert "fix-1" not in report


def test_his_cause_taps_are_counted_and_a_failed_clip_without_one_is_said(tmp_path: Path) -> None:
    grades = {
        "a__whole-ad": {"grade": "fail"},
        "a__scene-2": {"grade": "fail", "cause": "planner"},
        "a__scene-3": {"grade": "fail", "cause": "planner"},
        "a__scene-3__kept": {"grade": "fail", "cause": "motion_prompt"},
        "a__scene-4": {"grade": "fail", "cause": None},
    }
    judge = {
        "a:2": {"tries": [_try("fail")], "kept": {"remake": None}},
        "a:3": {"tries": [_try("fail"), _try("fail", 1)], "kept": {"remake": 1}},
        "a:4": {"tries": [_try("pass")], "kept": {"remake": None}},
    }
    meta = {"name": "R5", "ad_grade_is": "kept", "grades": "grades.json", "judge": "judge.json"}
    report = results.report(_run(tmp_path, meta, grades=grades, judge=judge))

    assert "- the plan asked for the wrong or an impossible thing: 2" in report
    assert "- the motion prompt: 1" in report
    assert "- the video model did the order badly: 0" in report
    assert "- Failed clips with no tap: 1" in report
    assert (
        "### Judge against his grades, kept remakes\n"
        "- Clips he failed: 1. The judge caught 1 (a s3)" in report
    )


def test_costs_are_listed_per_ad_with_their_source(tmp_path: Path) -> None:
    meta = {"name": "R", "grades": "grades.json", "costs": "costs.json"}
    costs = {"source": "the app", "ads": {"a": {"total_usd": 2.5}, "b": {"total_usd": 1.25}}}
    grades = {"a__whole-ad": {"grade": "pass"}, "b__whole-ad": {"grade": "fail"}}
    report = results.report(_run(tmp_path, meta, grades=grades, costs=costs))

    assert "- a: $2.50\n- b: $1.25\n- All 2 ads: $3.75. Source: the app." in report
    assert "- No judge verdicts in this run." in report


def test_the_command_prints_the_numbers_of_each_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(tmp_path, ROUND_4_LIKE, grades=GRADES, judge=JUDGE, taps=TAPS)
    results.main([str(tmp_path)])
    assert capsys.readouterr().out.startswith("## R: numbers")
