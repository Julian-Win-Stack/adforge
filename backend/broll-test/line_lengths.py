"""Test A of docs/plans/broll-boreal-h3.md: how long the planner's B-roll lines come out with
the new hint (item 24 of docs/broll-picture-logic.md): "a B-roll line has at least about 10
words", no upper number, and "one short sentence" taken out. Text only: no pictures, no
videos.

The planner is run 3 times on each of the 8 test products, from the second run's saved page
text, photos, target length and conversation (the "R2 …" jobs). Each B-roll line is measured
two ways: by the job's measured voice speed (what the planning check uses), and spoken by
that job's voice (what the clip gets). The second run's own B-roll lines, written with the
old hint, are measured the first way too, for comparison; they cost nothing.

    docker compose exec backend python broll-test/line_lengths.py

Writes MEDIA_ROOT/broll-test/line-lengths/results.json."""

import io
import json
import os
import sys
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")

import django  # noqa: E402

django.setup()

from django.db import connection  # noqa: E402

from adforge import file_store  # noqa: E402
from gateway.gateway import call_model, speak  # noqa: E402
from gateway.types import Image  # noqa: E402
from jobs import page  # noqa: E402
from jobs.checks import count_words  # noqa: E402
from jobs.models import Job, ProducedItem  # noqa: E402
from jobs.planning import PLAN_INSTRUCTIONS, PlanHandoff, producer_decision_for  # noqa: E402
from jobs.work import _conversation  # noqa: E402

OUT = Path("/app/media/broll-test/line-lengths")
PRODUCTS = ["02", "03", "05", "06", "07", "08", "01", "09"]
RUNS = 3

OLD_HINT = (
    "What a scene shows must match what its line says while it says it, so a B-roll line is "
    "one short sentence about what is shown."
)
NEW_HINT = (
    "What a scene shows must match what its line says while it says it. A B-roll line has at "
    "least about 10 words."
)
INSTRUCTIONS = PLAN_INSTRUCTIONS.replace(OLD_HINT, NEW_HINT)
assert INSTRUCTIONS != PLAN_INSTRUCTIONS, "The old hint wasn't found in PLAN_INSTRUCTIONS."


def job_for(product: str) -> Job:
    return Job.objects.get(session__name__startswith=f"R2 {product} ")


def voice_for(job: Job) -> ProducedItem:
    return job.produced.filter(kind=ProducedItem.Kind.VOICE).latest("created_at")


def plan_once(product: str, run: int) -> dict[str, Any]:
    try:
        job = job_for(product)
        photos = list(job.photos.all())
        decision = call_model(
            job=None,
            purpose="plan_ad",
            instructions=INSTRUCTIONS,
            handoff=PlanHandoff(
                product_url=job.product_url,
                page_text=page.for_model(job.page_text),
                target_seconds=job.target_seconds,
                photo_count=len(photos),
                conversation=_conversation(job),
            ),
            output=producer_decision_for(len(photos)),
            images=[Image(label=f"Photo {photo.position}", key=photo.file) for photo in photos],
        )
        if decision.plan is None:
            return {"product": product, "run": run, "asked": decision.question}
        return {
            "product": product,
            "run": run,
            "scenes": [
                {"line": scene.line, "shows": scene.shows} for scene in decision.plan.scenes
            ],
        }
    except Exception as error:  # noqa: BLE001 - a failed run is a result too
        return {"product": product, "run": run, "failed": f"{type(error).__name__}: {error}"}
    finally:
        connection.close()


def spoken_seconds(voice_id: str, line: str) -> float:
    try:
        key = speak(job=None, purpose="measure_voice", voice_id=voice_id, text=line)
        with wave.open(io.BytesIO(file_store.read(key))) as audio:
            return round(float(audio.getnframes() / audio.getframerate()), 2)
    finally:
        connection.close()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = {product: job_for(product) for product in PRODUCTS}
    voices = {product: voice_for(job) for product, job in jobs.items()}

    old = []
    for product, job in jobs.items():
        speed = voices[product].words_per_second or 0.0
        for scene in job.scenes.exclude(shows="").order_by("number"):
            old.append(
                {
                    "product": product,
                    "line": scene.line,
                    "words": count_words(scene.line),
                    "estimated_seconds": round(count_words(scene.line) / speed, 2),
                }
            )

    with ThreadPoolExecutor(max_workers=8) as pool:
        plans = list(
            pool.map(
                lambda pr: plan_once(*pr), [(p, r) for p in PRODUCTS for r in range(1, RUNS + 1)]
            )
        )

    lines = []
    for plan in plans:
        for number, scene in enumerate(plan.get("scenes", []), 1):
            if scene["shows"]:
                lines.append(
                    {"product": plan["product"], "run": plan["run"], "scene": number, **scene}
                )
    for line in lines:
        speed = voices[line["product"]].words_per_second or 0.0
        line["words"] = count_words(line["line"])
        line["estimated_seconds"] = round(line["words"] / speed, 2)
    with ThreadPoolExecutor(max_workers=8) as pool:
        heard = list(
            pool.map(
                lambda line: spoken_seconds(voices[line["product"]].voice_id, line["line"]), lines
            )
        )
    for line, seconds in zip(lines, heard, strict=True):
        line["spoken_seconds"] = seconds

    result = {
        "instructions_hint": NEW_HINT,
        "speeds": {p: round(v.words_per_second or 0.0, 3) for p, v in voices.items()},
        "targets": {p: j.target_seconds for p, j in jobs.items()},
        "plans": plans,
        "broll_lines": lines,
        "second_run_broll_lines": old,
    }
    (OUT / "results.json").write_text(json.dumps(result, indent=2))
    print("Wrote", OUT / "results.json")
    for plan in plans:
        if "scenes" not in plan:
            print(plan["product"], plan["run"], plan.get("asked") or plan.get("failed"))
    for line in lines:
        print(
            f"{line['product']}-{line['run']} #{line['scene']} {line['words']:>2}w "
            f"{line['estimated_seconds']:>5}s est {line['spoken_seconds']:>5}s heard  "
            f"{line['line']}"
        )


if __name__ == "__main__":
    main()
