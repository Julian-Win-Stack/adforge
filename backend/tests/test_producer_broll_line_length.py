"""Both scene kinds use the same fixed 4–14 second speech limits."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel
from jobs.models import Job

from .conftest import chat, facts_ok, handoffs, paid_for, recorded, results_of
from .test_h3_planning import h3_plan, script_plan

pytestmark = pytest.mark.django_db(transaction=True)

FITS = "The mug holds your favourite hot drink at home today and every day."
LONG = " ".join(["mug"] * 40)


def with_line(kind: str, line: str) -> dict[str, Any]:
    plan = h3_plan()
    plan["plan"]["scenes"][1].update(kind=kind, line=line)
    if kind == "talking":
        plan["plan"]["scenes"][1].update(kind_why="")
    return plan


@pytest.mark.parametrize("kind", ["talking", "broll"])
def test_a_short_line_is_lengthened_to_twelve_words_and_fact_checked_again(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], kind: str
) -> None:
    script_plan(fake_model, product_page_url, with_line(kind, "A mug."), checks=True)
    fake_model.respond("fact_check", facts_ok(1, 2, 3), facts_ok(2))
    fake_model.respond("lengthen_line", {"line": FITS})
    say(product_page_url)
    assert Job.objects.get().scenes.get(number=2).line == FITS
    assert handoffs("lengthen_line")[0]["fewest_words"] == 12
    assert handoffs("fact_check")[-1]["lines"][0]["line"] == FITS
    assert results_of("run_planning_checks")[0].startswith("The checks passed.")
    assert not {"design_voice", "measure_voice", "speak_line"} & set(paid_for())


@pytest.mark.parametrize("kind", ["talking", "broll"])
@pytest.mark.parametrize("line", [FITS, " ".join(["mug"] * 39)])
def test_lines_at_both_fixed_speed_boundaries_need_no_rewrite(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], kind: str, line: str
) -> None:
    script_plan(fake_model, product_page_url, with_line(kind, line), checks=True)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    say(product_page_url)
    assert "lengthen_line" not in paid_for()
    assert "shorten_line" not in paid_for()
    assert results_of("run_planning_checks")[0].startswith("The checks passed.")


@pytest.mark.parametrize("kind", ["talking", "broll"])
def test_a_long_line_is_shortened_to_thirty_nine_words_and_checked_again(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], kind: str
) -> None:
    script_plan(fake_model, product_page_url, with_line(kind, LONG), checks=True)
    fake_model.respond("fact_check", facts_ok(1, 2, 3), facts_ok(2))
    fake_model.respond("shorten_line", {"line": FITS})
    say(product_page_url)
    assert handoffs("shorten_line")[0]["most_words"] == 39
    assert handoffs("fact_check")[-1]["lines"][0]["line"] == FITS
    scene = Job.objects.get().scenes.get(number=2)
    assert (scene.kind, scene.line) == (kind, FITS)


@pytest.mark.parametrize("kind", ["talking", "broll"])
def test_after_two_lengthenings_a_short_line_is_returned_to_the_producer(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], kind: str
) -> None:
    script_plan(fake_model, product_page_url, with_line(kind, "A mug."), checks=True)
    fake_model.respond("fact_check", facts_ok(1, 2, 3), facts_ok(2), facts_ok(2))
    fake_model.respond("lengthen_line", {"line": "A lovely mug."}, {"line": "A mug."})
    say(product_page_url)
    assert paid_for().count("lengthen_line") == 2
    assert "shorter than 4 seconds" in results_of("run_planning_checks")[0]
    assert not results_of("ask_owner")
    assert Job.objects.get().scenes.get(number=2).kind == kind


@pytest.mark.parametrize("kind", ["talking", "broll"])
def test_after_three_shortenings_the_producer_gets_the_line_without_changing_kind(
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    kind: str,
) -> None:
    script_plan(fake_model, product_page_url, with_line(kind, LONG), checks=True)
    fake_model.respond("fact_check", facts_ok(1, 2, 3), *[facts_ok(2)] * 3)
    fake_model.respond("shorten_line", *[{"line": LONG}] * 3)
    say(product_page_url)
    assert paid_for().count("shorten_line") == 3
    answer = results_of("run_planning_checks")[0]
    assert "Split the scene or rewrite its line with the scene tools" in answer
    assert "including talking scenes" in answer
    assert not results_of("ask_owner")
    assert any("after 3 shortenings" in text for _, text in recorded(api, session_id))
    assert not any("after 3 shortenings" in text for _, text in chat(api, session_id))
    scene = Job.objects.get().scenes.get(number=2)
    assert (scene.kind, scene.line) == (kind, LONG)
    assert (
        "info",
        "Scene 2 couldn't be made as a product shot because its line is too long for a clip, "
        "so it will be said to camera instead.",
    ) not in recorded(api, session_id)
