"""The regression tap page, exercised in Chromium against a fixture lasting store."""

import json
import mimetypes
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from playwright.sync_api import sync_playwright
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from evals.regression.store import Store, write_json
from gateway.fake import FakeModel

from .conftest import picture
from .test_producer_broll import checked  # noqa: F401


def fixture_store(tmp_path: Path) -> Store:
    store = Store(tmp_path)
    store.write_run("new", {"name": "new", "scenarios": ["mug", "chair"], "to": "full"})
    store.write_baselines({"mug": "old"})
    fake = FakeModel()
    current_video = fake.download(url="https://fake.fal/video-1.mp4")
    old_video = fake.download(url="https://fake.fal/video-2.mp4")
    for run, scenario, full in [
        ("old", "mug", True),
        ("new", "mug", True),
        ("new", "chair", False),
    ]:
        base = store.export_dir(run, scenario) / scenario
        scene = base / "scene-1"
        scene.mkdir(parents=True)
        (scene / "start-picture.png").write_bytes(picture(250, 300, (90, 130, 110)))
        (base / "page.txt").write_text("Only this product's facts.")
        if full:
            clip = old_video if run == "old" else current_video
            (base / "ad.mp4").write_bytes(clip)
            (scene / "first.mp4").write_bytes(old_video)
            (scene / "remake-1.mp4").write_bytes(clip)
            (base / "note-1").mkdir()
            (base / "note-1/before.mp4").write_bytes(old_video)
            (base / "note-1/after.mp4").write_bytes(current_video)
        meta: dict[str, Any] = {
            "scenario": {
                "key": scenario,
                "kind": "full" if full else "picture",
                "link": "https://shop.test/product",
                "situation": "A demo",
            },
            "scenes": [
                {
                    "number": 1,
                    "line": "Meet our product.",
                    "shows": "The product on the table.",
                    "picture": "scene-1/start-picture.png",
                    **({"clip": "scene-1/remake-1.mp4"} if full else {}),
                }
            ],
            "notes": [
                {
                    "number": 1,
                    "text": "Make the table lighter.",
                    "before": "note-1/before.mp4",
                    "after": "note-1/after.mp4",
                }
            ]
            if full
            else [],
            "page_files": ["page.txt"],
        }
        write_json(base / "regression.json", meta)
        if full:
            write_json(
                base.parent / "results.json",
                {
                    f"{scenario}:1": {
                        "scene": 1,
                        "line": "Meet our product.",
                        "shows": "The product on the table.",
                        "kind": "broll",
                        "tries": [
                            {"pk": 11, "remake": None, "ruling": {"verdict": "unsure"}},
                            {"pk": 12, "remake": 1, "ruling": {"verdict": "pass"}},
                        ],
                        "kept": {"pk": 12, "remake": 1},
                    }
                },
            )
        media = store.media_dir(run, scenario)
        media.mkdir(parents=True)
        (media / "page-photo.png").write_bytes(picture(160, 180, (130, 90, 80)))
        store.write_answers(
            run,
            scenario,
            {
                "scenario": scenario,
                "run": run,
                "answers": {
                    "ad_no_dead_air": {
                        "verdict": "pass" if run == "old" else "fail",
                        "reason": "1.4 s silence",
                        "items": [],
                    },
                    "page_photos_this_product": {
                        "verdict": "needs_label",
                        "reason": "Needs label",
                        "items": [
                            {
                                "item": "photo-" + "a" * 64,
                                "verdict": "needs_label",
                                "reason": "Needs label",
                                "file": "page-photo.png",
                            }
                        ],
                    },
                },
                "first_try": {"scenes": 1, "first_passed": 0, "clips": 2},
                "cost_usd": 3.5,
            },
        )
    return store


DB = """
window.savedDocs = {};
window.claude = {use: async () => ({
  doc: id => ({set: async doc => { window.savedDocs[id] = doc; }}),
  collection: () => ({onSnapshot: callback => callback({docs: []})})
})};
"""


def serve_page(path: Path, server: HTTPServer) -> str:
    def get(request: Request) -> Response:
        file = path.parent / request.path.removeprefix("/grading/")
        return Response(file.read_bytes(), content_type=mimetypes.guess_type(file)[0])

    server.expect_request(re.compile("/grading/.*")).respond_with_handler(get)
    return server.url_for("/grading/" + path.name)


def test_old_new_page_keeps_all_takes_and_saves_the_regression_taps(
    tmp_path: Path, httpserver: HTTPServer
) -> None:
    from evals.regression.grading import build_page

    store = fixture_store(tmp_path)
    path = build_page(store, "new")
    assert json.loads((path.parent / "need.json").read_text())["assets"]
    assert json.loads((path.parent / "abatches.json").read_text())
    assert json.loads((path.parent / "publish.json").read_text())["capabilities"] == {
        "db": {},
        "assets": {},
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("REGRESSION_PROOF_BROWSER")
        )
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.set_default_timeout(5000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(DB)
        page.goto(serve_page(path, httpserver) + "#mug")
        assert page.locator("[data-comparison] video").count() == 2
        assert page.locator(".chip:visible").filter(has_text="judge: unsure").count() == 1
        assert (
            page.get_by_text("new: ad_no_dead_air fails — 1.4 s silence", exact=True).count() == 1
        )
        for suffix in [
            "ad",
            "page",
            "plan",
            "s1__picture",
            "s1__clip",
            "note1",
            "no-worse",
            "photo-" + "a" * 64,
        ]:
            assert page.locator('[id="t-mug__' + suffix + '"]').count() == 1
        if proof := os.environ.get("REGRESSION_GRADING_PROOF"):
            directory = Path(proof)
            directory.mkdir(parents=True, exist_ok=True)
            page.evaluate(
                "document.querySelectorAll('[data-comparison] video').forEach(v=>"
                "{v.preload='auto';v.load()})"
            )
            page.wait_for_function(
                "[...document.querySelectorAll('[data-comparison] video')]"
                ".every(v=>v.readyState===4)"
            )
            page.locator("[data-comparison]").screenshot(path=directory / "full-scenario.png")
        note = page.locator('[id="t-mug__s1__clip"]')
        note.fill("The cap floats above the bottle.")
        note.locator("..").get_by_role("button", name="Fail", exact=True).click()
        page.wait_for_function(
            "window.savedDocs['grades/mug__s1__clip']?.note === 'The cap floats above the bottle.'"
        )
        saved = page.evaluate("window.savedDocs['grades/mug__s1__clip']")
        assert saved == {
            "grade": "fail",
            "note": "The cap floats above the bottle.",
            "at": saved["at"],
        }
        assert saved["at"].endswith("Z")
        assert page.get_by_text("Old scenes and records", exact=False).count() == 1
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("combobox", name="Choose an ad").select_option("chair")
        assert page.locator("[data-comparison] img").count() == 1
        assert page.locator("[data-comparison] video").count() == 0
        assert page.locator('[id="t-chair__s1__picture"]').count() == 1
        assert page.locator('[id="t-chair__s1__clip"]').count() == 0
        # A first run has no old one, so there is no "no worse" call to make.
        assert page.locator('[id="t-chair__no-worse"]').count() == 0
        assert page.get_by_text("The new one is no worse").count() == 0
        # No Claude-written reminder lists, and the plan's fail tap names what to look for.
        assert page.get_by_text("Questions to grade").count() == 0
        assert (
            page.locator('[id="t-chair__plan"]')
            .locator("..")
            .get_by_role("button", name="Product used the wrong way, or makes no sense")
            .count()
            == 1
        )
        # Pictures are small; a tap opens the full-size file.
        picture = page.locator("[data-comparison] img")
        assert picture.bounding_box()["height"] <= 200  # type: ignore[index]
        picture.click()
        full = page.locator(".lb img")
        assert full.get_attribute("src") == picture.get_attribute("src")
        full.click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        if proof:
            page.set_viewport_size({"width": 1400, "height": 1000})
            page.screenshot(path=Path(proof) / "picture-scenario.png")
        assert errors == []
        browser.close()


def test_an_unsaved_grade_stays_in_the_local_draft_with_a_visible_warning(
    tmp_path: Path, httpserver: HTTPServer
) -> None:
    from evals.regression.grading import build_page

    path = build_page(fixture_store(tmp_path), "new")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(5000)
        page.add_init_script(
            DB.replace("window.savedDocs[id] = doc;", "throw new Error('offline');")
        )
        page.goto(serve_page(path, httpserver) + "#mug")
        note = page.locator('[id="t-mug__plan"]')
        note.fill("The cap is wrong.")
        note.blur()
        page.get_by_text(
            "Couldn't save (offline). Kept on this device, trying again.", exact=True
        ).wait_for()
        draft = page.evaluate("JSON.parse(localStorage.getItem('regression-new-drafts'))")
        assert draft["mug__plan"]["note"] == "The cap is wrong."
        page.reload()
        assert page.locator('[id="t-mug__plan"]').input_value() == "The cap is wrong."
        browser.close()


def test_every_picture_version_can_be_graded_and_changed_code_details_are_visible(
    tmp_path: Path, httpserver: HTTPServer
) -> None:
    from evals.regression.grading import build_page

    store = fixture_store(tmp_path)
    media = store.media_dir("new", "mug")
    for file in ("first-picture.png", "kept-picture.png", "end-picture.png"):
        (media / file).write_bytes(picture(150, 190, (80, 120, 140)))
    write_json(
        store.export_dir("new", "mug") / "mug" / "grading-records.json",
        {
            "scenes": [
                {
                    "number": 1,
                    "current_picture": 102,
                    "pictures": [
                        {
                            "pk": 101,
                            "kind": "starting_picture",
                            "version": 1,
                            "file": "first-picture.png",
                        },
                        {
                            "pk": 102,
                            "kind": "starting_picture",
                            "version": 2,
                            "file": "kept-picture.png",
                        },
                        {"pk": 103, "kind": "end_picture", "version": 1, "file": "end-picture.png"},
                    ],
                }
            ]
        },
    )
    for run, gap, scene in (("old", "1.4", "s1"), ("new", "2.4", "s2")):
        answers = store.read_answers(run, "mug")
        answers["answers"]["ad_no_dead_air"] = {
            "verdict": "fail",
            "reason": f"{gap} s silence",
            "items": [],
        }
        answers["answers"]["clip_words_match"] = {
            "verdict": "fail",
            "reason": "One scene drops words.",
            "items": [{"item": scene, "verdict": "fail", "reason": "missing last word"}],
        }
        if run == "new":
            shutil.copyfile(media / "page-photo.png", media / "same-photo-new-key.png")
            answers["answers"]["page_photos_this_product"]["items"][0]["file"] = (
                "same-photo-new-key.png"
            )
        store.write_answers(run, "mug", answers)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(5000)
        page.add_init_script(DB)
        page.goto(serve_page(build_page(store, "new"), httpserver) + "#mug")
        assert (
            page.get_by_text("new: ad_no_dead_air fails — 2.4 s silence", exact=True).count() == 1
        )
        assert (
            page.get_by_text(
                "new: clip_words_match fails — One scene drops words.; "
                "s2 fails — missing last word",
                exact=True,
            ).count()
            == 1
        )
        assert page.get_by_text(re.compile("^new: page_photos_this_product")).count() == 0
        page.get_by_text("Other starting and end pictures, with their records", exact=True).click()
        for suffix, words in (
            ("take3", "The first picture's cap floats."),
            ("take4", "The end picture is impossible."),
        ):
            textarea = page.locator(f'[id="t-mug__s1__{suffix}"]')
            textarea.fill(words)
            textarea.locator("..").get_by_role("button", name="Fail", exact=True).click()
            page.wait_for_function(
                "([id,note]) => window.savedDocs['grades/'+id]?.note === note",
                arg=[f"mug__s1__{suffix}", words],
            )
        assert page.locator('[id="t-mug__s1__picture"]').count() == 1
        assert page.locator('[id="t-mug__s1__take1"]').count() == 1
        assert page.locator('[id="t-mug__s1__clip"]').count() == 1
        assert page.evaluate(
            "(()=>{const ids=[...document.querySelectorAll('textarea')].map(t=>t.id);"
            "return ids.length===new Set(ids).size})()"
        )
        browser.close()


def test_exported_taps_keep_julians_words_and_add_photo_labels(
    tmp_path: Path, httpserver: HTTPServer
) -> None:
    from evals.regression.grading import build_page, main

    store = fixture_store(tmp_path)
    source = tmp_path / "collection.json"
    photo = "a" * 64
    docs = {
        "mug__plan": {
            "grade": "fail",
            "note": "The wrong product's facts.",
            "at": "2026-11-12T00:00:00Z",
        },
        "mug__photo-" + photo: {"grade": "pass", "note": "", "at": "2026-11-12T00:00:01Z"},
        "chair__photo-" + photo: {
            "grade": "fail",
            "note": "A different chair.",
            "at": "2026-11-12T00:00:02Z",
        },
    }
    source.write_text(json.dumps({key: {"data": doc} for key, doc in docs.items()}))
    before = store.read_answers("new", "mug")

    assert main(["new", "--import-grades", str(source)], store=store) == 0

    assert store.read_grades("new") == docs
    assert store.read_photo_labels() == {"mug": {photo: "this"}, "chair": {photo: "not"}}
    assert "The wrong product's facts." in store.read_grades_markdown("new")
    assert "A different chair." in store.read_grades_markdown("new")
    assert store.read_answers("new", "mug") == before
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(serve_page(build_page(store, "new"), httpserver) + "#mug")
        assert page.get_by_text("Page photos needing a label", exact=True).count() == 0
        assert page.locator('[id="t-mug__plan"]').input_value() == "The wrong product's facts."
        browser.close()


@pytest.mark.django_db(transaction=True)
def test_a_chat_export_keeps_every_plan_scene_and_its_own_model_inputs(
    checked: None,  # noqa: F811
    fake_model: Any,
    steps: Any,
    say: Callable[..., None],
    tmp_path: Path,
    httpserver: HTTPServer,
) -> None:
    from evals.regression.grading import build_page
    from evals.regression.grading_records import export_job
    from jobs.models import Job

    from .scene_script import make_scene_clip

    make_scene_clip(fake_model, steps, say)
    job = Job.objects.get()
    store = Store(tmp_path / "store")
    store.write_run("new", {"name": "new", "scenarios": ["mug"], "to": "pictures"})
    base = store.export_dir("new", "mug") / "mug"
    export_job(
        job,
        base,
        scenario={"key": "mug", "kind": "picture", "link": job.product_url, "situation": "A demo"},
    )
    shutil.copytree(settings.MEDIA_ROOT, store.media_dir("new", "mug"))
    meta = json.loads((base / "regression.json").read_text())
    rich = json.loads((base / "grading-records.json").read_text())

    assert [scene["number"] for scene in meta["scenes"]] == [1, 2, 3]
    assert (base / meta["scenes"][1]["picture"]).is_file()
    assert (base / meta["scenes"][1]["clip"]).is_file()
    assert (base / meta["page_files"][0]).read_text().startswith("Stoneware Mug")
    scene = rich["scenes"][1]
    assert scene["pictures"][0]["image"]["pictures"]
    assert scene["clips"][0]["make"]["handoff"]["starting_picture"] == scene["pictures"][0]["file"]
    page = build_page(store, "new")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        tab = browser.new_page()
        tab.goto(serve_page(page, httpserver) + "#mug")
        assert tab.get_by_text("Sent to the video model (H3)", exact=True).count() == 1
        assert tab.get_by_text("The image prompt", exact=True).count() >= 1
        assert tab.get_by_text("Scene 3", exact=True).count() >= 1
        browser.close()
