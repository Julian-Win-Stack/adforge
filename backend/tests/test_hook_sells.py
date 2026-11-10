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

from .conftest import facts_ok
from .test_h3_planning import h3_plan, script_plan
from .test_producer_checks import failing
from .test_scene_rules_sent import asked

pytestmark = pytest.mark.django_db(transaction=True)

# What the hook does, in the words Julian approved.
HOOK_SELLS = (
    "The hook does two things, in this order. First, the problem this product solves",
    "the main problem the page, the product's videos or the shop owner name, said in her own "
    "words as her own everyday moment of it.",
    "Only when they name no problem does it open on the solution instead: the result they "
    "promise, said as her own moment of having it.",
    "if another product could be swapped in and it would still work, it is too general.",
    "Then, in the same hook, she turns to the product as her answer to that moment",
    "one fact from the page about how it does, or her own opinion",
    "By the end of the hook the viewer has heard both the problem and that this product "
    "answers it.",
    "Never a stock filler phrase.",
    "It never opens on the product's name and never asks the viewer a question.",
)
# A hook that also sells runs a little longer (Julian's numbers).
HOOK_LENGTH = (
    "Its first sentence lands the problem or the result in about 8 words",
    "within 3 seconds.",
    "The whole hook aims for about 20 words",
    "it uses up to 26 words.",
)
ONE_STORY = (
    "The whole ad tells the hook's story.",
    "The body shows how this product answers that same problem, or gives that same result",
    "the last scene's after answers the hook: that problem gone, or why she's won over.",
    "No scene switches to a benefit the hook didn't set up.",
)
# What the old hook said and the new one must not.
OLD_HOOK = (
    "her honest surprise about the product",
    "never a sales claim",
    "stays a short hook",
    "aims for about 12 words",
    "up to 16 words",
    "best reason to buy",
    "surprising claim",
)


def flat(text: str) -> str:
    return " ".join(text.split())


def test_the_planner_writes_a_hook_that_sells_and_one_story_after_it(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], settings: Settings
) -> None:
    script_plan(fake_model, product_page_url, h3_plan())

    say(product_page_url)

    (told,) = (flat(instructions) for instructions in asked(fake_model, "plan_ad"))
    for rule in HOOK_SELLS + HOOK_LENGTH + ONE_STORY:
        assert rule in told
    for old in OLD_HOOK:
        assert old not in told
    # The one story comes right after the hook's length, as tested.
    assert (
        told.index(HOOK_LENGTH[-1])
        < told.index(ONE_STORY[0])
        < told.index("Every talking line sounds like a real person")
    )
    # The kind of thing waits at most to scene 2, and the hook never opens on the product.
    assert "The hook opens on the problem or the result, never on the product." in told
    # The last scene answers either kind of hook.
    assert "after a hook about a result, why she's won over" in told
    assert "surprise" not in told


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
    assert "The first scene's line stays the hook." in told
    for rule in HOOK_SELLS + HOOK_LENGTH:
        assert rule in told
    for old in OLD_HOOK:
        assert old not in told
    # The story rule is the planner's alone (the tested setup).
    assert ONE_STORY[0] not in told


def test_only_a_problem_hook_gets_a_gesture_for_the_problem() -> None:
    beats = flat(VideoForm.model_fields["beats"].description or "")
    assert "In the hook (part: hook) when its line names a problem, her free hand acts out" in beats
    assert "A hook that opens on the result instead gets no such gesture" in beats
    assert "surprise" not in beats
