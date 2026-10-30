"""A graded round's record, end to end: the app makes a B-roll clip through the chat, the
clip judge fails it and the auto-redo remakes it; the round is exported from the app into a
run folder, Julian's grading page is built from it, his taps (as the page saves them) become
the run's grades, and the numbers are printed from those files. The producer's model, the
scene models, the video model and the judge are faked at the gateway."""

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from evals import grading_page, results, round_export
from gateway.models import ModelCall
from jobs.library import (
    OwnerPhoto,
    PagePhoto,
    PictureLabel,
    SentList,
    add_to_library,
    keep_sent_list,
)
from jobs.models import AgentFix, Job, ProducedItem, SceneStep

from .test_library_contracts import SENT_LIST_FIXTURE
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
    # Every call costs a dollar, and the remake's video call is paid video.
    ModelCall.objects.update(cost_usd=Decimal(1))
    step_calls = list(remade_once.tool_call.model_calls.order_by("id"))
    ModelCall.objects.filter(pk=step_calls[-1].pk).update(provider="creatify")
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
    # A B-roll clip has no starting picture to export (spec #210 point 19).
    assert not (run / AD / "scene-2" / "start-picture.png").exists()
    inputs = (run / AD / "scene-2" / "inputs.txt").read_text()
    assert "PICTURE PROMPT: \n" in inputs
    assert "USAGE: " in inputs
    costs = json.loads((run / "costs.json").read_text())["ads"][AD]
    assert costs == {
        "total_usd": ModelCall.objects.filter(job=Job.objects.get()).count(),
        "video_usd": 1,
    }
    # The judge and the remake: the step's calls less making and collecting the first clip.
    assert scene["costs"]["total"] == len(step_calls) - 2
    ad = json.loads((run / AD / "ad.json").read_text())
    assert (ad["product_url"], ad["abandoned"], ad["first_finished_at"]) == (
        Job.objects.get().product_url,
        True,
        None,
    )
    assert json.loads((run / AD / "notes.json").read_text()) == []


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


def test_a_scene_the_agent_gave_up_on_is_exported_with_every_try_and_its_last_try_kept(
    remade_twice: SceneStep,  # noqa: F811
    tmp_path: Path,
) -> None:
    run = exported(tmp_path)

    scene = json.loads((run / "results.json").read_text())[f"{AD}:2"]
    assert [t["remake"] for t in scene["tries"]] == [None, 1, 2]
    # A give-up keeps its last try in the ad (Julian, 2026-10-23), so it is graded as kept.
    assert (scene["kept"]["remake"], scene["kept_is_remake"]) == (2, True)
    assert scene["gave_up"] == "Every try kept the same fault."
    assert scene["shows"] == remade_twice.shows
    assert scene["tries"][1]["judge_saw"][0] == SPINS
    page = grading_page.page(run)
    assert f"{AD}__scene-2__kept" in page
    assert "app kept this" in page


def test_a_first_clip_step_that_made_no_clip_is_passed_for_the_one_asked_again(
    remade_once: SceneStep,  # noqa: F811
    tmp_path: Path,
) -> None:
    # The first step failed before making a clip; the one asked again made the clips.
    again = SceneStep.objects.get(pk=remade_once.pk)
    again.pk = None
    again.save()
    ProducedItem.objects.filter(step=remade_once).update(step=again)
    AgentFix.objects.filter(step=remade_once).update(step=again)

    scene = json.loads((exported(tmp_path) / "results.json").read_text())[f"{AD}:2"]

    assert [t["remake"] for t in scene["tries"]] == [None, 1]


def test_a_broll_scene_with_no_clip_is_named_not_left_out_silently(
    remade_once: SceneStep,  # noqa: F811
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Its first clip and the fixing agent's tries, each in a step of its own.
    ProducedItem.objects.filter(scene=remade_once.scene, kind="clip").update(step=None)

    run = exported(tmp_path)

    assert json.loads((run / "results.json").read_text()) == {}
    assert f"{AD} scene 2: no B-roll clip made, left out" in capsys.readouterr().err


def test_the_ad_graded_whole_is_the_first_finished_one_and_newer_ones_are_kept_apart(
    remade_once: SceneStep,  # noqa: F811
    tmp_path: Path,
) -> None:
    from adforge import file_store

    job = Job.objects.get()
    for version in (1, 2):
        ProducedItem.objects.create(
            job=job,
            kind=ProducedItem.Kind.FINISHED_AD,
            version=version,
            file=file_store.save("ad.mp4", f"ad {version}".encode()),
        )

    run = exported(tmp_path)

    assert (run / AD / "ad.mp4").read_bytes() == b"ad 1"
    assert (run / AD / "ad-latest.mp4").read_bytes() == b"ad 2"
    ad = json.loads((run / AD / "ad.json").read_text())
    assert (ad["abandoned"], ad["finished_versions"]) == (False, 2)
    assert ad["wall_seconds"] >= 0


def test_an_ad_folder_unsafe_as_a_page_document_id_is_refused(
    remade_once: SceneStep,  # noqa: F811
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="letters, digits"):
        round_export.export_run({"11 mug": str(Job.objects.get().session_id)}, tmp_path, "R")
    assert not (tmp_path / "results.json").exists()


def test_exporting_again_keeps_the_run_file_written_by_hand(
    remade_once: SceneStep,  # noqa: F811
    tmp_path: Path,
) -> None:
    run = exported(tmp_path)
    (run / "run.json").write_text(json.dumps({"name": "edited", "grades": "grades.json"}))

    exported(tmp_path)

    assert json.loads((run / "run.json").read_text())["name"] == "edited"


def _sent(clip: ProducedItem, **change: Any) -> SentList:
    """The contract's fixture sent list, kept on `clip` (with `change` made to it)."""
    raw = json.loads(SENT_LIST_FIXTURE.read_text())
    sent = SentList.model_validate({**raw, **change})
    keep_sent_list(clip, sent)
    return sent


def test_every_clip_record_lists_what_it_was_sent_and_what_was_left_out(
    remade_once: SceneStep,  # noqa: F811
    tmp_path: Path,
) -> None:
    first, remake = ProducedItem.objects.filter(kind="clip").order_by("id")
    _sent(first)
    _sent(remake, left_out=[], prompt="Film this in real-time speed.")

    scene = json.loads((exported(tmp_path) / "results.json").read_text())[f"{AD}:2"]

    sent = scene["tries"][0]["sent"]
    assert [(p["image"], p["library_number"], p["label"]) for p in sent["pictures"]][:2] == [
        (1, 14, "The garlic press, closed, seen from the front."),
        (2, 4, "A hand squeezing the press over a bowl."),
    ]
    assert sent["pictures"][6]["drawn"] == "person_in_room"
    assert sent["pictures"][1]["for"] == "in-use"
    assert [(o["tier"], o["library_number"], o["label"]) for o in sent["left_out"]] == [
        (4, 5, "The garlic press, closed, seen from the top.")
    ]
    assert [(p["video"], p["moment"], p["start"], p["end"], p["trim"]) for p in sent["pieces"]] == [
        (1, 2, 6.0, 11.0, None),
        (2, 1, 3.0, 13.0, "pieces_over_limit"),
    ]
    assert [(r["of"], r["outcome"], r["made"]) for r in sent["recolours"]] == [
        (1, "done", 14),
        (2, "fell_back", None),
    ]
    assert sent["prompt"].startswith("Film this in real-time speed.\nImage 1 is")
    # Each try carries its own.
    assert scene["tries"][1]["sent"]["left_out"] == []
    assert scene["kept"]["sent"]["prompt"] == "Film this in real-time speed."


def test_the_page_shows_each_clips_sent_and_left_out_pictures_and_counts_the_capped_clips(
    remade_once: SceneStep,  # noqa: F811
    tmp_path: Path,
) -> None:
    first, remake = ProducedItem.objects.filter(kind="clip").order_by("id")
    _sent(first)
    _sent(remake, left_out=[])

    page = grading_page.page(exported(tmp_path))

    top = page.split('class="ad"')[0]
    assert "Clips with pictures left out: 1 of 2" in top
    clips = page.split('<figure class="clip">')[1:]
    assert "Image 1: #14 The garlic press, closed, seen from the front." in clips[0]
    assert "Image 7: drawn person in room" in clips[0]
    assert "#5 The garlic press, closed, seen from the top." in clips[0]
    assert "another side photo" in clips[0].split('class="left-out"')[1]
    assert "pieces over limit" in clips[0] and "fell back" in clips[0]
    assert 'class="left-out"' not in clips[1]
    assert "Image 1: #14" in clips[1]


def test_every_try_exports_with_what_it_was_sent(
    remade_once: SceneStep,  # noqa: F811
    tmp_path: Path,
) -> None:
    run = exported(tmp_path)

    scene = json.loads((run / "results.json").read_text())[f"{AD}:2"]
    # Every B-roll clip is made from its sent list now (spec #210 point 19).
    first, remake = ProducedItem.objects.filter(kind="clip").order_by("id")
    assert [t["sent"] for t in scene["tries"]] == [first.sent, remake.sent]
    assert first.sent is not None


def _bytes(clip: ProducedItem | None) -> bytes:
    from adforge import file_store

    assert clip is not None
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
        "fix_check": {"verdict": "failed"},
    },
    "8:4": {
        "tries": [_try("fail"), _try("fail", 1), _try("fail", 2)],
        "kept": {"remake": 2},
        "costs": {"total": 2.0},
        "fix_check": {"verdict": "passed"},
    },
    "9:2": {"tries": [_try("not_checked")], "kept": {"remake": None}, "costs": {"total": 0.4}},
}
TAPS = {
    "8-cleaner__scene-3": {"fixed_1": "yes", "fix_check_agree": "no"},
    "8-cleaner__scene-4": {"fixed_1": "no", "fixed_2": "partly", "fix_check_agree": "yes"},
}


def test_each_ads_library_is_exported_as_a_text_list_to_read_without_the_chat(
    remade_once: SceneStep,  # noqa: F811
    tmp_path: Path,
) -> None:
    job = Job.objects.get()
    front = job.photos.get(position=1)
    add_to_library(
        job,
        front.file,
        PictureLabel(
            kind="side",
            side="front",
            alone="yes",
            colour="sage",
            label="The mug from the front, handle on the right.",
        ),
        PagePhoto(photo=1),
    )
    add_to_library(
        job,
        "owner/clean-mug.png",
        PictureLabel(
            kind="line-up",
            side="front",
            alone="no",
            colour="black",
            target="the tallest mug",
            label="Three mugs on a shelf.",
        ),
        OwnerPhoto(original="owner/mug.png"),
    )

    run = exported(tmp_path)

    # The page's two photos, labelled as the page declares them, then the two added here.
    assert (run / AD / "library.txt").read_text() == (
        "1. The product from the front, alone on a plain background. Kind side, side front, "
        "alone yes. Colour green. From the page, product photo 1.\n"
        "2. The product from the front, alone on a plain background. Kind side, side front, "
        "alone yes. Colour green. From the page, product photo 2.\n"
        "3. The mug from the front, handle on the right. Kind side, side front, alone yes. "
        "Colour sage. From the page, product photo 1.\n"
        "4. Three mugs on a shelf. Kind line-up (the tallest mug), side front, alone no. "
        "Colour black. From the shop owner's photo owner/mug.png.\n"
    )


def test_an_ad_with_an_empty_library_exports_an_empty_list(
    remade_once: SceneStep,  # noqa: F811
    tmp_path: Path,
) -> None:
    Job.objects.get().library.all().delete()

    assert (exported(tmp_path) / AD / "library.txt").read_text() == ""
