"""The plan quality check (#404) through the producer's chat: every scene carries a proof, and a
plan whose action doesn't show it, that says a claim twice or whose hook isn't the page's
strongest point is sent back to the planner, at most twice. Outside services are scripted."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from adforge.retry import OutsideServiceDown
from agents.scene_agent import media
from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs import notices, plan_quality
from jobs.models import Job

from .conftest import handoffs, recorded, results_of
from .test_h3_planning import h3_plan, script_plan

pytestmark = pytest.mark.django_db(transaction=True)


def failing(
    *no_proof: int, claims: str = "", hook: str = "", why: str = "the action doesn't show the claim"
) -> Callable[[Any], dict[str, Any]]:
    """The check's answer: "no" for the proof of each scene in `no_proof`, for a claim said
    twice when `claims` names it, and for the hook when `hook` names a stronger point."""

    def answer(request: Any) -> dict[str, Any]:
        return {
            "decision": "fail",
            "reason": "Fix the plan.",
            "proofs": [
                {
                    "scene": number,
                    "answer": "no" if number in no_proof else "yes",
                    "why": why if number in no_proof else "It shows it.",
                }
                for number in request.handoff.proof_asked
            ],
            "claims_said_once": "no" if claims else "yes",
            "claims_why": claims,
            "hook_strongest": "no" if hook else "yes",
            "hook_why": hook,
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
        from .conftest import plan_quality_passes

        return plan_quality_passes(request)

    fake_model.answer_unscripted("check_plan_quality", answer)


def kept_line() -> str:
    return Job.objects.get().scenes.get(number=2).line


def asked(fake_model: FakeModel, purpose: str) -> list[Any]:
    return [request for request in fake_model.asked if request.purpose == purpose]


def planner_told(fake_model: FakeModel) -> list[str]:
    """What the planner was told on each ask, in turn."""
    return [request.instructions for request in asked(fake_model, "plan_ad")]


def kinds_recorded() -> list[str]:
    return [warning["kind"] for warning in Job.objects.get().warnings]


def test_a_plan_with_a_scene_missing_its_proof_is_sent_back_naming_the_rule(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    missing = h3_plan()
    missing["plan"]["scenes"][1]["proof"] = ""
    script_plan(fake_model, product_page_url, missing, plan_saying(SECOND))

    say(product_page_url)

    assert kept_line() == SECOND
    assert (
        "Scene 2 has no proof: give every scene its proof, what the viewer sees in the scene "
        "that proves its line." in planner_told(fake_model)[1]
    )


def test_a_plan_the_check_fails_once_is_sent_back_and_the_second_plan_kept(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    script_plan(fake_model, product_page_url, plan_saying(FIRST), plan_saying(SECOND))
    script_checks(fake_model, failing(2))

    say(product_page_url)

    assert kept_line() == SECOND
    told = planner_told(fake_model)
    assert len(told) == 2
    assert (
        "The plan quality check sent your last plan back: Scene 2's action doesn't show "
        in (told[1])
    )
    assert "the action doesn't show the claim" in told[1]
    assert FIRST in told[1]
    assert results_of("plan_ad")[0].startswith("Planned 3 scenes.")
    # Every verdict is recorded for the developer, and neither is a failure.
    verdicts = [text for _, text in recorded(api, session_id) if text.startswith("Plan ")]
    assert len(verdicts) == 2
    assert verdicts[0].startswith("Plan 1: fail (1 no). Proof: scene 2 no, scene 3 yes;")
    assert verdicts[0].endswith("Sent back to the planner.")
    assert verdicts[1].startswith("Plan 2: pass (0 no).")
    assert notices.PLAN_QUALITY_KEPT.name not in kinds_recorded()


@pytest.mark.parametrize(
    ("failure", "said"),
    [
        pytest.param(
            failing(claims="350 ml is said in scenes 2 and 3."),
            "A claim is said twice: 350 ml is said in scenes 2 and 3.",
            id="a claim said twice",
        ),
        pytest.param(
            failing(hook="Dishwasher safe, stated on the page, is stronger."),
            "The hook isn't the page's strongest true point: Dishwasher safe, stated on the "
            "page, is stronger.",
            id="a weak hook",
        ),
    ],
)
def test_a_repeated_claim_or_a_weak_hook_is_sent_back_the_same_way(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    failure: Callable[[Any], dict[str, Any]],
    said: str,
) -> None:
    script_plan(fake_model, product_page_url, plan_saying(FIRST), plan_saying(SECOND))
    script_checks(fake_model, failure)

    say(product_page_url)

    assert kept_line() == SECOND
    assert said in planner_told(fake_model)[1]


@pytest.mark.parametrize(
    ("answers", "kept", "kept_number"),
    [
        pytest.param([failing(2), failing(2, 3), failing(2, 3, hook="x")], FIRST, 1, id="first"),
        # A tie goes to the latest plan.
        pytest.param([failing(2, 3), failing(2), failing(3)], THIRD, 3, id="tie to the latest"),
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
        fake_model, product_page_url, plan_saying(FIRST), plan_saying(SECOND), plan_saying(THIRD)
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
    script_checks(fake_model, failing(2))

    say(product_page_url)

    assert kept_line() == FIRST
    assert len(asked(fake_model, "check_plan_quality")) == 1
    gone_on = next(text for _, text in recorded(api, session_id) if text.startswith("No plan"))
    assert "stopping as the planner asked the shop owner a question instead of planning" in (
        gone_on
    )
    assert "so plan 1," in gone_on


def test_a_replan_that_breaks_a_code_rule_twice_fails_the_tool(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    missing = h3_plan()
    missing["plan"]["scenes"][1]["proof"] = ""
    script_plan(fake_model, product_page_url, plan_saying(FIRST), missing, missing)
    script_checks(fake_model, failing(2))

    say(product_page_url)

    # As any plan breaking a code rule twice: told once, then the plan tool fails.
    assert not Job.objects.get().scenes.exists()
    assert "Scene 2 has no proof" in results_of("plan_ad")[0]


def test_a_plan_that_breaks_a_code_rule_twice_still_fails_the_tool(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    missing = h3_plan()
    missing["plan"]["scenes"][1]["proof"] = ""
    script_plan(fake_model, product_page_url, missing, missing)

    say(product_page_url)

    assert not Job.objects.get().scenes.exists()
    assert not asked(fake_model, "check_plan_quality")
    assert "Scene 2 has no proof" in results_of("plan_ad")[0]


def test_only_a_first_or_last_scene_showing_the_product_to_the_lens_skips_the_proof_question(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    # Scene 1 and scene 4 only show the mug to the lens; scene 2 is B-roll and scene 3 a
    # middle talking scene.
    script_plan(fake_model, product_page_url, h3_plan(4))

    say(product_page_url)

    asked = handoffs("check_plan_quality")[0]
    assert asked["proof_asked"] == [2, 3]
    assert [scene["proof"] for scene in asked["scenes"]] == [
        "The mug held up to the lens.",
        "Tea fills the mug up close.",
        "She drinks from the mug as she speaks.",
        "She holds the mug, ready to drink from it.",
    ]
    assert plan_quality.proof_asked(1, 4, talking=True, shows_to_lens=False)
    assert not plan_quality.proof_asked(4, 4, talking=True, shows_to_lens=True)
    assert plan_quality.proof_asked(2, 4, talking=True, shows_to_lens=True)
    # A B-roll scene is always asked, even one that says it only shows the product.
    assert plan_quality.proof_asked(4, 4, talking=False, shows_to_lens=True)


@pytest.mark.parametrize(
    "silence",
    [
        pytest.param([OutsideServiceDown("Azure answered 503")] * 8, id="its service failing"),
        pytest.param(
            [{"decision": "pass", "reason": "Fine.", "proofs": []}] * 2, id="no usable answer"
        ),
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


def test_each_scene_agent_is_given_its_scenes_proof_beside_its_line(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    script_plan(fake_model, product_page_url, h3_plan())

    say(product_page_url)

    facts = media._scene_facts(Job.objects.get().scenes.get(number=2)).splitlines()
    assert facts[1].startswith('  line: "It holds 350 ml')
    assert facts[2] == (
        "  proof (what the viewer sees that proves the line): Tea fills the mug up close."
    )


@pytest.mark.parametrize(
    ("number", "answer", "refused", "kind"),
    [
        pytest.param(
            2,
            {"decision": "no", "reason": "It only sets the mug down."},
            "It only sets the mug down.",
            notices.SCENE_PROOF_VERDICT,
            id="no",
        ),
        pytest.param(
            2,
            {"decision": "yes", "reason": "The pour shows it."},
            None,
            notices.SCENE_PROOF_VERDICT,
            id="yes",
        ),
        pytest.param(
            2,
            OutsideServiceDown("Azure answered 503"),
            None,
            notices.SCENE_PROOF_UNANSWERED,
            id="no answer",
        ),
        pytest.param(1, None, None, None, id="a first scene showing it to the lens"),
    ],
)
def test_the_proof_check_asks_one_scene_the_same_question(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    number: int,
    answer: Any,
    refused: str | None,
    kind: notices.Kind | None,
) -> None:
    script_plan(fake_model, product_page_url, h3_plan())
    say(product_page_url)
    if answer is not None:
        times = 8 if kind is notices.SCENE_PROOF_UNANSWERED else 1
        fake_model.respond("check_scene_proof", *[answer] * times)
    before = len(Job.objects.get().warnings)

    assert plan_quality.proof_check(Job.objects.get().scenes.get(number=number)) == refused

    checked = asked(fake_model, "check_scene_proof")
    if kind is None:
        assert not checked
        assert len(Job.objects.get().warnings) == before
        return
    assert plan_quality.PROOF_QUESTION in checked[0].instructions
    assert handoffs("check_scene_proof")[0]["scene"]["proof"] == "Tea fills the mug up close."
    assert Job.objects.get().warnings[-1]["kind"] == kind.name


def test_the_proof_rule_reads_the_same_in_the_proven_rules_doc() -> None:
    from pathlib import Path

    from jobs.planning import PROOF_RULE

    doc = Path(__file__).parents[2] / "docs" / "agents" / "proven-broll-rules.md"
    assert PROOF_RULE in doc.read_text()
    assert PROOF_RULE in plan_quality.PROOF_QUESTION
