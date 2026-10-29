"""Copy a graded round's ads out of the app into a run folder in the record shape
evals.results reads (see its docstring). Run right after the ads finish (the file store's keys
are shared across jobs). From backend/:

    uv run python -m evals.round_export <ads.json> <run folder> [<run name>]

ads.json is {"<ad folder>": "<chat session id>", ...}: one ad per session, its newest job. An
ad folder is letters, digits and - _ . only (it names the grading page's documents).

What it writes:
- results.json: the judge and the auto-redo per B-roll scene, keyed "<ad folder>:<N>".
- costs.json: every ad's model calls, all and paid video (the app's ModelCall records).
- run.json, unless one is there: the run's files, its whole-ad grades about the auto-redo's.
- <ad folder>/ad.mp4: the FIRST finished ad, the one made with the clips the auto-redo kept
  (graded whole); ad-latest.mp4 too when owner notes made newer versions.
- <ad folder>/ad.json: product link, app commit, when it started and was first finished
  (wall time), abandoned (no finished ad), model calls that failed (refusals, filters).
- <ad folder>/notes.json: each owner note, its restatement, estimate, yes, and fix check.
- <ad folder>/tool_calls.json: what the agents did after the ad's first clip (spec #247 point
  17), in call order, in the shape of evals.tool_calls: each tool call of the session made
  after it (the rows agents' own turns are charged to left out) with the agent, tool, `why`,
  the instruction given, the words said before it, the answer, its fold line, cost, started
  and finished, and the look's answers in words; the pictures it handed back are copied to
  <ad folder>/tool-call-pictures/. ad.json's first_clip_at names the cut; with no clip made,
  it is null and every call is kept. evals.agent_page makes the ad's page from it.
- <ad folder>/library.txt: the job's library as a text list, one line per item (its number,
  what it shows, its colour and where it came from), so a round can be read without the chat.
- each clip's record in results.json carries "sent", what cut-and-send sent the video model
  (the shape of jobs.library.SentList: the pictures with labels and library numbers, the
  pictures left out with their tier as the reason, the pieces with their video, moment,
  seconds and trim, the recolours and their outcome, the prompt), or null when it was made
  before cut-and-send.
- <ad folder>/scene-N/: first.mp4, remake-K.mp4, ruling-<clip>.json, start-picture.png and
  inputs.txt (line, shows, kind, usage, result, picture prompt, motion prompt).

A scene's FIRST clip is the first clip of its first clip step that made one (not one made from
an owner's note); its KEPT clip is the one the auto-redo kept for that step. A B-roll scene
with no such clip is left out and named on stderr.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.db.models import Q

BROLL_MADE = ("make_broll_clip", "collect_broll_clip")
VIDEO_PROVIDERS = ("creatify", "heygen")
SAFE_FOLDER = re.compile(r"^[A-Za-z0-9_.-]+$")


def clip_name(remake: int | None) -> str:
    """A clip's file name in its scene folder, without .mp4: "first" or "remake-K"."""
    return "first" if remake is None else f"remake-{remake}"


def check_folder(folder: str) -> str:
    """`folder`, refused unless it's safe as a file name and a page document id."""
    if not SAFE_FOLDER.match(folder):
        raise ValueError(f"Ad folder {folder!r}: use letters, digits and - _ . only")
    return folder


def _dollars(calls: Any) -> float:
    return float(sum((c.cost_usd or Decimal(0) for c in calls), Decimal(0)))


def _clip(clip: Any) -> dict[str, Any]:
    from jobs.clip_judge import still_failing
    from jobs.library import sent_list_of

    ruling = clip.ruling or {}
    sent = sent_list_of(clip)
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
        # What cut-and-send sent the video model (jobs.library.SentList); None when made before.
        "sent": sent.model_dump(mode="json") if sent is not None else None,
    }


def _redo_cost(step: Any) -> float:
    """What the judge and the remakes cost on a clip step: its model calls, less making and
    collecting its first clip."""
    from gateway.models import ModelCall

    calls = list(
        ModelCall.objects.filter(
            Q(tool_call=step.tool_call) | Q(tool_call__agent_fix__step=step)
        ).order_by("id")
    )
    first_made = {
        made
        for purpose in BROLL_MADE
        if (made := next((c.pk for c in calls if c.purpose == purpose), None)) is not None
    }
    return _dollars(c for c in calls if c.pk not in first_made)


def _first_clip_step(scene: Any) -> tuple[Any, list[Any]] | None:
    """The scene's first clip step not made from an owner's note that made a clip, and its
    clips oldest first; a step that failed before making one (and was asked again) is passed."""
    from jobs.models import ProducedItem, SceneStep

    steps = SceneStep.objects.filter(
        scene=scene, kind=SceneStep.Kind.CLIP, fix__isnull=True
    ).exclude(tool_call__agent="fixing_agent")
    for step in steps.order_by("id"):
        # The fixing agent films each try in a step of its own, charged to the agent's call.
        made = list(
            ProducedItem.objects.filter(kind=ProducedItem.Kind.CLIP, scene=scene)
            .filter(Q(step=step) | Q(step__tool_call__agent_fix__step=step))
            .order_by("id")
        )
        if made:
            return step, made
    return None


def _first_clip(job: Any) -> datetime | None:
    """When the ad's first clip was made: the earliest first clip of its scenes."""
    made = [found[1][0].created_at for s in job.scenes.all() if (found := _first_clip_step(s))]
    return min(made, default=None)


def _agent_calls(job: Any, after: datetime | None, base: Path) -> list[dict[str, Any]]:
    """The records of the session's tool calls made after `after` (all of them for None)."""
    from evals.tool_calls import Pictures, said_before, session_calls, tool_call

    if job.session is None:
        return []
    said = said_before(job.session)
    pictures = Pictures(base / "tool-call-pictures")
    return [
        tool_call(call, pictures, said.get(call.call_id, ""))
        for call in session_calls(job.session)
        if after is None or call.created_at > after
    ]


def _inputs(scene: Any, step: Any) -> str:
    picture_prompt = step.picture_step.prompt if step.picture_step else ""
    return (
        f"SCENE: {scene.number}\nLINE: {scene.line}\nSHOWS: {scene.shows}\n"
        f"BROLL_KIND: {scene.broll_kind}\nPERSON_SHOWN: {scene.person_shown}\n"
        f"USAGE: {scene.usage}\nRESULT: {scene.result}\nNEEDS: {scene.needs}\n"
        f"PICTURE PROMPT: {picture_prompt}\nMOTION PROMPT: {step.motion_prompt}\n"
    )


def _notes(job: Any) -> list[dict[str, Any]]:
    """Each owner note on the ad: his words, the restatement, the redo and what it cost to
    estimate, whether he said yes, and the fix check on what it made."""
    notes = []
    for fix in job.fixes.select_related("scene").order_by("id"):
        checks = [
            {"verdict": check.verdict, "reason": check.reason}
            for check in fix.checks.order_by("id")
        ]
        notes.append(
            {
                "scene": fix.scene.number,
                "notes": fix.notes,
                "fix": fix.fix,
                "redo": fix.redo,
                "estimate_usd": float(fix.estimate_usd),
                "yes": fix.confirmed_by_id is not None,
                "fix_checks": checks,
            }
        )
    return notes


def export_ad(job: Any, folder: str, out: Path, commit: str = "") -> dict[str, dict[str, Any]]:
    """Write one ad's files under out/<folder>/; gives its results.json entries."""
    from adforge import file_store
    from jobs.models import ProducedItem

    base = out / check_folder(folder)
    base.mkdir(parents=True, exist_ok=True)
    ads = list(job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).order_by("version"))
    if ads:
        (base / "ad.mp4").write_bytes(file_store.read(ads[0].file))
        if len(ads) > 1:
            (base / "ad-latest.mp4").write_bytes(file_store.read(ads[-1].file))
    failed = job.model_calls.exclude(outcome="succeeded").order_by("id")
    first_clip = _first_clip(job)
    (base / "tool_calls.json").write_text(
        json.dumps(_agent_calls(job, first_clip, base), indent=1, ensure_ascii=False)
    )
    (base / "ad.json").write_text(
        json.dumps(
            {
                "product_url": job.product_url,
                "app_commit": commit,
                "started_at": job.created_at.isoformat(),
                "first_finished_at": ads[0].created_at.isoformat() if ads else None,
                "wall_seconds": round((ads[0].created_at - job.created_at).total_seconds())
                if ads
                else None,
                "abandoned": not ads,
                "first_clip_at": first_clip.isoformat() if first_clip else None,
                "finished_versions": len(ads),
                "failed_model_calls": [
                    {"purpose": c.purpose, "model": c.model, "error": c.error} for c in failed
                ],
            },
            indent=1,
        )
    )
    (base / "notes.json").write_text(json.dumps(_notes(job), indent=1))
    from jobs.library_items import library_text

    (base / "library.txt").write_text(library_text(job) + "\n" if job.library.exists() else "")

    from jobs.models import AgentFix

    results: dict[str, dict[str, Any]] = {}
    for scene in job.scenes.order_by("number"):
        # A scene the fixing agent gave up on before 2026-10-23 is said to camera since, its
        # tries kept.
        if not scene.shows and not AgentFix.objects.filter(step__scene=scene).exists():
            continue
        found = _first_clip_step(scene)
        if found is None:
            print(f"{folder} scene {scene.number}: no B-roll clip made, left out", file=sys.stderr)
            continue
        step, made = found
        first = made[0]
        tries = [first, *sorted((c for c in made if c.remake), key=lambda c: c.remake or 0)]
        gave_up = AgentFix.objects.filter(step=step, passed__isnull=True).first()
        # A give-up keeps its last try since 2026-10-23; one before said the scene to camera,
        # keeping nothing.
        said = gave_up is not None and gave_up.said_to_camera
        kept = None if said else next(c for c in tries if c.file == made[-1].file)
        where = base / f"scene-{scene.number}"
        where.mkdir(exist_ok=True)
        for clip in tries:
            name = clip_name(clip.remake)
            (where / f"{name}.mp4").write_bytes(file_store.read(clip.file))
            (where / f"ruling-{name}.json").write_text(json.dumps(clip.ruling, indent=1))
        if step.picture:
            suffix = Path(step.picture.file).suffix or ".png"
            (where / f"start-picture{suffix}").write_bytes(file_store.read(step.picture.file))
        (where / "inputs.txt").write_text(_inputs(scene, step))
        results[f"{folder}:{scene.number}"] = {
            "ad_folder": folder,
            "scene": scene.number,
            "line": scene.line,
            "shows": step.shows,
            "broll_kind": step.broll_kind,
            "tries": [_clip(c) for c in tries],
            "kept": _clip(kept) if kept is not None else None,
            "kept_is_remake": kept is not None and kept.remake is not None,
            "gave_up": gave_up.gave_up if gave_up is not None else None,
            "costs": {"total": _redo_cost(step)},
        }
    return results


def _costs(job: Any) -> dict[str, float]:
    calls = list(job.model_calls.all())
    return {
        "total_usd": _dollars(calls),
        "video_usd": _dollars(c for c in calls if c.provider in VIDEO_PROVIDERS),
    }


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except OSError, subprocess.CalledProcessError:
        return ""


def export_run(ads: dict[str, str], out: Path, name: str) -> None:
    """Every ad of `ads` ({ad folder: chat session id}) into the run folder `out`."""
    from jobs.models import Job

    for folder in ads:
        check_folder(folder)
    out.mkdir(parents=True, exist_ok=True)
    commit = _commit()
    results: dict[str, dict[str, Any]] = {}
    costs: dict[str, dict[str, float]] = {}
    for folder, session in ads.items():
        job = Job.objects.filter(session_id=session).order_by("-created_at").first()
        if job is None:
            print(f"{folder}: no ad made in session {session}", file=sys.stderr)
            continue
        results.update(export_ad(job, folder, out, commit))
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
                    "regrades": None,
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
