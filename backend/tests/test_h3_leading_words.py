"""Leading false starts are cut once, keeping the clip, transcript and ad together."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from adforge import file_store
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import Job, ProducedItem
from jobs.planning import END_CARD_SECONDS

from .conftest import (
    NO_CHOICES,
    HeldSteps,
    chat,
    facts_ok,
    handoffs,
    paid_for,
    recorded,
    results_of,
    served,
    video,
)
from .test_h3_planning import script_plan
from .test_h3_scene_words import LINE, TALKING_PICTURE
from .test_producer_broll import calling, run
from .test_producer_broll_line_length import with_line
from .test_scene_film import FILM
from .test_scene_pictures import picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def trim_ready(fake_model: FakeModel, product_page_url: str, say: Callable[..., None]) -> Job:
    plan = with_line("talking", LINE)
    plan["plan"]["scenes"][1].update(person_shown="has face")
    script_plan(fake_model, product_page_url, plan)
    say(product_page_url)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    calling(fake_model, say, ("create_person", {}), ("run_planning_checks", NO_CHOICES))
    return Job.objects.get()


@pytest.mark.parametrize("changed_price", [False, True])
def test_leading_junk_is_cut_before_the_remaining_words_are_checked(
    fake_model: FakeModel,
    trim_ready: Job,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    changed_price: bool,
    settings: Any,
) -> None:
    fake_model.words_per_second = 3
    heard = LINE.replace("$10", "$20") if changed_price else LINE
    fake_model.respond("transcribe_line", {"text": "Your tour. " + heard})
    fake_model.respond(
        "check_words",
        {"verdict": "fail", "reason": "The price changed." if changed_price else "Extra words."},
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", TALKING_PICTURE)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "The changed price cannot be used."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the mug.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)

    assert paid_for().count("make_broll_clip") == 1
    clip = ProducedItem.objects.get(kind=ProducedItem.Kind.CLIP)
    transcript = ProducedItem.objects.get(kind=ProducedItem.Kind.TRANSCRIPT)
    raw = ModelCall.objects.get(purpose="collect_broll_clip").output
    assert raw is not None
    removed = 2 / 3 - 0.05
    assert video(file_store.read(clip.file))[2] == pytest.approx(
        video(file_store.read(raw["file"]))[2] - removed, abs=0.1
    )
    assert clip.seconds == video(file_store.read(clip.file))[2]
    assert transcript.text == heard
    assert transcript.words[0]["start"] == pytest.approx(0.05, abs=0.001)
    card = next(
        row for row in api.get(f"/api/sessions/{session_id}/scenes/").json() if row["scene"] == 2
    )
    assert served(card["versions"][-1]["url"], settings) == file_store.read(clip.file)
    records = [
        json.loads(text)
        for _, text in recorded(api, session_id)
        if text.startswith('{"seconds_removed"')
    ]
    assert len(records) == 1
    assert records[0]["seconds_removed"] == pytest.approx(removed, abs=0.001)
    assert records[0]["words_removed"] == "Your tour."
    assert not any("Your tour" in text for role, text in chat(api, session_id) if role == "notice")
    if changed_price:
        assert "It fails." in results_of("film")[0]
        assert handoffs("check_words")[-1]["said"] == heard
        return
    assert "It passes." in results_of("film")[0]
    calling(
        fake_model,
        say,
        # Scene 1 goes first: the plan keeps its B-roll scene while it has two scenes.
        ("remove_scene", {"scene": 1}),
        ("remove_scene", {"scene": 2}),
        ("create_music", {"mood": "warm and calm"}),
        ("assemble_ad", {}),
    )
    ad = trim_ready.produced.get(kind=ProducedItem.Kind.FINISHED_AD)
    assert ad.seconds == clip.seconds + END_CARD_SECONDS
    assert ad.cuts[0]["clip"] == clip.pk
    assert ad.cuts[0]["clip_start"] == 0
    assert " ".join(row["text"] for row in ad.captions) == LINE
    assert ad.captions[0]["start"] == pytest.approx(0.05, abs=0.001)


@pytest.mark.parametrize("unavailable", ["times", "cut"])
def test_a_cut_that_cannot_be_made_is_recorded_and_the_original_words_are_checked(
    fake_model: FakeModel,
    trim_ready: Job,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
    unavailable: str,
) -> None:
    import subprocess
    from dataclasses import replace

    from gateway.types import Transcription

    fake_model.words_per_second = 3
    heard = "Your tour. " + LINE
    fake_model.respond("transcribe_line", {"text": heard})
    if unavailable == "times":
        transcribe = fake_model.transcribe

        def without_times(*, model: str, audio: bytes) -> Transcription:
            heard = transcribe(model=model, audio=audio)
            return replace(
                heard, words=tuple(replace(word, start=0.0, end=0.0) for word in heard.words)
            )

        monkeypatch.setattr(fake_model, "transcribe", without_times)
    else:
        run_command = subprocess.run

        def cut_fails(arguments: list[str], *args: Any, **kwargs: Any) -> Any:
            if "-ss" in arguments and arguments[-1].endswith("/piece.mp4"):
                return subprocess.CompletedProcess(arguments, 1, stdout=b"", stderr=b"cut failed")
            return run_command(arguments, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", cut_fails)
    fake_model.respond("check_words", {"verdict": "fail", "reason": "The extra words remain."})
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", TALKING_PICTURE)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "The words still do not match."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the mug.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)

    clip = ProducedItem.objects.get(kind=ProducedItem.Kind.CLIP)
    raw = ModelCall.objects.get(purpose="collect_broll_clip").output
    assert raw is not None
    assert clip.file == raw["file"]
    assert handoffs("check_words")[-1]["said"] == heard
    assert "It fails." in results_of("film")[0]
    warnings = [
        text for _, text in recorded(api, session_id) if "leading words were not cut" in text
    ]
    assert len(warnings) == 1
    assert ("word times" if unavailable == "times" else "cut failed") in warnings[0]
    assert not any("leading words were not cut" in text for _, text in chat(api, session_id))
