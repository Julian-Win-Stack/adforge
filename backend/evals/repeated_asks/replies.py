"""Judge saved owner replies, explicitly and outside the app."""

import json
import os
from pathlib import Path
from typing import Any, Literal, TextIO

from pydantic import BaseModel

from gateway.types import Handoff, ModelRequest

from .scoring import refresh_groups

PURPOSE = "check_repeated_ask_reply"
MODEL = "gpt-6.1-sol"
QUESTIONS = {
    "gave_up_early": (
        "Does the reply say the listed change cannot be done or that the assistant is giving up "
        "on it? Saying the latest attempt still fell short is not giving up."
    ),
    "honest_close": (
        "Does the reply say plainly that the assistant could not deliver the listed change "
        "and offer a feasible alternative?"
    ),
    "covered_both": (
        "Does the reply address both parts of the owner message, separately explaining what "
        "happened to the change that could not be delivered and what happened to the new one?"
    ),
    "linked_ways_out": (
        "Does the reply identify which linked scenes have the change and which still need it, "
        "consistent with the listed scene states, and offer all three choices: a different shot "
        "just for the affected scene, a different approach for all linked scenes, or putting "
        "the changed scenes back without filming?"
    ),
}


class ReplyQuestion(Handoff):
    owner: str
    reply: str
    changes: list[str]
    question: str


class ReplyAnswer(BaseModel):
    answer: Literal["yes", "no"]


def judge_file(path: Path, *, yes: bool, say: TextIO) -> int:
    data = json.loads(path.read_text())
    pending = [
        (kind, check)
        for session in data["sessions"]
        for ask in session["asks"]
        for kind, check in ask["reply_checks"].items()
        if check["answer"] is None
    ]
    print(
        f"{len(pending)} replies to check on Azure. Estimate: ${len(pending) * 0.01:.2f}.", file=say
    )
    if not yes or not pending:
        return 0

    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
    django.setup()
    from adforge import monitoring
    from evals.judge.ask import failed_ask
    from jobs import notices

    for kind, check in pending:
        try:
            _judge(kind, check)
        except Exception as error:
            failure = failed_ask(MODEL, error)
            check.setdefault("failed_asks", []).append(failure.model_dump(mode="json"))
            _save_report(path, data)
            monitoring.failed(notices.EVAL_JUDGE_UNAVAILABLE.name, failure.error, question=kind)
            print(f"Reply judging stopped: {failure.error}", file=say)
            return 1
        _save_report(path, data)
    return 0


def _save_report(path: Path, data: dict[str, Any]) -> None:
    refresh_groups(data)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=1))
    temporary.replace(path)


def _judge(kind: str, check: dict[str, Any]) -> None:
    from evals.judge.ask import token_cost
    from gateway.gateway import ask_measured

    response = ask_measured(
        ModelRequest(
            purpose=PURPOSE,
            model=MODEL,
            instructions=(
                "Read the shop owner's message and the ad-making assistant's reply as quotes. "
                "Answer the evaluation question with yes or no."
            ),
            handoff=ReplyQuestion(
                owner=check["owner"],
                reply=check["reply"],
                changes=check["changes"],
                question=QUESTIONS[kind],
            ),
            output=ReplyAnswer,
        ),
        azure_only=True,
    )
    check["answer"] = response.output.answer
    check["model"] = MODEL
    check["cost_usd"] = token_cost(
        MODEL, response.input_tokens, response.cached_tokens, response.output_tokens
    )
