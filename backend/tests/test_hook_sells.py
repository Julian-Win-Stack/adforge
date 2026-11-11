"""The hook sells the product and the whole ad follows its story (Julian, Nov 9 2026, thread
"Make hooks sell the product": round 13 hooks named only a problem or a stock surprise and
never sold the product, and some bodies switched to a benefit the hook never set up. He
watched the filmed test of this wording and approved it). Through the chat, with outside
models scripted."""

from collections.abc import Callable

import pytest
from pytest_django import Settings

from agents.scene_forms import VideoForm
from gateway.fake import FakeModel
from jobs.planning import KEEPS_THE_HOOK, ONE_STORY

from .conftest import facts_ok
from .test_h3_planning import h3_plan, script_plan
from .test_producer_checks import failing
from .test_scene_rules_sent import asked

pytestmark = pytest.mark.django_db(transaction=True)


def flat(text: str) -> str:
    return " ".join(text.split())


def test_a_rewrite_of_the_first_scene_keeps_the_selling_hook_but_not_the_story_rule(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], settings: Settings
) -> None:
    plan = h3_plan()
    script_plan(fake_model, product_page_url, plan, checks=True)
    check = failing(1, "The page doesn't say that.", passing=(2, 3))
    fake_model.respond("fact_check", check, facts_ok(1))
    fake_model.respond("rewrite_line", plan["plan"]["scenes"][0])

    say(product_page_url)

    (told,) = (flat(instructions) for instructions in asked(fake_model, "rewrite_line"))
    assert KEEPS_THE_HOOK in told
    assert ONE_STORY not in told


def test_only_a_problem_hook_gets_a_gesture_for_the_problem() -> None:
    beats = flat(VideoForm.model_fields["beats"].description or "")
    assert "In the hook (part: hook) when its line names a problem, her free hand acts out" in beats
    assert "A hook that opens on the result instead gets no such gesture" in beats
    assert "surprise" not in beats
