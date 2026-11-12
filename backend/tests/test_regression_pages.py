"""The fixed scenario inputs, saved once and replayed through the real chat."""

from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
import yt_dlp
from pytest_django import Settings
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from adforge import file_store
from evals.regression.pages import SavedPageSource, save_page
from evals.regression.scenarios import Scenario, load_scenarios
from evals.regression.store import Store
from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs.models import Job
from jobs.page_source import use_page_source

from .conftest import (
    PUBLIC_ADDRESS,
    READABLE,
    SHAMPOO_PHOTO,
    FakeDns,
    FakeFirecrawl,
    a_video,
    shampoo_picked,
)
from .test_producer_page import reading
from .test_product_videos import FakeYtDlp


def test_the_same_thirty_scenarios_cover_fifteen_full_and_fifteen_picture_runs() -> None:
    scenarios = load_scenarios()
    assert len(scenarios) == 30
    assert len({scenario.key for scenario in scenarios}) == 30
    assert Counter(scenario.kind for scenario in scenarios) == {"full": 15, "picture": 15}
    assert [scenario.key for scenario in scenarios if scenario.owner_words] == ["logitech-pebble"]
    assert all(scenario.situation and scenario.graded_round for scenario in scenarios)
    assert all(scenario.owner_notes == [] for scenario in scenarios)
    pebble = next(scenario for scenario in scenarios if scenario.key == "logitech-pebble")
    assert pebble.first_message == (
        "https://www.logitech.com/en-us/shop/p/pebble-2-m350s-wireless-mouse.910-006985\n"
        "Leave the price out of the ad."
    )
    # Round 15's product list names the page it was graded on.
    brooklinen = next(
        scenario for scenario in scenarios if scenario.key == "brooklinen-luxe-sateen"
    )
    assert brooklinen.link == "https://www.brooklinen.com/products/luxe-core-sheet-set"
    assert all(scenario.link for scenario in scenarios)


def a_scenario(link: str) -> Scenario:
    return replace(load_scenarios()[0], link=link)


def start_chat(api: APIClient, fake_model: FakeModel, link: str) -> str:
    reading(fake_model, link)
    fake_model.respond("check_page", READABLE)
    made = api.post("/api/sessions/", {}, format="json")
    assert made.status_code == 201
    session_id: str = made.json()["id"]
    sent = api.post(f"/api/sessions/{session_id}/messages/", {"text": link}, format="json")
    assert sent.status_code == 201
    return session_id


def no_network(*args: Any, **kwargs: Any) -> Any:
    pytest.fail("A saved product page must not touch the network")


@pytest.mark.django_db(transaction=True)
def test_save_pages_then_a_link_only_chat_reads_the_same_text_and_photos_offline(
    tmp_path: Path,
    shampoo_page_url: str,
    firecrawl: FakeFirecrawl,
    fake_model: FakeModel,
    api: APIClient,
    monkeypatch: pytest.MonkeyPatch,
    dns: FakeDns,
    settings: Settings,
) -> None:
    dns.records["localhost"] = PUBLIC_ADDRESS
    store = Store(tmp_path / "store")
    scenario = a_scenario(shampoo_page_url)
    assert save_page(store, scenario)
    assert ModelCall.objects.count() == 0  # Saving never calls a model.
    assert len(firecrawl.requests) == 3

    live = start_chat(api, fake_model, shampoo_page_url)
    live_job = Job.objects.get(session_id=live)
    assert live_job.page_text
    live_photos = [file_store.read(photo.file) for photo in live_job.photos.order_by("position")]
    requests_before = len(firecrawl.requests)
    dns.lookups.clear()
    monkeypatch.setattr(httpx, "stream", no_network)
    monkeypatch.setattr(httpx, "post", no_network)
    settings.FIRECRAWL_API_KEY = ""  # Replay needs no Firecrawl account.
    with use_page_source(SavedPageSource(store.page_dir(scenario.key))):
        saved = start_chat(api, fake_model, shampoo_page_url)

    saved_job = Job.objects.get(session_id=saved)
    assert saved_job.page_text == live_job.page_text
    assert saved_job.page_text_full == live_job.page_text_full
    assert file_store.read(saved_job.page_html_key) == file_store.read(live_job.page_html_key)
    assert [
        file_store.read(photo.file) for photo in saved_job.photos.order_by("position")
    ] == live_photos
    assert len(firecrawl.requests) == requests_before
    assert dns.lookups == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", ["video", "iframe"])
def test_page_video_and_poster_are_replayed_from_saved_files(
    kind: str,
    tmp_path: Path,
    shampoo_page_url: str,
    firecrawl: FakeFirecrawl,
    fake_model: FakeModel,
    api: APIClient,
    httpserver: HTTPServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from jobs import firecrawl as page_firecrawl

    content = a_video(72, 128, sound=True)
    src = httpserver.url_for("/gallery.mp4")
    poster = httpserver.url_for("/poster.jpg")
    httpserver.expect_request("/gallery.mp4").respond_with_data(content, content_type="video/mp4")
    httpserver.expect_request("/poster.jpg").respond_with_data(
        SHAMPOO_PHOTO, content_type="image/jpeg"
    )
    marks = page_firecrawl.marks_in(firecrawl.marked["data"])
    marks["vids"] = [{"n": 1, "src": src, "poster": poster, "kind": kind, "shown": True, "box": 1}]
    firecrawl.marked["data"]["actions"]["javascriptReturns"] = [{"value": json.dumps(marks)}]
    fake_model.answer_unscripted("pick_photos", lambda _: shampoo_picked({"gallery_videos": [1]}))
    store = Store(tmp_path / "store")
    scenario = a_scenario(shampoo_page_url)
    downloader = FakeYtDlp({src: content})
    if kind == "iframe":
        monkeypatch.setattr(yt_dlp, "YoutubeDL", downloader)
    assert save_page(store, scenario)
    assert ModelCall.objects.count() == 0
    assert len(downloader.asked) == int(kind == "iframe")

    monkeypatch.setattr(httpx, "stream", no_network)
    monkeypatch.setattr(httpx, "post", no_network)
    monkeypatch.setattr(yt_dlp, "YoutubeDL", no_network)
    with use_page_source(SavedPageSource(store.page_dir(scenario.key))):
        session_id = start_chat(api, fake_model, shampoo_page_url)

    job = Job.objects.get(session_id=session_id)
    kept = job.videos.get()
    assert file_store.read(kept.file) == content
    assert kept.duration == 1
    assert kept.first_frame


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("missing", ["page", "photo"])
def test_a_missing_saved_input_fails_clearly_without_a_live_fallback(
    missing: str,
    tmp_path: Path,
    shampoo_page_url: str,
    firecrawl: FakeFirecrawl,
    fake_model: FakeModel,
    api: APIClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "store")
    scenario = a_scenario(shampoo_page_url)
    assert save_page(store, scenario)
    folder = store.page_dir(scenario.key)
    if missing == "page":
        next(folder.rglob("page.json")).unlink()
    else:
        for file in folder.rglob("*.jpg"):
            file.unlink()
        for file in folder.rglob("*.jpe"):
            file.unlink()

    monkeypatch.setattr(httpx, "stream", no_network)
    monkeypatch.setattr(httpx, "post", no_network)
    with use_page_source(SavedPageSource(folder)):
        session_id = start_chat(api, fake_model, shampoo_page_url)

    job = Job.objects.get(session_id=session_id)
    assert job.page_html_key == ""
    warning = next(warning for warning in job.warnings if warning["kind"] == "saved_page_missing")
    assert "missing" in warning["text"]
    assert str(folder) in warning["text"]
    assert len(firecrawl.requests) == 3


@pytest.mark.django_db
def test_save_pages_skips_a_saved_page_and_refresh_replaces_only_the_manifest(
    tmp_path: Path,
    shampoo_page_url: str,
    firecrawl: FakeFirecrawl,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "store")
    scenario = a_scenario(shampoo_page_url)
    assert save_page(store, scenario)
    old_file = next(store.page_dir(scenario.key).rglob("page.html"))
    old_html = old_file.read_bytes()
    with monkeypatch.context() as offline:
        offline.setattr(httpx, "stream", no_network)
        offline.setattr(httpx, "post", no_network)
        assert not save_page(store, scenario)
    assert len(firecrawl.requests) == 3

    firecrawl.page_read["data"]["rawHtml"] += "\n<p>New page text</p>"
    assert save_page(store, scenario, refresh=True)
    assert len(firecrawl.requests) == 6
    assert old_file.read_bytes() == old_html
    frozen = SavedPageSource(store.page_dir(scenario.key))
    assert frozen.read_page(shampoo_page_url)["rawHtml"].endswith("<p>New page text</p>")


@pytest.mark.django_db
def test_save_pages_command_reports_a_dead_link_and_saves_nothing(
    tmp_path: Path,
    firecrawl: FakeFirecrawl,
    dns: FakeDns,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from evals.regression.__main__ import main

    dns.records["ridge.com"] = PUBLIC_ADDRESS
    firecrawl.page_read["data"]["metadata"]["statusCode"] = 404
    store = Store(tmp_path / "store")
    assert main(["save-pages", "--store", str(store.root), "--only", "ridge-wallet"]) == 1
    assert "Dead link: ridge-wallet" in capsys.readouterr().out
    assert not store.page_dir("ridge-wallet").exists()
    assert len(firecrawl.requests) == 1


def test_save_pages_reports_missing_configuration_before_reading_a_link(
    tmp_path: Path,
    dns: FakeDns,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from evals.regression.__main__ import main

    assert main(["save-pages", "--store", str(tmp_path), "--only", "ridge-wallet"]) == 1
    assert "FIRECRAWL_API_KEY" in capsys.readouterr().out
    assert dns.lookups == []
