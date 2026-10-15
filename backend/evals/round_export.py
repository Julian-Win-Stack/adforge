"""Copy a graded round's ads out of the app into a run folder in the record shape
evals.results reads (see its docstring): results.json (the judge and the auto-redo per B-roll
scene), costs.json, run.json, and per ad its finished ad and each B-roll scene's first clip,
remakes and the judge's ruling on each. Run right after the ads finish (the file store's keys
are shared across jobs). From backend/:

    uv run python -m evals.round_export <ads.json> <run folder> [<run name>]

ads.json is {"<ad folder>": "<chat session id>", ...}: one ad per session, its newest job.

A scene's FIRST clip is the first clip its first clip step made (not one made from an owner's
note); its KEPT clip is the one the auto-redo kept for that step, the scene's clip unless an
owner's note made another later.
"""

import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

BROLL_MADE = ("make_broll_clip", "collect_broll_clip")
VIDEO_PROVIDERS = ("creatify", "heygen")


def _clip_name(remake: int | None) -> str:
    return "first" if remake is None else f"remake-{remake}"


def _clip(clip: Any) -> dict[str, Any]:
    from jobs.clip_judge import still_failing

    ruling = clip.ruling or {}
    return {
        "pk": clip.pk,
        "version": clip.version,
        "remake": clip.remake,
        "file": clip.file,
        "seconds": clip.seconds,
        "motion_prompt": clip.motion_prompt,
        "ruling": clip.ruling,
        # What the judge saw wrong, as the producer is told it: the problems of its failed checks.
        "judge_saw": list(dict.fromkeys(still_failing(ruling))) if "checks" in ruling else [],
    }


def _redo_cost(step: Any) -> float:
    """What the judge and the remakes cost on a clip step: its model calls, less making and
    collecting its first clip."""
    calls = list(step.tool_call.model_calls.order_by("id")) if step.tool_call else []
    first_made: set[int] = set()
    for purpose in BROLL_MADE:
        made = next((c.pk for c in calls if c.purpose == purpose), None)
        if made is not None:
            first_made.add(made)
    return float(
        sum((c.cost_usd or Decimal(0) for c in calls if c.pk not in first_made), Decimal(0))
    )


def export_ad(job: Any, folder: str, out: Path) -> dict[str, dict[str, Any]]:
    """Write one ad's files under out/<folder>/; gives its results.json entries."""
    from adforge import file_store
    from jobs.models import ProducedItem, SceneStep

    base = out / folder
    base.mkdir(parents=True, exist_ok=True)
    ad = job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).order_by("-version").first()
    if ad is not None:
        (base / "ad.mp4").write_bytes(file_store.read(ad.file))
    results: dict[str, dict[str, Any]] = {}
    for scene in job.scenes.order_by("number"):
        if not scene.shows:
            continue
        step = (
            SceneStep.objects.filter(scene=scene, kind=SceneStep.Kind.CLIP, fix__isnull=True)
            .order_by("id")
            .first()
        )
        made = (
            list(step.produced.filter(kind=ProducedItem.Kind.CLIP).order_by("id")) if step else []
        )
        if step is None or not made:
            continue
        first = made[0]
        tries = [first, *sorted((c for c in made if c.remake), key=lambda c: c.remake or 0)]
        kept = next(c for c in tries if c.file == made[-1].file)
        where = base / f"scene-{scene.number}"
        where.mkdir(exist_ok=True)
        for clip in tries:
            name = _clip_name(clip.remake)
            (where / f"{name}.mp4").write_bytes(file_store.read(clip.file))
            (where / f"ruling-{name}.json").write_text(json.dumps(clip.ruling, indent=1))
        (where / "inputs.txt").write_text(
            f"SCENE: {scene.number}\nLINE: {scene.line}\nSHOWS: {scene.shows}\n"
            f"BROLL_KIND: {scene.broll_kind}\nMOTION PROMPT: {step.motion_prompt}\n"
        )
        results[f"{folder}:{scene.number}"] = {
            "status": "done",
            "ad_folder": folder,
            "scene": scene.number,
            "line": scene.line,
            "shows": scene.shows,
            "broll_kind": scene.broll_kind,
            "first": _clip(first),
            "tries": [_clip(c) for c in tries],
            "kept": _clip(kept),
            "kept_is_remake": kept.remake is not None,
            "costs": {"total": _redo_cost(step)},
        }
    return results


def _costs(job: Any) -> dict[str, float]:
    calls = job.model_calls.all()
    return {
        "total_usd": float(sum((c.cost_usd or Decimal(0) for c in calls), Decimal(0))),
        "video_usd": float(
            sum(
                (c.cost_usd or Decimal(0) for c in calls if c.provider in VIDEO_PROVIDERS),
                Decimal(0),
            )
        ),
    }


def export_run(ads: dict[str, str], out: Path, name: str) -> None:
    """Every ad of `ads` ({ad folder: chat session id}) into the run folder `out`."""
    from jobs.models import Job

    out.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict[str, Any]] = {}
    costs: dict[str, dict[str, float]] = {}
    for folder, session in ads.items():
        job = Job.objects.filter(session_id=session).order_by("-created_at").first()
        if job is None:
            print(f"{folder}: no ad made in session {session}", file=sys.stderr)
            continue
        results.update(export_ad(job, folder, out))
        costs[folder] = _costs(job)
    (out / "results.json").write_text(json.dumps(results, indent=1, default=str))
    (out / "costs.json").write_text(
        json.dumps({"source": "the app's ModelCall records, per job", "ads": costs}, indent=1)
    )
    run_file = out / "run.json"
    if not run_file.exists():
        run_file.write_text(
            json.dumps(
                {
                    "name": name,
                    "ad_grade_is": "kept",
                    "grades": "grades.json",
                    "judge": "results.json",
                    "taps": None,
                    "costs": "costs.json",
                    "ad_folders": {},
                },
                indent=1,
            )
        )


def main(args: list[str]) -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
    import django

    django.setup()
    ads_file, out = Path(args[0]), Path(args[1])
    export_run(json.loads(ads_file.read_text()), out, args[2] if len(args) > 2 else out.name)


if __name__ == "__main__":
    main(sys.argv[1:])
