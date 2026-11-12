"""Outside calls wait across workers rather than fail when the account is busy."""

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise
from typing import Any

import httpx
import pytest
from django.db import connection
from pytest_django import Settings
from rest_framework.test import APIClient

from adforge import file_store
from adforge.retry import OutsideServiceDown
from agents import tasks
from gateway import gateway
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import Picture, ToolUse, Transcription
from jobs import firecrawl as firecrawl_api
from jobs.models import Job, SceneStep

from .conftest import READABLE, FakeFirecrawl, HeldSteps, chat, recorded, shampoo_picked
from .test_pictures_on_azure import script_pictures
from .test_producer_broll import calling, checked  # noqa: F401
from .test_providers import wav

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_burst_of_pictures_waits_without_failures_or_retries(
    fake_model: FakeModel, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.OPENAI_PICTURES_PER_MINUTE = 300
    starts: list[float] = []
    drawing = fake_model.draw

    def draw(**asked: Any) -> Picture:
        starts.append(time.monotonic())
        return drawing(**asked)

    monkeypatch.setattr(fake_model, "draw", draw)

    def make(_: int) -> str:
        try:
            return gateway.draw_picture(job=None, purpose="draw_person", prompt="A potter.")
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        keys = list(pool.map(make, range(3)))

    assert len(set(keys)) == 3
    assert all(b - a >= 0.18 for a, b in pairwise(sorted(starts)))
    assert list(ModelCall.objects.values_list("outcome", "attempt")) == [("succeeded", 1)] * 3


def test_three_ads_get_full_page_reads_with_at_most_two_firecrawl_calls(
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.ONE_AD_AT_A_TIME = False
    settings.FIRECRAWL_CONCURRENCY = 2
    settings.FIRECRAWL_REQUESTS_PER_MINUTE = 600
    fake_model.answer_unscripted(
        "pick_photos", lambda _: shampoo_picked({"gallery_images": [8], "more_images": []})
    )
    active = most = 0
    lock = threading.Lock()
    post = httpx.post
    starts: list[float] = []

    def slow_post(*args: Any, **kwargs: Any) -> httpx.Response:
        nonlocal active, most
        with lock:
            active += 1
            most = max(active, most)
            starts.append(time.monotonic())
        try:
            time.sleep(0.15)
            return post(*args, **kwargs)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(httpx, "post", slow_post)
    fake_model.answer_unscripted("check_page", lambda _: READABLE)
    fake_model.answer_unscripted(
        "produce",
        lambda request: (
            turn(says="Read it.")
            if any(isinstance(item, ToolUse) for item in request.handoff.conversation)
            else turn(calls=[("read_page", {"link": shampoo_page_url, "target_seconds": None})])
        ),
    )
    barrier = threading.Barrier(3)

    def start(_: int) -> str:
        try:
            api = APIClient()
            response = api.post("/api/sessions/", {}, format="json")
            session: str = response.json()["id"]
            barrier.wait(timeout=10)
            sent = api.post(
                f"/api/sessions/{session}/messages/", {"text": shampoo_page_url}, format="json"
            )
            assert sent.status_code == 201, sent.json()
            return session
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        sessions = list(pool.map(start, range(3)))

    assert most == 2
    assert len(firecrawl.requests) == 9
    # Nine calls at 600/min span eight 100 ms admission intervals. Observe the
    # whole burst, allowing one interval for dispatch after the database admission:
    # thread scheduling can bunch two HTTP starts without changing the shared pace.
    assert max(starts) - min(starts) >= 0.7
    for session in sessions:
        job = Job.objects.get(session_id=session)
        assert job.page_text_full
        assert set(job.firecrawl["files"]) >= {"page", "marked", "product"}
        assert not any(
            kind in text
            for _, text in recorded(APIClient(), session)
            for kind in ["couldn't open", "plain download"]
        )


def test_firecrawl_paces_a_burst_even_with_room_for_every_concurrent_call(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.FIRECRAWL_CONCURRENCY = 9
    settings.FIRECRAWL_REQUESTS_PER_MINUTE = 600
    starts: list[float] = []

    def answer(*args: Any, **kwargs: Any) -> httpx.Response:
        starts.append(time.monotonic())
        return httpx.Response(200, json={"success": True, "data": {"rawHtml": "A fine mug."}})

    monkeypatch.setattr(httpx, "post", answer)

    def read(_: int) -> str:
        try:
            return str(firecrawl_api.read_page("https://shop.example/mug")["rawHtml"])
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=9) as pool:
        assert list(pool.map(read, range(9))) == ["A fine mug."] * 9

    # Eight 100 ms intervals, less one interval for dispatch after admission. The
    # replies are instant and all nine slots are available, so only pacing can wait.
    assert max(starts) - min(starts) >= 0.7


def test_a_long_picture_wait_records_one_developer_notice_and_keeps_going(
    fake_model: FakeModel, settings: Settings, api: APIClient, session_id: str
) -> None:
    settings.OPENAI_PICTURES_PER_MINUTE = 300
    settings.SERVICE_LIMIT_NOTICE_SECONDS = 0.05
    job = Job.objects.create(session_id=session_id, product_url="https://shop.example/mug")
    gateway.draw_picture(job=job, purpose="draw_person", prompt="A potter.")
    gateway.draw_picture(job=job, purpose="draw_person", prompt="A potter smiling.")

    waits = [text for _, text in recorded(api, session_id) if "still waiting" in text]
    assert len(waits) == 1
    job.refresh_from_db()
    assert job.warnings[0]["kind"] == "service_limit_wait"
    assert not any("still waiting" in text for _, text in chat(api, session_id))
    assert ModelCall.objects.filter(outcome=ModelCall.Outcome.SUCCEEDED).count() == 2


def test_a_429_still_retries_and_each_picture_attempt_takes_the_pace(
    fake_model: FakeModel, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.OPENAI_PICTURES_PER_MINUTE = 300
    fake_model.respond("draw_person", OutsideServiceDown("OpenAI answered 429"))
    drawing = fake_model.draw
    starts: list[float] = []

    def draw(**asked: Any) -> Picture:
        starts.append(time.monotonic())
        return drawing(**asked)

    monkeypatch.setattr(fake_model, "draw", draw)
    assert gateway.draw_picture(job=None, purpose="draw_person", prompt="A potter.")
    assert len(starts) == 2
    assert starts[1] - starts[0] >= 0.18
    assert list(ModelCall.objects.values_list("outcome", "attempt")) == [
        ("failed", 1),
        ("succeeded", 2),
    ]


def test_transcripts_wait_for_the_configured_number_of_concurrent_calls(
    fake_model: FakeModel, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.ELEVENLABS_TRANSCRIPTION_CONCURRENCY = 2
    audio = file_store.save("line.wav", wav(1))
    fake_model.answer_unscripted("transcribe_line", lambda _: {"text": "A fine mug."})
    transcribing = fake_model.transcribe
    active = most = 0
    lock = threading.Lock()

    def hear(**asked: Any) -> Transcription:
        nonlocal active, most
        with lock:
            active += 1
            most = max(active, most)
        try:
            time.sleep(0.15)
            return transcribing(**asked)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(fake_model, "transcribe", hear)

    def make(_: int) -> str:
        try:
            return gateway.transcribe(job=None, purpose="transcribe_line", audio_key=audio).text
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        assert list(pool.map(make, range(3))) == ["A fine mug."] * 3
    assert most == 2


def test_a_dead_workers_concurrency_slot_is_returned(
    fake_model: FakeModel, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.ELEVENLABS_TRANSCRIPTION_CONCURRENCY = 1
    audio = file_store.save("line.wav", wav(1))
    fake_model.answer_unscripted("transcribe_line", lambda _: {"text": "A fine mug."})
    entered = threading.Event()
    hearing = fake_model.transcribe

    def hear(**asked: Any) -> Transcription:
        entered.set()
        return hearing(**asked)

    monkeypatch.setattr(fake_model, "transcribe", hear)
    env = {
        **os.environ,
        "SERVICE_LIMIT_TEST_DB": connection.settings_dict["NAME"],
    }
    code = """import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
import django
django.setup()
from django.conf import settings
from gateway.service_limits import slot
settings.DATABASES["default"]["NAME"] = os.environ["SERVICE_LIMIT_TEST_DB"]
settings.ELEVENLABS_TRANSCRIPTION_CONCURRENCY = 1
with slot("elevenlabs"):
    print("held", flush=True)
    import sys
    sys.stdin.read()
"""
    worker = subprocess.Popen(
        [sys.executable, "-c", code],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )

    def make() -> str:
        try:
            return gateway.transcribe(job=None, purpose="transcribe_line", audio_key=audio).text
        finally:
            connection.close()

    try:
        assert worker.stdout is not None
        assert worker.stdout.readline().strip() == "held"
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(make)
            try:
                assert not entered.wait(0.15)
            finally:
                worker.kill()
                worker.wait(timeout=10)
            assert pending.result(timeout=10) == "A fine mug."
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=10)
        if worker.stdin:
            worker.stdin.close()
        if worker.stdout:
            worker.stdout.close()


def test_a_scene_waiting_for_pictures_keeps_beating_and_is_not_failed_as_dead(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    settings: Settings,
    api: APIClient,
    session_id: str,
) -> None:
    settings.STEP_HEARTBEAT_SECONDS = 0.02
    settings.STEP_DEAD_AFTER_SECONDS = 0.3
    settings.OPENAI_PICTURES_PER_MINUTE = 60
    settings.SERVICE_LIMIT_NOTICE_SECONDS = 0.05
    gateway.draw_picture(job=Job.objects.get(), purpose="draw_person", prompt="A potter.")
    script_pictures(fake_model)
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 1, "instruction": "Draw its picture.", "quotes": ["Go on"]},
        ),
    )
    (step_id,) = steps.held
    fake_model.respond("produce", turn(says="Done."))

    def work() -> None:
        try:
            steps.run_held()
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(work)
        deadline = time.monotonic() + 10
        beats = set()
        while time.monotonic() < deadline:
            step = SceneStep.objects.get(pk=step_id)
            if step.taken_at:
                beats.add(step.seen_at)
            if len(beats) >= 3:
                break
            time.sleep(0.02)
        assert len(beats) >= 3
        assert not pending.done(), "The scene should still be waiting for its picture slot"
        tasks.fail_dead_steps()
        assert SceneStep.objects.get(pk=step_id).status == SceneStep.Status.RUNNING
        pending.result(timeout=30)
    assert any("still waiting" in text for _, text in recorded(api, session_id))
