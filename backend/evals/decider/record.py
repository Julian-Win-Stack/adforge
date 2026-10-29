"""Keeping everything a played case did, as files the evals can read again later (Julian
2026-10-17 00:19: "those things could become the evals later"). In <out>/<case id>/:

- case.json: the case as it was given, its expected answers included.
- given.json: what the decider was given on its first turn of each message: the ad as code
  wrote it and the shop owner's messages, with the photos each message carried (their files
  in pictures/, named sent-...).
- tool_calls.json: every tool it called, in order: arguments, result, the pictures handed
  back (label and file in pictures/), refused or not, when.
- model_calls.json: every model call it took: model, the conversation given, the pictures
  shown by label, the raw answer, tokens, cost, time, failed or not.
- turns.json: per message, its outcome, the decide answer and the plan as the chat shows it.
- result.json: the above in short, with the spend, whether each decided scene was looked at
  first, and the checks against the expected answers (evals.decider.score); the run's
  results.json lists these.
- pictures/: every picture the decider was shown, named by order and label.

And in <out>/told/<id>.json, what the decider was told, once per version of it: its
instructions, its tools' descriptions and arguments, and the commit of the code; each
result names its id under "told".
"""

import hashlib
import json
import shutil
import subprocess
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from agents import decider
from agents.models import ToolCall
from chat.models import Message
from evals.tool_calls import Pictures, model_call, rows, slug, tool_call
from gateway.models import ModelCall
from jobs.models import Job

from .play import Played
from .score import score

SCENE_LOOKS = ("look_at_references", "look_at_version", "look_at_clip")
LOOKS = (*SCENE_LOOKS, "look_at_owner_photo")


def record(case: dict[str, Any], played: Played, out: Path, *, model: str) -> dict[str, Any]:
    """Write the case's folder in `out` and give back its result."""
    folder = out / case["id"]
    shutil.rmtree(folder, ignore_errors=True)
    (folder / "pictures").mkdir(parents=True)
    job = played.job
    pictures = Pictures(folder / "pictures")
    calls = [tool_call(call, pictures) for call in _calls(job)]
    model_calls = [_model_call(call) for call in _model_calls(job)]
    turns = [asdict(turn) for turn in played.turns]
    for turn in turns:
        turn["photo_files"] = [_sent(photo, folder / "pictures") for photo in turn["photos"]]
    given = [
        _given(call, turn)
        for call, turn in zip(
            [call for call in model_calls if call["first_of_its_message"]], turns, strict=False
        )
    ]
    spend = sum((Decimal(call["cost_usd"] or 0) for call in model_calls), Decimal(0))
    result = {
        "id": case["id"],
        "layer": case.get("layer", ""),
        "rule": case.get("rule", ""),
        "side": case.get("side", ""),
        "movement": case.get("movement", False),
        "ad": case.get("ad", ""),
        "model": model,
        "turns": turns,
        "looks": [
            {"tool": call["tool"], **call["arguments"], "pictures": len(call["pictures"])}
            for call in calls
            if call["tool"] in LOOKS and not call["refused"]
        ],
        "judge_findings_calls": sum(1 for call in calls if call["tool"] == "judge_findings"),
        # The owner-notes run judged no clip (its video is faked), so these findings are empty
        # unless a case's ad carries a real judge's ruling.
        "judge_findings_judged": sum(
            1
            for call in calls
            if call["tool"] == "judge_findings"
            and not call["refused"]
            and "didn't judge" not in call["result"]
        ),
        "rows": rows(calls, looks=SCENE_LOOKS, decides=decider.Decide.name),
        "refused": [
            {"tool": call["tool"], "result": call["result"]} for call in calls if call["refused"]
        ],
        "red_notices": _red_notices(job, played.turns[0].seq if played.turns else 0),
        "model_calls_failed": sum(1 for call in model_calls if call["outcome"] != "succeeded"),
        "spend_usd": str(spend),
        "started_at": model_calls[0]["created_at"] if model_calls else None,
        "finished_at": model_calls[-1]["created_at"] if model_calls else None,
        "expected": case.get("expect", []),
        "expected_by": case.get("expected_by", "claude"),
        "past": case.get("past", {}),
    }
    result["told"] = _keep_told(out)
    result["checks"] = score(case, result)
    _write(folder / "case.json", case)
    _write(folder / "given.json", given)
    _write(folder / "tool_calls.json", calls)
    _write(folder / "model_calls.json", model_calls)
    _write(folder / "turns.json", turns)
    _write(folder / "result.json", result)
    return result


def _keep_told(out: Path) -> str:
    here = Path(__file__).parent
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=here
    ).stdout.strip()
    changed = subprocess.run(
        ["git", "status", "--porcelain", "--", "agents", "evals"],
        capture_output=True,
        text=True,
        cwd=here.parent.parent,
    ).stdout.strip()
    told = {
        "instructions": decider.INSTRUCTIONS,
        "tools": [
            {
                "name": tool.name,
                "description": tool.spec().description,
                "arguments": tool.model_json_schema(),
            }
            for tool in decider.DECIDER.tools
        ],
    }
    named = hashlib.sha256(json.dumps(told, sort_keys=True).encode()).hexdigest()[:12]
    told["commit"] = commit + (" with local changes" if changed else "")
    (out / "told").mkdir(exist_ok=True)
    _write(out / "told" / f"{named}.json", told)
    return named


def _calls(job: Job) -> list[ToolCall]:
    return list(
        ToolCall.objects.filter(session=job.session, agent=decider.DECIDER_NAME).order_by(
            "created_at", "id"
        )
    )


def _model_calls(job: Job) -> list[ModelCall]:
    return list(
        ModelCall.objects.filter(session=job.session, purpose=decider.PURPOSE).order_by(
            "created_at", "id"
        )
    )


def _model_call(call: ModelCall) -> dict[str, Any]:
    conversation = call.handoff.get("conversation", [])
    return {
        **model_call(call),
        # The first turn after a message from the shop owner: the conversation ends with it.
        "first_of_its_message": bool(conversation)
        and conversation[-1].get("by") == "user"
        and conversation[-1].get("text") != decider.REMINDER,
    }


def _given(call: dict[str, Any], turn: dict[str, Any]) -> dict[str, Any]:
    conversation = call["handoff"]["conversation"]
    return {
        "the_ad": conversation[0].get("text", ""),
        "messages": [each.get("text", "") for each in conversation[1:] if each.get("by") == "user"],
        "photos": turn["photo_files"],
        "at": call["created_at"],
    }


def _sent(photo: str, folder: Path) -> str:
    """A copy of a photo the shop owner sent, in pictures/."""
    name = f"sent-{slug(Path(photo).stem)}{Path(photo).suffix}"
    shutil.copyfile(photo, folder / name)
    return f"pictures/{name}"


def _red_notices(job: Job, since: int) -> list[str]:
    """The red notices posted while the case played: the ad's own, from making its first
    version, aren't the case's."""
    assert job.session is not None
    return list(
        job.session.messages.filter(
            role=Message.Role.NOTICE, level=Message.Level.PROBLEM, seq__gte=since
        ).values_list("text", flat=True)
    )


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str))
