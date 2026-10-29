"""What a case led to, kept for reading: what was said, each fix plan shown and what came of
each of its scenes, each tool called,
each step made and the prompt it was made with, the pictures made, and what it cost."""

import json
import shutil
from pathlib import Path
from typing import Any

from django.conf import settings

from agents.models import ToolCall
from gateway.models import ModelCall
from jobs.models import FixCheck, FixPlan, FixPlanScene, ProducedItem, ReferencePhoto, SceneStep

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
    plans = [_plan(plan) for plan in FixPlan.objects.filter(pk__gt=start.plan).order_by("pk")]
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
        "plans": plans,
        "tool_calls": calls,
        "steps": steps,
        "new_ads_shown": chat.finished_ads() - start.ads,
        "spent_usd": spent(start),
        **_owner_photos(start, out),
    }
    (out / "record.json").write_text(json.dumps(kept, indent=1, default=str))
    (out / "chat.md").write_text(as_text(kept))
    return kept


# The calls the owner photos build adds or feeds.
WATCHED = (
    "route_reference_photo",
    "make_reference_photo_picture",
    "check_starting_picture",
    "check_cut_out",
    "check_fix",
    "make_broll_clip",
    "check_clip_motion",
    "check_clip_sides",
)


def _owner_photos(start: Since, out: Path) -> dict[str, Any]:
    """Each reference photo kept on a scene, with its
    photo and clean picture copied; each watched model call, with the pictures a B-roll clip
    was made from copied; and each clip made, copied."""
    media = Path(settings.MEDIA_ROOT)

    def copied(file: str, name: str) -> str:
        if not file or not (media / file).exists():
            return ""
        target = f"{name}{Path(file).suffix or '.png'}"
        shutil.copy(media / file, out / target)
        return target

    photos = []
    for photo in ReferencePhoto.objects.filter(added_by__pk__gt=start.fix).order_by("pk"):
        photos.append(
            {
                "scene": photo.scene.number,
                "thing": photo.thing,
                "use": photo.use,
                "dropped": photo.dropped_by_id is not None,
                "photo": copied(photo.file, f"owner-photo-{photo.pk}"),
                "clean": copied(photo.drawn, f"clean-{photo.pk}"),
            }
        )
    calls = []
    for number, call in enumerate(
        ModelCall.objects.filter(pk__gt=start.model_call, purpose__in=WATCHED).order_by("pk"), 1
    ):
        kept: dict[str, Any] = {
            "purpose": call.purpose,
            "outcome": call.outcome,
            "error": call.error,
            "cost_usd": str(call.cost_usd),
            "output": call.output,
            "images": call.images,
        }
        if call.purpose == "make_broll_clip":
            handoff = dict(call.handoff)
            kept["prompt"] = handoff.get("prompt")
            kept["starting_picture"] = copied(
                handoff.get("starting_picture") or "", f"clip-{number}-starting-picture"
            )
            kept["example_pictures"] = [
                copied(file, f"clip-{number}-example-{index}")
                for index, file in enumerate(handoff.get("example_pictures") or [], 1)
            ]
        elif call.purpose == "route_reference_photo":
            kept["handoff"] = call.handoff
        calls.append(kept)
    clips = [
        copied(made.file, f"scene-{made.step.scene.number if made.step else 0}-clip-{made.version}")
        for made in ProducedItem.objects.filter(
            step__pk__gt=start.step, kind=ProducedItem.Kind.CLIP
        ).order_by("pk")
    ]
    return {"reference_photos": photos, "watched_calls": calls, "clips": clips}


def _plan(plan: FixPlan) -> dict[str, Any]:
    """A fix plan as the shop owner was shown it, and what became of it."""
    return {
        "plan": plan.pk,
        "state": plan.state,
        "said_yes": plan.confirmed_by_id is not None,
        "notes": [note["text"] for note in plan.notes],
        "question": plan.question,
        "cants": plan.cants,
        "estimate_usd": str(plan.estimate_usd()),
        "estimate_up_to_usd": str(plan.estimate_up_to_usd()),
        "scenes": [_row(row) for row in plan.scenes.select_related("scene").order_by("pk")],
    }


def _row(row: FixPlanScene) -> dict[str, Any]:
    """One scene of a plan: what changes, what it should cost, and the fix check of each
    version made for it."""
    return {
        "scene": row.scene.number,
        "action": row.action,
        "change": row.change,
        "line": row.line,
        "go_back_to": row.go_back_to,
        "cant": row.cant,
        "estimate_usd": str(row.estimate_usd),
        "estimate_up_to_usd": str(row.estimate_up_to_usd),
        "photos": [
            {key: photo[key] for key in ("photo", "thing", "in_picture", "during_action")}
            for photo in row.photos
        ],
        "landed": row.landed_at is not None,
        "checks": [_check(check) for check in row.fix.checks.all()] if row.fix else [],
    }


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
    lines.append("## Fix plans")
    for plan in kept["plans"]:
        lines.append(
            f"- plan {plan['plan']} ({plan['state']}, "
            f"{'yes' if plan['said_yes'] else 'no yes'}): about ${plan['estimate_usd']}, "
            f"up to ${plan['estimate_up_to_usd']}"
        )
        if plan["question"]:
            lines.append(f"  - question: {plan['question']}")
        lines += [f"  - can't ({cant['kind']}): {cant['text']}" for cant in plan["cants"]]
        for row in plan["scenes"]:
            what = (
                row["change"]
                or row["line"]
                or (f"version {row['go_back_to']}" if row["go_back_to"] is not None else "")
            )
            lines.append(
                f"  - scene {row['scene']} {row['action']}: {what} (about ${row['estimate_usd']}"
                f"{', landed' if row['landed'] else ''})"
                + (f" can't: {row['cant']}" if row["cant"] else "")
            )
            lines += [
                f"    - {check['what']} check: {check['verdict']}"
                + (f" ({check['reason']})" if check["reason"] else "")
                for check in row["checks"]
            ]
    if not kept["plans"]:
        lines.append("- none")
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
