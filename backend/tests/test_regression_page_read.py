"""The grading page shows what the page read kept beside what it dropped, and why."""

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from playwright.sync_api import sync_playwright
from pytest_httpserver import HTTPServer

from evals.regression import grading_records
from evals.regression.grading import build_page
from evals.regression.grading_records import export_job
from evals.regression.store import Store, write_json
from gateway.fake import FakeModel
from jobs.models import Job

from .conftest import READABLE, FakeFirecrawl, photo, shampoo_picked
from .test_producer_page import LATHER_PATH, SPLASH_PATH, reading
from .test_regression_grading import DB, serve_page

pytestmark = [pytest.mark.django_db(transaction=True)]

MADE_UP = "Our shampoo is made from moon dust and starlight."


def test_the_page_read_shows_what_was_kept_beside_what_was_dropped_with_the_apps_reasons(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    tmp_path: Path,
) -> None:
    httpserver.expect_request(SPLASH_PATH).respond_with_data("Not here", status=404)
    httpserver.expect_request(LATHER_PATH).respond_with_data(
        "<html>Summer sale!</html>", content_type="text/html"
    )
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)

    def copy_one_passage_and_make_one_up(request: Any) -> dict[str, Any]:
        lines = [line for line in request.handoff.page_text.splitlines() if len(line) > 40]
        return {"product": "Detox Shampoo", "passages": [lines[0], MADE_UP]}

    fake_model.answer_unscripted("copy_page_text", copy_one_passage_and_make_one_up)
    fake_model.respond(
        "pick_photos", shampoo_picked({"gallery_images": [8, 10], "more_images": [15]})
    )

    say(f"Make an ad for {shampoo_page_url}")

    job = Job.objects.get()
    store = Store(tmp_path / "store")
    store.write_run("new", {"name": "new", "scenarios": ["shampoo"], "to": "plans"})
    base = store.export_dir("new", "shampoo") / "shampoo"
    meta = export_job(
        job,
        base,
        scenario={"key": "shampoo", "kind": "picture", "link": job.product_url, "situation": ""},
    )
    shutil.copytree(settings.MEDIA_ROOT, store.media_dir("new", "shampoo"))
    read = meta["page_read"]

    assert read["text"]["kept"] == job.page_text
    reasons = {line["text"]: line["why"] for line in read["text"]["dropped"]}
    assert reasons[MADE_UP] == grading_records.NOT_ON_PAGE
    assert grading_records.NOT_COPIED in reasons.values()
    assert not any(
        line in job.page_text for line in reasons if reasons[line] != grading_records.NOT_ON_PAGE
    )
    by_number = {photo["n"]: photo for photo in read["photos"]}
    assert by_number[8]["kept"] is True
    assert by_number[10]["kept"] is False
    assert "answered 404" in by_number[10]["why"]
    assert "text/html" in by_number[15]["why"]
    assert grading_records.NOT_PICKED in {p["why"] for p in read["photos"]}
    assert any("couldn't be kept" in note for note in read["notes"])

    # The saved page holds its pictures; the dropped ones are shown from there.
    splash = next(link for link in by_number[10]["urls"] if SPLASH_PATH in link)
    page = store.page_dir("shampoo")
    (page / "snapshots").mkdir(parents=True)
    (page / "snapshots/splash.png").write_bytes(photo(10, 600, 700))
    write_json(page / "manifest.json", {"downloads": {splash: {"file": "snapshots/splash.png"}}})
    store.write_answers("new", "shampoo", {"answers": {}, "cost_usd": 0})

    path = build_page(store, "new", output=tmp_path / "page")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        tab = browser.new_page(viewport={"width": 1400, "height": 1000})
        tab.set_default_timeout(5000)
        errors: list[str] = []
        tab.on("pageerror", lambda error: errors.append(str(error)))
        tab.add_init_script(DB)
        tab.goto(serve_page(path, httpserver) + "#shampoo")
        kept = tab.locator('[data-side="kept"]')
        dropped = tab.locator('[data-side="dropped"]')
        assert kept.locator("pre").inner_text().strip() == job.page_text.strip()
        assert kept.locator("img").count() == 1
        assert dropped.get_by_text(MADE_UP, exact=True).count() == 1
        assert dropped.get_by_text(grading_records.NOT_ON_PAGE + " (1)", exact=True).count() == 1
        assert dropped.get_by_text(by_number[10]["why"] + " (1)", exact=True).count() == 1
        # Kept on the left, dropped on the right.
        assert kept.bounding_box()["x"] < dropped.bounding_box()["x"]  # type: ignore[index]
        # The splash photo was saved with the page, so it is shown; the lather one wasn't.
        assert dropped.locator("img").count() == 1
        assert dropped.get_by_text("Not saved with the page").count() >= 1
        if proof := os.environ.get("REGRESSION_GRADING_PROOF"):
            tab.locator("#page-read").screenshot(path=Path(proof) / "page-read.png")
        assert errors == []
        browser.close()
