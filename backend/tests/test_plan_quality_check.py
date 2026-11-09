"""The plan quality check (#404, #437) through the producer's chat: a plan that says a claim
twice is sent back to the planner, at most twice. The check asks nothing about the hook or
whether a scene proves its line. Outside services are scripted."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from adforge.retry import OutsideServiceDown
from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs import notices
from jobs.models import Job

from .conftest import PLAN_QUALITY_PASSED, recorded, results_of
from .test_h3_planning import h3_plan, script_plan

pytestmark = pytest.mark.django_db(transaction=True)


def failing(claims: str) -> Callable[[Any], dict[str, Any]]:
    """The check's answer: "no" for the claim said twice that `claims` names."""

    def answer(_: Any) -> dict[str, Any]:
        return {
            "decision": "fail",
            "reason": "Fix the plan.",
            "claims_said_once": "no",
            "claims_why": claims,
        }

    return answer


def plan_saying(line: str, count: int = 3) -> dict[str, Any]:
    """The mug's H3 plan with scene 2 saying `line`, so each plan can be told apart."""
    plan = h3_plan(count)
    plan["plan"]["scenes"][1]["line"] = line
    return plan


FIRST = "It holds 350 ml of your favourite hot tea, with a hand thrown finish for every drink."
SECOND = "It holds 350 ml of hot tea, and its hand thrown glaze makes each sip feel special."
THIRD = "Pour in 350 ml of your favourite hot tea and feel the hand thrown finish in your hand."


def script_checks(fake_model: FakeModel, *answers: Callable[[Any], dict[str, Any]]) -> None:
    """Answer the plan check's asks with `answers`, in turn, and pass any after them."""
    answering = list(answers)

    def answer(request: Any) -> dict[str, Any]:
        if answering:
            return answering.pop(0)(request)
        return PLAN_QUALITY_PASSED

    fake_model.answer_unscripted("check_plan_quality", answer)


def overlong() -> dict[str, Any]:
    """The mug's H3 plan with an overlay too long to draw: a code rule."""
    plan = h3_plan()
    plan["plan"]["scenes"][0]["overlay"] = "An overlay far too long to fit in its one band"
    return plan


def kept_line() -> str:
    return Job.objects.get().scenes.get(number=2).line


def asked(fake_model: FakeModel, purpose: str) -> list[Any]:
    return [request for request in fake_model.asked if request.purpose == purpose]


def planner_told(fake_model: FakeModel) -> list[str]:
    """What the planner was told on each ask, in turn."""
    return [request.instructions for request in asked(fake_model, "plan_ad")]


def kinds_recorded() -> list[str]:
    return [warning["kind"] for warning in Job.objects.get().warnings]


def test_a_plan_the_check_fails_once_is_sent_back_and_the_second_plan_kept(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    script_plan(fake_model, product_page_url, plan_saying(FIRST), plan_saying(SECOND))
    script_checks(fake_model, failing("350 ml is said in scenes 2 and 3."))

    say(product_page_url)

    assert kept_line() == SECOND
    told = planner_told(fake_model)
    assert len(told) == 2
    assert (
        "The plan quality check sent your last plan back: A claim is said twice: 350 ml is "
        "said in scenes 2 and 3." in told[1]
    )
    assert FIRST in told[1]
    assert results_of("plan_ad")[0].startswith("Planned 3 scenes.")
    # Every verdict is recorded for the developer, and neither is a failure.
    verdicts = [text for _, text in recorded(api, session_id) if text.startswith("Plan ")]
    assert len(verdicts) == 2
    assert verdicts[0].startswith("Plan 1: fail (1 no). Every claim once: no.")
    assert verdicts[0].endswith("Sent back to the planner.")
    assert verdicts[1].startswith("Plan 2: pass (0 no).")
    assert notices.PLAN_QUALITY_KEPT.name not in kinds_recorded()


def test_the_check_asks_only_whether_a_claim_is_said_twice(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    # Scene 1 only shows the mug to the lens, scene 2 is B-roll, scene 3 talks while she drinks
    # from the mug, and scene 4, the last, says the price.
    script_plan(fake_model, product_page_url, h3_plan(4))

    say(product_page_url)

    (check,) = asked(fake_model, "check_plan_quality")
    assert [scene.line for scene in check.handoff.scenes][3].endswith("from Kiln & Co.")
    told = check.instructions.lower()
    assert "is every claim said only once" in told
    # The proof question is gone (Julian, Nov 7 2026, #437), and the hook is never asked
    # (Julian, Nov 6: too subjective).
    assert "proof" not in told and "prove" not in told
    assert "hook" not in told
    assert results_of("plan_ad")[0].startswith("Planned 4 scenes.")


@pytest.mark.parametrize(
    ("answers", "kept", "kept_number"),
    [
        # Every plan gets one "no", so the tie goes to the latest plan.
        pytest.param([failing("x"), failing("y"), failing("z")], THIRD, 3, id="tie to the latest"),
    ],
)
def test_a_plan_failing_the_check_three_times_goes_on_with_the_fewest_no_answers(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    answers: list[Callable[[Any], dict[str, Any]]],
    kept: str,
    kept_number: int,
) -> None:
    script_plan(
        fake_model,
        product_page_url,
        plan_saying(FIRST, 4),
        plan_saying(SECOND, 4),
        plan_saying(THIRD, 4),
    )
    script_checks(fake_model, *answers)

    say(product_page_url)

    assert kept_line() == kept
    # The first plan and two sent back: never a fourth plan or check.
    assert len(planner_told(fake_model)) == 3
    assert len(asked(fake_model, "check_plan_quality")) == 3
    assert fake_model.left("plan_ad") == 0
    texts = [text for _, text in recorded(api, session_id)]
    gone_on = next(text for text in texts if text.startswith("No plan passed"))
    assert f"so plan {kept_number}, with the fewest" in gone_on
    for number in (1, 2, 3):
        assert f"Plan {number}: fail" in gone_on
    assert kinds_recorded().count(notices.PLAN_QUALITY_VERDICT.name) == 3
    assert notices.PLAN_QUALITY_KEPT.name in kinds_recorded()
    # The plan kept is the planner's one plan call left "plan": the producer reads its reason.
    assert list(
        ModelCall.objects.filter(purpose="plan_ad")
        .order_by("created_at", "id")
        .values_list("decision", flat=True)
    ) == ["plan" if number == kept_number else "sent back" for number in (1, 2, 3)]
    assert next(text for text in texts if text.startswith("Plan 3:")).endswith("The plan goes on.")


ASKED = {
    "decision": "ask",
    "reason": "The price isn't clear.",
    "question": "Which price should the ad say?",
    "question_items": [{"kind": "price", "question": "Which price should the ad say?"}],
    "plan": None,
}


def test_a_replan_that_asks_instead_goes_on_with_the_plans_made_so_far(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    script_plan(fake_model, product_page_url, plan_saying(FIRST), ASKED)
    script_checks(fake_model, failing("x"))

    say(product_page_url)

    assert kept_line() == FIRST
    assert len(asked(fake_model, "check_plan_quality")) == 1
    gone_on = next(text for _, text in recorded(api, session_id) if text.startswith("No plan"))
    assert "stopping as the planner asked the shop owner a question instead of planning" in (
        gone_on
    )
    assert "so plan 1," in gone_on


def test_a_replan_that_breaks_a_code_rule_three_times_fails_the_tool(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    broken = overlong()
    script_plan(fake_model, product_page_url, plan_saying(FIRST), broken, broken, broken)
    script_checks(fake_model, failing("x"))

    say(product_page_url)

    # As any plan breaking a code rule three times: told twice, then the plan tool fails.
    assert not Job.objects.get().scenes.exists()
    assert "An overlay is a few words" in results_of("plan_ad")[0]


def test_a_plan_that_breaks_a_code_rule_three_times_still_fails_the_tool(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    broken = overlong()
    script_plan(fake_model, product_page_url, broken, broken, broken)

    say(product_page_url)

    assert not Job.objects.get().scenes.exists()
    assert not asked(fake_model, "check_plan_quality")
    assert "An overlay is a few words" in results_of("plan_ad")[0]


@pytest.mark.parametrize(
    "silence",
    [
        pytest.param([OutsideServiceDown("Azure answered 503")] * 8, id="its service failing"),
        pytest.param([{"decision": "pass", "reason": "Fine."}] * 2, id="no usable answer"),
    ],
)
def test_a_check_that_gives_no_answer_never_blocks_the_plan(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    silence: list[Any],
) -> None:
    script_plan(fake_model, product_page_url, plan_saying(FIRST))
    fake_model.respond("check_plan_quality", *silence)

    say(product_page_url)

    assert kept_line() == FIRST
    assert len(planner_told(fake_model)) == 1
    assert any(
        text.startswith("The plan quality check didn't answer this time")
        for _, text in recorded(api, session_id)
    )
    assert notices.PLAN_QUALITY_UNANSWERED.name in kinds_recorded()


def test_the_broll_rule_reads_the_same_in_the_proven_rules_doc() -> None:
    from pathlib import Path

    from jobs.planning import BROLL_SELLS_RULE

    doc = Path(__file__).parents[2] / "docs" / "agents" / "proven-broll-rules.md"
    assert BROLL_SELLS_RULE in doc.read_text()
