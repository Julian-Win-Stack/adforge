"""She speaks at a natural ad pace (#407): a slow voice is planned again, every clip's pace is
measured and kept without filming it again, and the ad opens on its first word."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from pytest_django import Settings
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from jobs.models import Job, ProducedItem

from .conftest import NO_CHOICES, HeldSteps, chat, facts_ok, paid_for, recorded, results_of
from .test_h3_planning import h3_plan, script_plan
from .test_h3_scene_words import TALKING_PICTURE
from .test_producer_broll import calling, run
from .test_scene_film import FILM
from .test_scene_pictures import picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

# Scene 2's line: 14 words, a 6-second clip at 2.8 words a second.
LINE = "This handmade mug costs only $10 and keeps your tea warm all morning long."


def test_a_voice_asking_for_a_slow_pace_is_sent_back_and_planned_again(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], settings: Settings
) -> None:
    slow = h3_plan()
    slow["plan"]["person_voice"] = "A warm woman in her thirties, at an easy, unhurried pace."
    script_plan(fake_model, product_page_url, slow, h3_plan())

    say(product_page_url)

    first, again = (asked.instructions for asked in fake_model.asked if asked.purpose == "plan_ad")
    sent_back = 'The voice asks for a slow pace ("unhurried").'
    assert sent_back not in first
    assert sent_back in again
    # The planner is told the rule before it plans, in the same words.
    assert "never describe it as slow, unhurried, leisurely or taking its time" in first
    assert "unhurried" not in Job.objects.get().person_voice


@pytest.fixture
def talking_scene_2(fake_model: FakeModel, product_page_url: str, say: Callable[..., None]) -> Job:
    plan = h3_plan()
    plan["plan"]["scenes"][1].update(
        line=LINE, kind="talking", kind_why="", person_shown="has face"
    )
    script_plan(fake_model, product_page_url, plan)
    say(product_page_url)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    calling(fake_model, say, ("create_person", {}), ("run_planning_checks", NO_CHOICES))
    return Job.objects.get()


def film_scene_2(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    fake_model.respond("transcribe_line", {"text": LINE})
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", TALKING_PICTURE)]),
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the mug.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)


def test_a_slow_clip_has_its_pace_recorded_and_is_kept_without_filming_again(
    fake_model: FakeModel,
    talking_scene_2: Job,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    # Slower than the 2.8 words a second it was planned at.
    fake_model.words_per_second = 2.0

    film_scene_2(fake_model, steps, say)

    assert paid_for().count("make_broll_clip") == 1
    assert "It passes." in results_of("film")[0]
    clip = ProducedItem.objects.get(kind=ProducedItem.Kind.CLIP)
    # Kept beside the clip's verdicts, after the clip judge replaced its ruling.
    assert clip.ruling is not None
    assert clip.ruling["pace"] == {
        "words_per_second": 2.0,
        "words": 14,
        "planned_words_per_second": 2.8,
    }
    assert clip.ruling["word_check"]["verdict"] == "pass"
    (noticed,) = [
        json.loads(text) for _, text in recorded(api, session_id) if '"words_per_second"' in text
    ]
    assert noticed == {"scene": 2, "clip": clip.pk, **clip.ruling["pace"]}
    # A developer notice only: the shop owner never sees it.
    assert not any("words_per_second" in text for _, text in chat(api, session_id))


def test_the_ad_opens_on_its_first_word_not_on_the_silence_before_it(
    fake_model: FakeModel,
    talking_scene_2: Job,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    settings: Any,
) -> None:
    # H3 left 1.2 seconds of silence before her first word, as Bondi's 1.66 seconds did.
    fake_model.words_per_second = 2.8
    fake_model.pause_seconds = 1.2

    film_scene_2(fake_model, steps, say)
    calling(
        fake_model,
        say,
        ("remove_scene", {"scene": 3}),
        ("remove_scene", {"scene": 1}),
        ("create_music", {"mood": "warm and calm"}),
        ("assemble_ad", {}),
    )

    clip = ProducedItem.objects.get(kind=ProducedItem.Kind.CLIP)
    assert clip.seconds is not None
    ad = talking_scene_2.produced.get(kind=ProducedItem.Kind.FINISHED_AD)
    assert ad.cuts[0]["clip"] == clip.pk
    assert ad.cuts[0]["clip_start"] == pytest.approx(1.15, abs=0.001)
    assert ad.cuts[0]["voice_clip_start"] == pytest.approx(1.15, abs=0.001)
    assert ad.seconds == pytest.approx(clip.seconds - 1.15, abs=0.001)
    # The first word, and its caption, come within 0.3 seconds of the ad's start.
    assert 0 < ad.captions[0]["start"] <= 0.3
    assert " ".join(row["text"] for row in ad.captions) == LINE
