"""The per-ad "what the agent did" record and page (spec #247 point 17), end to end: the app
makes a B-roll clip through the chat and the fixing agent remakes it; the producer then calls
tools after that first clip (rows as the agent loop writes them, since its tools after the
first clip come with later tickets: a look, and the fixture's folded call); the round is
exported, and the grading page and the ad's page are built from the run folder. The models
are faked at the gateway."""

import json
import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.utils import timezone

from agents.models import ToolCall
from evals import agent_page, grading_page
from evals.tool_calls import LOOK, TURNS
from gateway.models import ModelCall
from jobs.models import AgentFix, Job, ProducedItem, Scene, SceneStep

from .test_producer_auto_fix import remade_once  # noqa: F401
from .test_producer_broll import checked, instructed  # noqa: F401 (fixtures)
from .test_round_results import AD, exported

pytestmark = pytest.mark.django_db(transaction=True)

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "tool_answers.json").read_text())
SAID = "Scene 3's brush looks wrong to the owner; I'll look, then remake it."
LOOK_SAYS = {
    "sees": "A brush with a blue handle stirring paint.",
    "wrong": "The handle is blue; the page's brush is red.",
    "root_cause": "Image 2 shows a blue brush and the prompt names no colour.",
    "direction": "Send the red brush photo as Image 1 and name its colour.",
}


def _model_call(job: Job, purpose: str, call: ToolCall | None, output: Any, cost: str) -> None:
    ModelCall.objects.create(
        session=job.session,
        job=job,
        tool_call=call,
        purpose=purpose,
        provider="openai",
        model="gpt-5.6-sol",
        attempt=1,
        handoff={},
        output=output,
        outcome=ModelCall.Outcome.SUCCEEDED,
        cost_usd=Decimal(cost),
        duration_ms=10,
    )


def _producer_calls_after_the_first_clip(job: Job) -> tuple[ToolCall, ToolCall]:
    """A turn where the producer says SAID and calls its look and the fixture's tool; the look's
    model call answers in words; the fixture's call is folded."""
    now = timezone.now() + timedelta(seconds=1)
    assert job.session is not None
    given = FIXTURE["tool_call"]
    look = ToolCall.objects.create(
        session=job.session,
        job=job,
        agent="producer",
        tool="look",
        call_id="call_look",
        arguments={"scene": 3, "question": "What is wrong with the brush?"},
        why="I need to see the clip before I change it.",
        result="The handle is blue; the page's brush is red.",
        answer="made",
        finished_at=now,
    )
    folded = ToolCall.objects.create(
        session=job.session,
        job=job,
        agent=given["agent"],
        tool=given["tool"],
        call_id="call_after_the_clip",
        arguments=given["arguments"],
        why=given["why"],
        result=given["result"],
        answer=given["answer"],
        fold_line=FIXTURE["fold_line"],
        finished_at=now,
    )
    _model_call(
        job,
        "produce",
        None,
        {
            "says": SAID,
            "calls": [
                {"call_id": c.call_id, "tool": c.tool, "arguments": c.arguments}
                for c in (look, folded)
            ],
        },
        "0.02",
    )
    _model_call(job, LOOK, look, LOOK_SAYS, "0.03")
    # The row the fixing agent's own turns are charged to (agents.fixing_agent._turns).
    ToolCall.objects.create(
        session=job.session,
        job=job,
        agent="fixing_agent",
        tool=TURNS,
        call_id="fixing_agent-1-turns",
        arguments={},
        finished_at=now,
    )
    return look, folded


def test_the_export_keeps_every_tool_call_after_the_first_clip_with_its_why_words_and_answer(
    remade_once: SceneStep,  # noqa: F811
    tmp_path: Path,
) -> None:
    job = Job.objects.get()
    look, folded = _producer_calls_after_the_first_clip(job)
    first_clip = ProducedItem.objects.filter(kind="clip").order_by("id").first()
    assert first_clip is not None

    run = exported(tmp_path)

    calls = json.loads((run / AD / "tool_calls.json").read_text())
    ad = json.loads((run / AD / "ad.json").read_text())
    assert ad["first_clip_at"] == first_clip.created_at.isoformat()
    # The producer's calls before the first clip are not kept; the fixing agent's remake after
    # it is, and the row its own turns are charged to is not.
    assert ToolCall.objects.filter(tool="make_clip").exists()
    assert [(c["agent"], c["tool"]) for c in calls] == [
        ("fixing_agent", "film_again"),
        ("producer", "look"),
        ("producer", given_tool := FIXTURE["tool_call"]["tool"]),
    ]
    assert all(c["tool"] != TURNS for c in calls)
    seen, kept = calls[1], calls[2]
    assert (seen["why"], seen["said"], seen["answer"]) == (look.why, SAID, "made")
    assert seen["arguments"] == look.arguments
    assert seen["result"] == look.result
    assert seen["cost_usd"] == "0.030000"
    assert [m["purpose"] for m in seen["model_calls"]] == [LOOK]
    words = seen["model_calls"][0]["words"]
    assert all(said in words for said in LOOK_SAYS.values())
    assert "Root cause: " in words
    assert (kept["tool"], kept["fold_line"], kept["said"]) == (
        given_tool,
        FIXTURE["fold_line"],
        SAID,
    )
    assert folded.finished_at is not None
    assert kept["finished_at"] == folded.finished_at.isoformat()
    assert kept["created_at"] == folded.created_at.isoformat()


def test_the_ads_page_lists_calls_in_order_with_cost_time_look_and_fold_linked_from_the_round(
    remade_once: SceneStep,  # noqa: F811
    tmp_path: Path,
) -> None:
    look, _ = _producer_calls_after_the_first_clip(Job.objects.get())
    run = exported(tmp_path)

    grading_page.main([str(run)])

    grading = (run / "grading.html").read_text()
    assert f'href="{AD}/{agent_page.FILE}"' in grading
    page = (run / AD / agent_page.FILE).read_text()
    assert page.startswith("<!doctype html>")
    tools = re.findall(r'<li class="card" id="call-\d+">\s*<header><h3>\d+\. (\w+)', page)
    assert tools == ["film_again", "look", FIXTURE["tool_call"]["tool"]]
    look_card = page.split('id="call-2"')[1].split('id="call-3"')[0]
    assert look.why in look_card
    assert "$0.030000" in look_card
    assert f"started {look.created_at.isoformat().replace('T', ' ')[:19]} UTC" in look_card
    assert "The look (gpt-5.6-sol)" in look_card
    assert "Send the red brush photo as Image 1 and name its colour." in look_card
    assert "What is wrong with the brush?" in look_card
    folded_card = page.split('id="call-3"')[1]
    assert f"Folded to: {FIXTURE['fold_line']}" in folded_card
    assert "Folded to" not in look_card
    assert "after its first clip" in page and "3 calls" in page
    assert 'href="../grading.html#' in page


def test_an_ad_that_made_no_clip_keeps_every_call_and_says_so(
    remade_once: SceneStep,  # noqa: F811
    tmp_path: Path,
) -> None:
    ProducedItem.objects.filter(kind="clip").update(step=None)

    run = exported(tmp_path)

    assert json.loads((run / AD / "ad.json").read_text())["first_clip_at"] is None
    calls = json.loads((run / AD / "tool_calls.json").read_text())
    assert [c["tool"] for c in calls][:2] == ["read_page", "plan_ad"]
    assert len(calls) == ToolCall.objects.exclude(tool=TURNS).count()
    page = agent_page.page(run, AD)
    assert "no clip was made, so every call is listed" in page
    _linked_both_ways(run)


def test_a_talking_only_ad_has_its_page_linked_from_the_round(
    remade_once: SceneStep,  # noqa: F811
    tmp_path: Path,
) -> None:
    # Every scene said to camera: no B-roll scene and no B-roll clip.
    AgentFix.objects.all().delete()
    Scene.objects.update(shows="")
    ProducedItem.objects.filter(kind="clip").delete()

    run = exported(tmp_path)

    assert json.loads((run / "results.json").read_text()) == {}
    assert json.loads((run / AD / "ad.json").read_text())["first_clip_at"] is None
    assert json.loads((run / AD / "tool_calls.json").read_text())[0]["tool"] == "read_page"
    _linked_both_ways(run)


def _linked_both_ways(run: Path) -> None:
    """The round's page links the ad's page, and the ad's back link lands on its section."""
    grading_page.main([str(run)])
    grading = (run / "grading.html").read_text()
    section = grading.split(f'<section class="ad" id="{AD}">')[1].split("</section>")[0]
    assert f'href="{AD}/{agent_page.FILE}"' in section
    assert "No B-roll clip to grade." in section
    assert f'href="../grading.html#{AD}"' in (run / AD / agent_page.FILE).read_text()


def test_a_run_exported_before_the_record_has_no_link_to_a_page(
    remade_once: SceneStep,  # noqa: F811
    tmp_path: Path,
) -> None:
    run = exported(tmp_path)
    (run / AD / "tool_calls.json").unlink()

    assert agent_page.FILE not in grading_page.page(run)
    assert grading_page.ads_of(run) == []


def test_the_export_says_which_calls_couldnt_do_what_they_were_asked(
    remade_once: SceneStep,  # noqa: F811
    tmp_path: Path,
) -> None:
    job = Job.objects.get()
    _producer_calls_after_the_first_clip(job)
    now = timezone.now() + timedelta(seconds=2)

    def row(call_id: str, result: str, answer: str, finished: bool = True) -> None:
        ToolCall.objects.create(
            session=job.session,
            job=job,
            agent="producer",
            tool="make_clip",
            call_id=call_id,
            arguments={"scene": 3},
            result=result,
            answer=answer,
            finished_at=now if finished else None,
        )

    row("call_failed", "Failed: the video model couldn't make the clip (busy).", "cant")
    # Kept before rows kept their kind: their words say it.
    row("call_refused_before", "Refused: scene 3's clip is still being made.", "")
    row("call_failed_before", "Failed: the video model couldn't make the clip (busy).", "")
    row("call_asked_before", "Question: Which scene? Nothing was done.", "")
    # The kind the row keeps, whatever its words.
    row("call_ended", "The fix ended without a try that passed.", "cant")
    row("call_running", "", "", finished=False)

    run = exported(tmp_path)

    calls = json.loads((run / AD / "tool_calls.json").read_text())
    assert [
        (c["answer"], c["refused"])
        for c in calls
        if c["tool"] == "make_clip" and not c["fold_line"]
    ] == [
        ("cant", True),
        ("cant", True),
        ("cant", True),
        ("question", False),
        ("cant", False),
        ("", False),
    ]
