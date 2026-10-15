"""What a case led to, kept for reading: what was said, each fix restated, each tool called,
each step made and the prompt it was made with, the pictures made, and what it cost."""

import json
import shutil
from pathlib import Path
from typing import Any

from django.conf import settings

from agents.models import ToolCall
from jobs.models import Fix, FixCheck, ProducedItem, SceneStep

from .chat import Chat, Since, Turned, spent


def record(
    chat: Chat, start: Since, turned: Turned, case: dict[str, Any], out: Path
) -> dict[str, Any]:
    """Keep what the case led to in `out`: record.json, chat.md and each picture made."""
    out.mkdir(parents=True, exist_ok=True)
    said = [
        {
            "role": message.role,
            "text": message.text,
            "attached": [
                f"{attachment.kind} of scene {attachment.scene}"
                if attachment.scene is not None
                else ("video of the whole ad" if attachment.kind == "video" else attachment.kind)
                for attachment in message.attachments.all()
            ],
        }
        for message in chat.said_since(start.seq)
    ]
    fixes = [
        {
            "scene": fix.scene.number,
            "redo": fix.redo,
            "fix": fix.fix,
            "estimate_usd": str(fix.estimate_usd),
            "said_yes": fix.confirmed_by_id is not None,
            "checks": [_check(check) for check in fix.checks.all()],
        }
        for fix in Fix.objects.filter(pk__gt=start.fix).order_by("pk")
    ]
    calls = [
        {"tool": call.tool, "arguments": call.arguments, "result": call.result}
        for call in ToolCall.objects.filter(pk__gt=start.tool_call).order_by("pk")
    ]
    steps = []
    for step in SceneStep.objects.filter(pk__gt=start.step).order_by("pk"):
        before = (
            SceneStep.objects.filter(scene=step.scene, kind=step.kind, pk__lte=start.step)
            .order_by("pk")
            .last()
        )
        made = step.produced.order_by("pk").last()
        picture = ""
        if made is not None and made.kind == ProducedItem.Kind.STARTING_PICTURE:
            picture = f"scene-{step.scene.number}-picture-{made.version}.png"
            shutil.copy(Path(settings.MEDIA_ROOT) / made.file, out / picture)
            if before is not None and (old := before.produced.order_by("pk").last()) is not None:
                shutil.copy(
                    Path(settings.MEDIA_ROOT) / old.file,
                    out / f"scene-{step.scene.number}-picture-before.png",
                )
        steps.append(
            {
                "scene": step.scene.number,
                "kind": step.kind,
                "status": step.status,
                "note": step.note,
                "line": step.line,
                "picture_prompt": step.prompt,
                "picture_prompt_before": before.prompt if before else "",
                "motion_prompt": step.motion_prompt
                or (step.picture_step.motion_prompt if step.picture_step else ""),
                "motion_prompt_before": _motion_before(step, start),
                "picture": picture,
                "result": step.result,
            }
        )
    kept = {
        "case": case,
        "sent": turned.sent,
        "error": turned.error,
        "said": said,
        "fixes": fixes,
        "tool_calls": calls,
        "steps": steps,
        "new_ads_shown": chat.finished_ads() - start.ads,
        "spent_usd": spent(start),
    }
    (out / "record.json").write_text(json.dumps(kept, indent=1, default=str))
    (out / "chat.md").write_text(as_text(kept))
    return kept


def _check(check: FixCheck) -> dict[str, str]:
    """What the fix check found in a version the fix made. A clip's check is the fake's: the
    video was never made."""
    if check.what == "clip":
        return {"what": "clip", "verdict": "video faked: not judged", "reason": ""}
    return {"what": check.what, "verdict": check.verdict, "reason": check.reason}


def _motion_before(step: SceneStep, start: Since) -> str:
    """The video prompt of the scene's clip before the notes."""
    clip = (
        SceneStep.objects.filter(scene=step.scene, kind=SceneStep.Kind.CLIP, pk__lte=start.step)
        .order_by("pk")
        .last()
    )
    if clip is None:
        return ""
    return clip.motion_prompt or (clip.picture_step.motion_prompt if clip.picture_step else "")


def as_text(kept: dict[str, Any]) -> str:
    """The case as a person reads it."""
    case = kept["case"]
    lines = [f"# {case['id']} (ad #{case['ad']})", "", f"Wants: {case.get('wants', '')}", ""]
    for said in kept["said"]:
        who = {"user": "OWNER", "agent": "APP", "notice": "NOTICE"}[said["role"]]
        attached = f" [{', '.join(said['attached'])}]" if said["attached"] else ""
        if said["text"] or attached:
            lines.append(f"**{who}:** {said['text']}{attached}")
            lines.append("")
    lines.append("## Fixes restated")
    lines += [
        f"- scene {fix['scene']} {fix['redo']}: {fix['fix']} (about ${fix['estimate_usd']}, "
        f"{'yes' if fix['said_yes'] else 'no yes'})"
        + "".join(
            f"\n  - {check['what']} check: {check['verdict']}"
            + (f" ({check['reason']})" if check["reason"] else "")
            for check in fix["checks"]
        )
        for fix in kept["fixes"]
    ] or ["- none"]
    lines += ["", "## Steps made"]
    for step in kept["steps"]:
        lines.append(
            f"- scene {step['scene']} {step['kind']} ({step['status']}) note: {step['note']!r}"
        )
        if step["kind"] == "starting_picture" and step["picture_prompt"]:
            lines.append(f"  - picture prompt: {step['picture_prompt']}")
        if step["kind"] == "clip" and step["motion_prompt"] != step["motion_prompt_before"]:
            lines.append(f"  - video prompt before: {step['motion_prompt_before']}")
            lines.append(f"  - video prompt now: {step['motion_prompt']}")
    lines += ["", f"New ads shown: {kept['new_ads_shown']}", f"Error: {kept['error'] or 'none'}"]
    return "\n".join(lines) + "\n"
