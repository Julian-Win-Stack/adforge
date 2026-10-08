"""A B-roll scene's line must take 4 to 14 seconds to say, as its clip lasts 5 to 15 whole
seconds; a talking scene's line may take up to 18. Driven through the chat with the models
faked: a B-roll line out of range is lengthened or shortened and checked again, and the
shop owner is never asked about it. The fake voice speaks 2 words a second."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from jobs.checks import fewest_words_in_a_broll_line, most_words_in_a_broll_line
from jobs.models import Job, Scene

from .conftest import (
    FACTS_OK,
    NO_CHOICES,
    broll,
    broll_labels,
    chat,
    facts_ok,
    handoffs,
    lines,
    paid_for,
    plan_with,
    results_of,
)
from .test_producer_checks import checking, failing

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)

POUR = "tea poured from a teapot into the mug"
OPENING = "Meet the Stoneware Mug from Kiln & Co."
PRICE = "Yours for $24.00."
# 4 words: 2 seconds, under the 4 a B-roll line must take.
SHORT = "Hand-thrown, holds 350 ml."
# 8 words: exactly the 4 seconds a B-roll line must take at least.
FITS = "Hand-thrown, holds 350 ml, and dishwasher safe too."
# 32 words: 16 seconds, over the 14 a B-roll line may take, within a talking line's 18.
LONG = " ".join(["Hand-thrown and dishwasher safe, it holds 350 ml."] * 4)
# 28 words: exactly the 14 seconds a B-roll line may take.
LONGEST = " ".join(["Hand-thrown and dishwasher safe, it holds 350 ml."] * 3) + " " + SHORT
# 40 words: 20 seconds, over even a talking line's 18.
TOO_LONG_TO_TALK = " ".join(["Hand-thrown and dishwasher safe, it holds 350 ml."] * 5)
SWITCHED = (
    "Scene 2 couldn't be made as a product shot because its line is too long for a clip, so "
    "it will be said to camera instead."
)
PASSED = "The checks passed. Every line matches the product page. The ad is ready to render."


def scene_2_saying(line: str, *, shows: str | None = POUR) -> dict[str, Any]:
    """The mug plan with scene 2 saying `line` over what it `shows`: the tea poured unless
    given, so it is a B-roll scene."""
    plan = plan_with(OPENING, line, PRICE)
    plan["plan"]["scenes"][1] = broll({**plan["plan"]["scenes"][1], "shows": shows})
    return plan


def scene_2() -> Scene:
    return Job.objects.get().scenes.get(number=2)


def rechecked() -> list[list[dict[str, Any]]]:
    """The lines handed to each fact check after the first, oldest first."""
    return [handoff["lines"] for handoff in handoffs("fact_check")[1:]]


def checked_line(line: str) -> dict[str, Any]:
    """Scene 2 as the fact check is handed it, showing the pour with SHOWCASE's labels."""
    return {"scene": 2, "line": line, "shows": POUR, "usage": None, "result": None}


@pytest.fixture
def notices(api: APIClient, session_id: str) -> Callable[[], list[str]]:
    """The notices about the ad's scenes shown in the chat, oldest first: the test server
    has no Firecrawl, which has its own."""
    return lambda: [
        text for role, text in chat(api, session_id) if role == "notice" and "Scene" in text
    ]


# --- A B-roll line too short ------------------------------------------------------------------


@pytest.fixture
def lengthened_scene_2(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    """A chat whose B-roll scene 2 said a line of 2 seconds, lengthened once to 4."""
    checking(fake_model, product_page_url, scene_2_saying(SHORT))
    fake_model.respond("fact_check", FACTS_OK, facts_ok(2))
    fake_model.respond("lengthen_line", {"line": FITS})
    say(f"Make an ad for {product_page_url}")


def test_a_broll_line_too_short_for_its_clip_is_lengthened(lengthened_scene_2: None) -> None:
    assert (lines(), scene_2().shows) == ([OPENING, FITS, PRICE], POUR)


def test_a_broll_line_is_lengthened_to_the_fewest_words_the_voice_says_in_4_seconds(
    lengthened_scene_2: None,
) -> None:
    (sent,) = handoffs("lengthen_line")
    assert (sent["scene"], sent["fewest_words"]) == (2, 8)
    assert sent["script"][1] == checked_line(SHORT)


def test_a_lengthened_line_is_fact_checked_again_and_the_checks_pass(
    lengthened_scene_2: None,
) -> None:
    assert rechecked() == [[checked_line(FITS)]]
    assert results_of("run_planning_checks")[0].splitlines()[0] == PASSED


def test_a_broll_line_said_in_exactly_4_seconds_isnt_lengthened(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(fake_model, product_page_url, scene_2_saying(FITS))
    fake_model.respond("fact_check", FACTS_OK)

    say(f"Make an ad for {product_page_url}")

    assert "lengthen_line" not in paid_for()
    assert Job.objects.get().status == "ready_to_render"


@pytest.fixture
def still_short_after_two_lengthenings(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    """A chat whose B-roll scene 2's line was lengthened twice and is still too short."""
    checking(fake_model, product_page_url, scene_2_saying(SHORT))
    fake_model.respond("fact_check", FACTS_OK, facts_ok(2), facts_ok(2))
    fake_model.respond("lengthen_line", {"line": "Hand-thrown, it holds 350 ml."}, {"line": SHORT})
    say(f"Make an ad for {product_page_url}")


def test_a_broll_line_still_too_short_after_two_lengthenings_is_kept_and_nobody_is_asked(
    still_short_after_two_lengthenings: None, notices: Callable[[], list[str]]
) -> None:
    assert paid_for().count("lengthen_line") == 2
    assert (scene_2().line, scene_2().shows) == (SHORT, POUR)
    assert results_of("run_planning_checks")[0].splitlines()[0] == PASSED
    # Lengthening isn't shown in the chat.
    assert notices() == []


def test_a_talking_line_too_short_for_a_clip_is_left_alone(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(fake_model, product_page_url, scene_2_saying(SHORT, shows=None))
    fake_model.respond("fact_check", FACTS_OK)

    say(f"Make an ad for {product_page_url}")

    assert "lengthen_line" not in paid_for()
    assert Job.objects.get().status == "ready_to_render"


# --- A B-roll line too long -------------------------------------------------------------------


@pytest.fixture
def shortened_scene_2(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    """A chat whose B-roll scene 2 said a line of 16 seconds, shortened once to fit."""
    checking(fake_model, product_page_url, scene_2_saying(LONG))
    fake_model.respond("fact_check", FACTS_OK, facts_ok(2))
    fake_model.respond("shorten_line", {"line": FITS})
    say(f"Make an ad for {product_page_url}")


def test_a_broll_line_too_long_for_its_clip_is_shortened(shortened_scene_2: None) -> None:
    assert (lines(), scene_2().shows) == ([OPENING, FITS, PRICE], POUR)


def test_a_broll_line_is_shortened_to_the_most_words_the_voice_says_in_14_seconds(
    shortened_scene_2: None,
) -> None:
    assert [(sent["scene"], sent["most_words"]) for sent in handoffs("shorten_line")] == [(2, 28)]


def test_a_shortened_broll_line_is_fact_checked_again(shortened_scene_2: None) -> None:
    assert rechecked() == [[checked_line(FITS)]]
    assert results_of("run_planning_checks")[0].splitlines()[0] == PASSED


def test_a_broll_line_said_in_exactly_14_seconds_isnt_shortened(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(fake_model, product_page_url, scene_2_saying(LONGEST))
    fake_model.respond("fact_check", FACTS_OK)

    say(f"Make an ad for {product_page_url}")

    assert "shorten_line" not in paid_for()
    assert Job.objects.get().status == "ready_to_render"


def test_a_talking_line_of_16_seconds_is_left_alone(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(fake_model, product_page_url, scene_2_saying(LONG, shows=None))
    fake_model.respond("fact_check", FACTS_OK)

    say(f"Make an ad for {product_page_url}")

    assert "shorten_line" not in paid_for()
    assert (scene_2().line, Job.objects.get().status) == (LONG, "ready_to_render")


@pytest.fixture
def still_long_after_three_shortenings(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    """A chat whose B-roll scene 2's line was shortened 3 times and still takes 16 seconds."""
    checking(fake_model, product_page_url, scene_2_saying(LONG))
    fake_model.respond("fact_check", FACTS_OK, facts_ok(2), facts_ok(2), facts_ok(2))
    fake_model.respond("shorten_line", {"line": LONG}, {"line": LONG}, {"line": LONG})
    say(f"Make an ad for {product_page_url}")


def test_a_broll_line_still_too_long_after_three_shortenings_becomes_a_talking_scene(
    still_long_after_three_shortenings: None,
) -> None:
    assert paid_for().count("shorten_line") == 3
    assert (scene_2().line, scene_2().shows) == (LONG, "")
    assert broll_labels()[1] == ("", "", "", "", [])
    # Nobody is asked: the checks pass with the person saying the line.
    assert results_of("run_planning_checks")[0].splitlines()[0] == PASSED


def test_a_broll_scene_that_becomes_a_talking_scene_is_told_in_the_chat_and_kept_with_the_job(
    still_long_after_three_shortenings: None, notices: Callable[[], list[str]]
) -> None:
    assert notices() == [SWITCHED]
    assert ("info", SWITCHED) in [
        (warning["level"], warning["text"]) for warning in Job.objects.get().warnings
    ]


def test_a_broll_scene_that_becomes_a_talking_scene_starts_the_talking_limits_fresh(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    checking(fake_model, product_page_url, scene_2_saying(TOO_LONG_TO_TALK))
    fake_model.respond("fact_check", FACTS_OK, *[facts_ok(2)] * 4)
    fake_model.respond(
        "shorten_line", *[{"line": TOO_LONG_TO_TALK}] * 3, {"line": "Hand-thrown and safe."}
    )

    say(f"Make an ad for {product_page_url}")

    # Shortened 3 times as B-roll, to 28 words, then once as a talking line, to 36.
    assert [sent["most_words"] for sent in handoffs("shorten_line")] == [28, 28, 28, 36]
    assert (scene_2().line, scene_2().shows) == ("Hand-thrown and safe.", "")


# --- A B-roll line the shop owner chose ------------------------------------------------------


@pytest.fixture
def asked_about_scene_2(
    request: pytest.FixtureRequest,
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    """A chat whose B-roll scene 2 still failed the fact check after 2 rewrites, and whose
    producer asked the shop owner about it. Parametrize it indirectly with scene 2's line."""
    line: str = request.param
    checking(fake_model, product_page_url, scene_2_saying(line), reply="Keep scene 2?")
    fake_model.respond(
        "fact_check",
        failing(2, "The line says $19.99.", passing=(1, 3)),
        failing(2, "The line says $19.99."),
        failing(2, "The line says $19.99."),
    )
    fake_model.respond("rewrite_line", *[broll({"line": line, "shows": POUR})] * 2)
    say(f"Make an ad for {product_page_url}")


def choosing(fake_model: FakeModel, say: Callable[..., None], choice: dict[str, Any]) -> None:
    """The shop owner answers, and the producer runs the checks with their choice for scene
    2."""
    fake_model.respond(
        "produce",
        turn(calls=[("run_planning_checks", {**NO_CHOICES, "line_choices": [choice]})]),
        turn(says="Done."),
    )
    say(f"Go with that: {choice['own_line'] or 'keep it'}")


@pytest.mark.parametrize("asked_about_scene_2", [LONG], indirect=True)
@pytest.mark.parametrize(
    "choice",
    [
        pytest.param({"scene": 2, "choice": "keep", "own_line": None}, id="kept"),
        pytest.param({"scene": 2, "choice": "own", "own_line": LONG}, id="their own"),
    ],
)
def test_a_broll_line_the_shop_owner_chose_too_long_for_its_clip_becomes_a_talking_scene(
    fake_model: FakeModel,
    asked_about_scene_2: None,
    say: Callable[..., None],
    notices: Callable[[], list[str]],
    choice: dict[str, Any],
) -> None:
    choosing(fake_model, say, choice)

    # Their line is never shortened, and they aren't asked about its length.
    assert "shorten_line" not in paid_for()
    assert (scene_2().line, scene_2().shows) == (LONG, "")
    assert notices() == [SWITCHED]
    assert results_of("run_planning_checks")[1].splitlines()[0] == PASSED


@pytest.mark.parametrize("asked_about_scene_2", [SHORT], indirect=True)
def test_a_broll_line_the_shop_owner_kept_too_short_for_its_clip_is_kept_as_it_is(
    fake_model: FakeModel, asked_about_scene_2: None, say: Callable[..., None]
) -> None:
    choosing(fake_model, say, {"scene": 2, "choice": "keep", "own_line": None})

    assert "lengthen_line" not in paid_for()
    assert (scene_2().line, scene_2().shows) == (SHORT, POUR)
    assert results_of("run_planning_checks")[1].splitlines()[0] == PASSED


# --- The seconds rule ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("words_per_second", "fewest", "most"),
    [(2.0, 8, 28), (2.5, 10, 35), (2.6, 11, 36)],
)
def test_a_broll_line_has_the_whole_words_the_voice_says_in_4_to_14_seconds(
    words_per_second: float, fewest: int, most: int
) -> None:
    assert (
        fewest_words_in_a_broll_line(words_per_second),
        most_words_in_a_broll_line(words_per_second),
    ) == (fewest, most)
