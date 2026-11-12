"""Save grading records, adapted from the grading-page skill's extract.py.

The runner calls this while the scenario database and file store are installed. Rendering
the saved page later needs neither that database nor a model. Media keys in the rich
records refer to the scenario's durable media/ folder, copied by the runner.
"""

import json
import os
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .store import atomic_write_text, read_json, write_json

if TYPE_CHECKING:
    from agents.models import ToolCall
    from gateway.models import ModelCall
    from jobs.models import Job, ProducedItem


def _call(call: ToolCall, said: dict[str, str]) -> dict[str, Any]:
    return {
        "id": call.pk,
        "agent": call.agent,
        "tool": call.tool,
        "args": call.arguments,
        "why": call.why,
        "said": said.get(call.call_id, ""),
        "result": call.result,
        "answer": call.answer,
        "fold": call.fold_line,
        "pictures": call.pictures,
        "cost": float(sum((m.cost_usd or 0) for m in call.model_calls.all())),
        "at": call.created_at.isoformat(),
        "done": call.finished_at.isoformat() if call.finished_at else None,
    }


def _model(call: ModelCall) -> dict[str, Any]:
    return {
        "purpose": call.purpose,
        "model": call.model,
        "provider": call.provider,
        "cost": float(call.cost_usd or 0),
        "outcome": call.outcome,
        "handoff": call.handoff,
        "images": call.images,
    }


def _produced(item: ProducedItem, calls: list[ModelCall]) -> dict[str, Any]:
    record: dict[str, Any] = {
        "pk": item.pk,
        "kind": item.kind,
        "version": item.version,
        "remake": item.remake,
        "file": item.file,
        "seconds": item.seconds,
        "text": item.text,
        "motion_prompt": item.motion_prompt,
        "sent": item.sent,
        "ruling": item.ruling,
        "change": item.change,
        "created_at": item.created_at.isoformat(),
        "picture": {"pk": item.picture_id, "file": item.picture.file}
        if item.picture_id and item.picture
        else None,
        "made_from": {"pk": item.made_from_id, "file": item.made_from.file}
        if item.made_from_id and item.made_from
        else None,
        "image": None,
        "make": None,
    }
    image = next(
        (
            call
            for call in reversed(calls)
            if call.purpose in ("draw_scene_picture", "make_end_picture")
            and (call.output or {}).get("file") == item.file
        ),
        None,
    )
    if image is not None:
        record["image"] = {
            **_model(image),
            "prompt": image.handoff.get("prompt"),
            "pictures": image.handoff.get("pictures", []),
        }
    if item.kind == "clip":
        prompt = (item.sent or {}).get("prompt")
        # Bringing a take back creates another ProducedItem with the same immutable file.
        # Match when those bytes first appeared, before any later try with the same prompt.
        born = (
            item.job.produced.filter(kind="clip", file=item.file)
            .order_by("created_at", "pk")
            .values_list("created_at", flat=True)
            .first()
        )
        make = next(
            (
                call
                for call in reversed(calls)
                if call.purpose in ("make_broll_clip", "make_talking_clip")
                and call.created_at <= (born or item.created_at)
                and call.outcome == "succeeded"
                and (
                    not item.picture
                    or item.picture.file
                    in [
                        call.handoff.get("starting_picture"),
                        *(call.handoff.get("example_pictures") or []),
                    ]
                )
                and (
                    (prompt and call.handoff.get("prompt") == prompt)
                    or ((call.output or {}).get("file") == item.file)
                )
            ),
            None,
        )
        if make is not None:
            record["make"] = _model(make)
    return record


def export_job(
    job: Job,
    base: Path,
    *,
    scenario: dict[str, Any] | None = None,
    notes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Write rich records and the shared regression.json alongside a round export."""
    from adforge import file_store
    from agents.models import ToolCall
    from evals.tool_calls import said_before
    from gateway.models import ModelCall
    from jobs.models import AgentFix, ProducedItem
    from jobs.stale import current_clip

    base.mkdir(parents=True, exist_ok=True)
    calls = list(ModelCall.objects.filter(job=job).order_by("created_at", "pk"))
    tools = list(
        ToolCall.objects.filter(session=job.session)
        .exclude(tool="take_turn")
        .prefetch_related("model_calls")
        .order_by("created_at", "pk")
    )
    said = said_before(job.session) if job.session else {}

    def keep(file: str, relative: str) -> str:
        target = base / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(file_store.read(file))
        return relative

    page_files = []
    if job.page_text:
        atomic_write_text(base / "page.txt", job.page_text)
        page_files.append("page.txt")
    for photo in job.library.filter(number__in=job.product_photos).order_by("number"):
        if photo.file:
            page_files.append(
                keep(photo.file, f"page-photo-{photo.number}{Path(photo.file).suffix}")
            )
    scenes = []
    rich_scenes = []
    results = read_json(base.parent / "results.json")
    identity = scenario or {
        "key": base.name,
        "kind": "full",
        "link": job.product_url,
        "situation": "",
    }
    for scene in job.scenes.order_by("number"):
        rows = list(
            scene.produced.select_related("picture", "made_from").order_by("created_at", "pk")
        )
        clips = [_produced(p, calls) for p in rows if p.kind == ProducedItem.Kind.CLIP]
        pictures = [
            _produced(p, calls)
            for p in rows
            if p.kind in (ProducedItem.Kind.STARTING_PICTURE, ProducedItem.Kind.END_PICTURE)
        ]
        clip = current_clip(scene)
        picture = scene.current_picture() or (clip.picture if clip and clip.picture_id else None)
        simple: dict[str, Any] = {
            "number": scene.number,
            "line": scene.line,
            "shows": scene.shows,
        }
        folder = f"scene-{scene.number}"
        (base / folder).mkdir(exist_ok=True)
        atomic_write_text(
            base / folder / "inputs.txt",
            f"SCENE: {scene.number}\nLINE: {scene.line}\nSHOWS: {scene.shows}\n"
            f"KIND: {scene.kind}\nKIND_WHY: {scene.kind_why}\n",
        )
        if picture:
            simple["picture"] = keep(
                picture.file, f"{folder}/start-picture{Path(picture.file).suffix or '.png'}"
            )
        kept = None
        for number, record in enumerate(clips):
            record["remake"] = number or None
            filename = "first.mp4" if number == 0 else f"remake-{number}.mp4"
            relative = keep(record["file"], f"{folder}/{filename}")
            write_json(base / folder / f"ruling-{Path(filename).stem}.json", record["ruling"])
            record["export_file"] = relative
            if clip and record["pk"] == clip.pk:
                simple["clip"] = relative
                kept = record
        scenes.append(simple)
        fixes = list(
            AgentFix.objects.filter(step__scene=scene).prefetch_related("scene_runs").order_by("pk")
        )
        fix_ids = {fix.pk for fix in fixes}
        scene_tools = [_call(t, said) for t in tools if t.agent_fix_id in fix_ids]
        rich_scenes.append(
            {
                "number": scene.number,
                "part": scene.part,
                "kind": scene.kind,
                "kind_why": scene.kind_why,
                "broll_kind": scene.broll_kind,
                "line": scene.line,
                "shows": scene.shows,
                "current": clip.pk if clip else None,
                "current_picture": picture.pk if picture else None,
                "pictures": pictures,
                "clips": clips,
                "agent": scene_tools,
                "fixes": [
                    {
                        "gave_up": fix.gave_up,
                        "runs": list(fix.scene_runs.values()),
                    }
                    for fix in fixes
                ],
            }
        )
        results[f"{identity['key']}:{scene.number}"] = {
            **results.get(f"{identity['key']}:{scene.number}", {}),
            "ad_folder": identity["key"],
            "scene": scene.number,
            "line": scene.line,
            "shows": scene.shows,
            "kind": scene.kind,
            "kind_why": scene.kind_why,
            "broll_kind": scene.broll_kind,
            "tries": clips,
            "kept": kept,
            "kept_is_remake": kept is not None and kept["remake"] is not None,
            "gave_up": next((fix.gave_up for fix in reversed(fixes) if fix.gave_up), None),
        }
    ads = list(job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).order_by("version"))
    if ads:
        keep(ads[0].file, "ad.mp4")
        if len(ads) > 1:
            keep(ads[-1].file, "ad-latest.mp4")
    exported_notes = []
    for number, note in enumerate(notes or [], 1):
        n = note.get("number", number)
        exported = {"number": n, "text": note.get("text", note.get("note", ""))}
        for when in ("before", "after"):
            if source := note.get(when):
                if (base / source).is_file():
                    exported[when] = source
                else:
                    exported[when] = keep(source, f"note-{n}/{when}.mp4")
        exported_notes.append(exported)
    costs: defaultdict[str, float] = defaultdict(float)
    for call in calls:
        costs[call.purpose] += float(call.cost_usd or 0)
    rich = {
        "session": str(job.session_id or ""),
        "url": job.product_url,
        "product": job.product_name,
        "product_colour": job.product_colour,
        "product_size": job.product_size,
        "behaviour": job.product_behaviour,
        "page_text": job.page_text,
        "scenes": rich_scenes,
        "costs": dict(costs),
        "warnings": job.warnings,
        "messages": list(job.session.messages.values("seq", "role", "text")) if job.session else [],
        "shared": list(job.produced.filter(scene__isnull=True).values("kind", "file", "version")),
        "library": list(job.library.order_by("number").values()),
        "videos": list(job.videos.order_by("position").values()),
        "producer": [_call(t, said) for t in tools if t.agent != "scene_agent"],
        "trace": (
            os.environ["LANGFUSE_SESSIONS_URL"].rstrip("/") + "/" + str(job.session_id)
            if os.environ.get("LANGFUSE_SESSIONS_URL") and job.session_id
            else None
        ),
    }
    simple = {
        "scenario": identity,
        "scenes": scenes,
        "notes": exported_notes,
        "page_files": page_files,
    }
    # Datetimes and Decimal fields in raw record lists are JSON strings, as in the skill.
    write_json(base / "grading-records.json", json.loads(json.dumps(rich, default=str)))
    write_json(base / "regression.json", simple)
    write_json(base.parent / "results.json", results)
    return simple
