"""Count the approved code questions on one scenario; never call a model."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from evals import code_tests

from . import answer_rules as rules
from .questions import code_questions
from .store import Answer, AnswerItem, Answers, FirstTry, Store, read_json

if TYPE_CHECKING:
    from jobs.models import Job, ProducedItem


@dataclass(frozen=True)
class NoteComparison:
    before: ProducedItem
    after: ProducedItem
    sent_at: datetime


def not_reached(reason: str = "The run did not reach this stage.") -> Answer:
    return {"verdict": "not_reached", "reason": reason, "items": []}


def _counted(verdict: code_tests.Verdict, item: str) -> Answer:
    return rules.counted(verdict.passed, verdict.why, item)


def page_photos_this_product(job: Job, labels: Mapping[str, str]) -> Answer:
    from adforge import file_store

    photos = {photo.number: photo for photo in job.library.filter(number__in=job.product_photos)}
    missing = set(job.product_photos) - photos.keys()
    if missing:
        raise ValueError(f"Product library photos are missing: {sorted(missing)}")
    return rules.photo_labels(
        [
            (photos[number].file, file_store.read(photos[number].file))
            for number in job.product_photos
        ],
        labels,
    )


def plan_has_demo(job: Job) -> Answer:
    planned = code_tests.Shipped(
        key="plan",
        scenes=[
            code_tests.Scene(scene.number, scene.kind, scene.talking_style, None)
            for scene in job.scenes.all()
        ],
        brand=job.brand,
        product_name=job.product_name,
        end_card_seconds=None,
        end_card_photo=False,
    )
    return _counted(code_tests.demo_floor(planned), "plan")


def plan_names_brand(job: Job) -> Answer:
    script = " ".join(job.scenes.values_list("line", flat=True))
    names = [name for name in (job.brand, job.product_name) if code_tests.words(name)]
    said = next((name for name in names if code_tests.heard_in(name, script)), None)
    return rules.counted(
        said is not None,
        f'"{said}" appears in the script.'
        if said
        else "Neither the brand nor the product name appears as whole words in the script.",
        "plan",
    )


def plan_fits_length(job: Job) -> Answer:
    return rules.plan_length(list(job.scenes.values_list("line", flat=True)), job.target_seconds)


def _finished(job: Job) -> ProducedItem | None:
    from jobs.models import ProducedItem

    return (
        job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).order_by("-version", "-pk").first()
    )


@dataclass(frozen=True)
class _Cut:
    clip: ProducedItem
    line: str
    heard: rules.HeardCut


def _cuts(job: Job) -> list[_Cut]:
    from jobs import work
    from jobs.models import ProducedItem

    finished = _finished(job)
    if finished is None:
        return []
    clips = ProducedItem.objects.select_related("scene").in_bulk(
        cut["clip"] for cut in finished.cuts
    )
    cuts = []
    for cut in finished.cuts:
        clip = clips[cut["clip"]]
        if clip.scene is None:
            raise ValueError(f"Clip {clip.pk} has no scene.")
        transcript = work.clip_transcript(clip)
        cuts.append(
            _Cut(
                clip,
                clip.scene.line,
                rules.HeardCut(
                    f"s{cut['scene']}",
                    transcript.words if transcript is not None else [],
                    float(cut.get("voice_start", cut["start"])),
                    float(cut.get("voice_end", cut["end"])),
                    float(cut.get("voice_clip_start", cut["clip_start"])),
                    float(cut.get("voice_clip_end", cut["clip_end"])),
                ),
            )
        )
    return cuts


def clip_words_match(job: Job) -> Answer:
    if _finished(job) is None:
        return not_reached("There is no finished ad whose clips can be checked.")
    items: list[AnswerItem] = []
    for cut in _cuts(job):
        said = " ".join(str(word.get("text", "")) for word in cut.heard.words)
        missing = rules.missing_words(cut.line, said)
        items.append(
            {
                "item": cut.heard.item,
                "verdict": "fail" if missing else "pass",
                "reason": "Missing words: " + ", ".join(missing)
                if missing
                else "Every word of the line is heard in order.",
            }
        )
    return (
        rules.combine(items)
        if items
        else rules.counted(False, "The finished ad has no scene cuts.", "ad")
    )


def ad_finishes(job: Job) -> Answer:
    from adforge import file_store

    finished = _finished(job)
    exists = finished is not None and bool(finished.file) and file_store.exists(finished.file)
    return rules.counted(
        exists, "A finished ad is made." if exists else "No finished ad file is available.", "ad"
    )


def ad_fits_length(job: Job) -> Answer:
    from django.conf import settings

    from adforge import file_store

    finished = _finished(job)
    if finished is None:
        return not_reached("There is no finished ad to measure.")
    with tempfile.TemporaryDirectory() as folder:
        video = Path(folder) / "ad.mp4"
        video.write_bytes(file_store.read(finished.file))
        measured = subprocess.run(
            [
                settings.FFPROBE,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(video),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    seconds = float(json.loads(measured.stdout)["format"]["duration"])
    return rules.finished_length(seconds, job.target_seconds)


def ad_end_card(job: Job) -> Answer:
    shipped = code_tests.shipped("ad", job)
    return (
        _counted(code_tests.end_card(shipped), "ad")
        if shipped is not None
        else not_reached("There is no finished ad.")
    )


def ad_brand_heard(job: Job) -> Answer:
    shipped = code_tests.shipped("ad", job)
    return (
        _counted(code_tests.name_said(shipped), "ad")
        if shipped is not None
        else not_reached("There is no finished ad.")
    )


def ad_last_words(job: Job) -> Answer:
    if _finished(job) is None:
        return not_reached("There is no finished ad.")
    cuts = _cuts(job)
    if not cuts:
        return rules.counted(False, "The finished ad has no played words.", "ad")
    return rules.combine(
        [
            rules.last_words(cut.heard.item, cut.heard.words, cut.heard.voice_clip_end)
            for cut in cuts
        ]
    )


def ad_no_dead_air(job: Job) -> Answer:
    if _finished(job) is None:
        return not_reached("There is no finished ad.")
    return rules.dead_air([cut.heard for cut in _cuts(job)])


def _clip_files(ad: ProducedItem, *, identity: bool) -> dict[str, str]:
    from jobs.models import ProducedItem

    clips = ProducedItem.objects.in_bulk(cut["clip"] for cut in ad.cuts)
    return {
        f"s{clips[cut['clip']].scene_id if identity else cut['scene']}": clips[cut["clip"]].file
        for cut in ad.cuts
    }


def note_untouched_same(
    job: Job, before: ProducedItem, after: ProducedItem, sent_at: datetime
) -> Answer:
    from jobs.models import AgentFix, ProducedItem

    identity = before.job_id == after.job_id
    # Scene ids survive edits and renumbering within one copied chat. Across the original
    # and its copy, scene numbers match the corresponding original scenes instead.
    fixes = AgentFix.objects.filter(
        step__scene__job=after.job,
        door=AgentFix.Door.OWNER,
        created_at__gte=sent_at,
        created_at__lte=after.created_at,
    )
    touched = {
        f"s{fix.step.scene_id if identity else fix.step.scene.number}"
        for fix in fixes.select_related("step__scene")
    }
    all_touched = after.job.produced.filter(
        kind__in=[ProducedItem.Kind.VOICE, ProducedItem.Kind.PORTRAIT],
        created_at__gte=sent_at,
        created_at__lte=after.created_at,
    ).exists()
    old = _clip_files(before, identity=identity)
    if all_touched:
        touched.update(old)
    answered = rules.untouched_clips(old, _clip_files(after, identity=identity), touched)
    if identity:
        names = {f"s{cut.clip.scene_id}": cut.heard.item for cut in _cuts(after.job)}
        for item in answered["items"]:
            item["item"] = names.get(item["item"], item["item"])
        answered = rules.combine(answered["items"], "The note touched every scene.")
    return answered


def _answer(job: Job, question: str, count: Callable[[], Answer]) -> Answer:
    from chat.models import Message
    from jobs import notices

    try:
        return count()
    except Exception as error:
        reason = f"Could not answer {question}: {type(error).__name__}: {error}"
        notices.post_notice(
            job,
            notices.Kind("regression_answer_failed", for_owner=False),
            reason,
            Message.Level.PROBLEM,
        )
        stage = question.partition("_")[0]
        return rules.counted(False, reason, stage if stage in ("page", "plan", "ad") else "ad")


def first_try(job: Job) -> FirstTry:
    from jobs.models import AgentFix

    passed = 0
    clips: set[int] = set()
    for scene in job.scenes.all():
        fixes = AgentFix.objects.filter(step__scene=scene).order_by("created_at", "pk")
        first = fixes.first()
        for fixing in fixes:
            if fixing.first_clip_id is not None:
                clips.add(fixing.first_clip_id)
            clips.update(fixing.tries.exclude(clip=None).values_list("clip_id", flat=True))
        if first is None:
            continue
        if first.first_clip is not None:
            ruling = first.first_clip.ruling or {}
            passed += (
                ruling.get("verdict") == "pass"
                and (ruling.get("word_check") or {}).get("verdict") == "pass"
            )
        else:
            filmed = first.tries.exclude(clip=None).order_by("number").first()
            passed += filmed is not None and filmed.passed
    return {"scenes": job.scenes.count(), "first_passed": passed, "clips": len(clips)}


def answer_job(
    job: Job,
    *,
    scenario: str,
    run: str,
    to: Literal["plans", "pictures", "full"] = "full",
    kind: Literal["full", "picture"] = "full",
    photo_labels: Mapping[str, str] | None = None,
    notes: Sequence[NoteComparison] = (),
    model_call_ids: Collection[int] | None = None,
    cost_usd: Decimal | float | None = None,
) -> Answers:
    from gateway.models import ModelCall

    answers = {question.id: not_reached() for question in code_questions()}
    counts: dict[str, Callable[[], Answer]] = {
        "page_photos_this_product": lambda: page_photos_this_product(job, photo_labels or {}),
    }
    if job.scenes.exists():
        counts.update(
            {
                "plan_has_demo": lambda: plan_has_demo(job),
                "plan_names_brand": lambda: plan_names_brand(job),
                "plan_fits_length": lambda: plan_fits_length(job),
            }
        )
    if to == "full" and kind == "full":
        counts.update(
            {
                "clip_words_match": lambda: clip_words_match(job),
                "ad_finishes": lambda: ad_finishes(job),
                "ad_fits_length": lambda: ad_fits_length(job),
                "ad_end_card": lambda: ad_end_card(job),
                "ad_brand_heard": lambda: ad_brand_heard(job),
                "ad_last_words": lambda: ad_last_words(job),
                "ad_no_dead_air": lambda: ad_no_dead_air(job),
            }
        )
    answers.update({question: _answer(job, question, count) for question, count in counts.items()})
    if notes:
        items: list[AnswerItem] = []
        for number, note in enumerate(notes, 1):
            result = _answer(
                job,
                "note_untouched_same",
                partial(note_untouched_same, job, note.before, note.after, note.sent_at),
            )
            items.append(
                {"item": f"note{number}", "verdict": result["verdict"], "reason": result["reason"]}
            )
        answers["note_untouched_same"] = rules.combine(items)
    if cost_usd is None:
        calls = (
            ModelCall.objects.filter(pk__in=model_call_ids)
            if model_call_ids is not None
            else job.model_calls.all()
        )
        cost_usd = sum((call.cost_usd or Decimal(0) for call in calls), Decimal(0))
    return {
        "scenario": scenario,
        "run": run,
        "answers": answers,
        "first_try": first_try(job),
        "cost_usd": float(cost_usd),
    }


def _answer_restored(store: Store, run: str, scenario: str) -> None:
    """Only a child calls this: its Django settings already point at the restored dump."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
    import django

    django.setup()
    from django.core.management import call_command

    from jobs.models import Job, ProducedItem

    # Re-answering an older run must work after the app adds a database migration.
    # Only this disposable restored database is migrated; its saved dump stays intact.
    call_command("migrate", verbosity=0)
    record = store.read_run(run)
    metadata = read_json(store.scenario_dir(run, scenario) / "scenario.json")
    if not metadata.get("job_id"):
        raise ValueError("scenario.json must name the original scenario job_id.")
    job = Job.objects.get(pk=metadata["job_id"])
    notes = [
        NoteComparison(
            ProducedItem.objects.get(pk=note["before_ad"]),
            ProducedItem.objects.get(pk=note["after_ad"]),
            datetime.fromisoformat(note["sent_at"]),
        )
        for note in metadata.get("notes", [])
    ]
    to, kind = record.get("to", "full"), metadata.get("kind", "full")
    if to not in ("plans", "pictures", "full") or kind not in ("full", "picture"):
        raise ValueError("The saved run must name a valid target and scenario kind.")
    answered = answer_job(
        job,
        scenario=scenario,
        run=run,
        to=cast(Literal["plans", "pictures", "full"], to),
        kind=cast(Literal["full", "picture"], kind),
        photo_labels=store.read_photo_labels().get(scenario, {}),
        notes=notes,
        model_call_ids=metadata.get("model_call_ids"),
        cost_usd=metadata.get("cost_usd"),
    )
    store.write_answers(run, scenario, answered)
    print(
        f"{scenario}: answered {len(answered['answers'])} code questions; "
        f"${answered['cost_usd']:.2f}"
    )


def main(argv: Sequence[str], *, store: Store) -> int:
    from .database import child_environment, isolated_database

    parser = argparse.ArgumentParser(prog="python -m evals.regression answer")
    parser.add_argument("run")
    parser.add_argument("--only", nargs="+")
    args = parser.parse_args(argv)
    record = store.read_run(args.run)
    scenarios = record.get("scenarios", [])
    if not scenarios:
        print(f"Run {args.run!r} has no saved scenarios.", file=sys.stderr)
        return 1
    unknown = set(args.only or []) - set(scenarios)
    if unknown:
        parser.error(f"Scenarios are not in this run: {', '.join(sorted(unknown))}")
    failed = False
    for scenario in scenarios:
        if args.only and scenario not in args.only:
            continue
        try:
            dump = store.dump_path(args.run, scenario)
            if not dump.is_file():
                raise ValueError("No saved db.dump; finish the scenario run first.")
            with isolated_database(dump) as url:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "evals.regression.answers",
                        str(store.root.resolve()),
                        args.run,
                        scenario,
                    ],
                    env=child_environment(url, store.media_dir(args.run, scenario)),
                    capture_output=True,
                    text=True,
                )
            if result.returncode:
                raise ValueError(result.stderr.strip() or "The answer child failed.")
            print(result.stdout.strip())
        except Exception as error:
            failed = True
            print(f"{scenario}: could not answer: {error}", file=sys.stderr)
    return int(failed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Answer a restored eval scenario (internal child)."
    )
    parser.add_argument("store", type=Path)
    parser.add_argument("run")
    parser.add_argument("scenario")
    given = parser.parse_args()
    try:
        _answer_restored(Store(given.store), given.run, given.scenario)
    except Exception as error:
        print(
            f"Could not answer {given.scenario}: {type(error).__name__}: {error}", file=sys.stderr
        )
        raise SystemExit(1) from None
