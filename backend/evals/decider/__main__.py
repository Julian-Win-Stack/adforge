"""The decider's replay and rule cases (fix redesign ticket 07, test layers 1 and 1b): the real
decider (agents/decider.py) reads past shop owners' notes, and hand-made notes of one rule
each, on round 4's ads as the owner-notes run made them again, and everything it did is kept
for Julian to grade and for the evals to read again. From backend/:

    uv run python -m evals.decider run --out /mnt/project-files/runs/fix-redesign-decider/<run>
        [--cases evals/decider/replay_cases.json,evals/decider/rule_cases.json]
        [--only id,id] [--model gpt-6.1-sol] [--parallel 8]
    uv run python -m evals.decider page <run folder>

Each case starts from a copy of its ad's first version, the database the owner-notes run made
(`python -m evals.owner_notes run`, database owner_notes_first_<ad>, its session id in
<first versions>/<ad>.json), so a case pays only for the decider's own model calls. The run
writes a folder per case (evals.decider.record), results.json with every case's result and
the run's spend, and grading.html, then prints every red notice and the spend.

Needs Postgres (adforge/adforge on localhost) and the OpenAI or Azure key. No video, picture
or voice is made: every key that pays for one is dropped.
"""

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import Any

from evals.owner_notes.__main__ import MEDIA, PAID_KEYS, PG, _psql

HERE = Path(__file__).parent
CASES = [HERE / "replay_cases.json", HERE / "rule_cases.json"]
FIRSTS = Path("/mnt/project-files/runs/fix-redesign-decider/first-versions")


def _setup(database: str) -> None:
    os.environ["DATABASE_URL"] = PG + database
    os.environ["MEDIA_ROOT"] = MEDIA
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
    os.environ["LANGFUSE_PUBLIC_KEY"] = ""
    os.environ["LANGFUSE_SECRET_KEY"] = ""
    for key in [*PAID_KEYS, "FIRECRAWL_API_KEY"]:
        os.environ.pop(key, None)
    import django

    django.setup()


def case(asked: dict[str, Any], session: str, database: str, out: Path, model: str) -> None:
    """Play one case on its ad's copy in `database`, and keep what happened in `out`."""
    _setup(database)
    from chat.models import Session
    from gateway import catalog

    from .play import play
    from .record import record

    catalog.MODEL_FOR_PURPOSE["decide_fixes"] = model
    job = Session.objects.get(pk=session).jobs.order_by("created_at").last()
    assert job is not None, f"session {session} has no ad"
    forget_the_faked_judge(job)
    record(asked, play(job, asked), out, model=model)


def forget_the_faked_judge(job: Any) -> None:
    """The owner-notes run fakes the clip judge as passing every clip it never saw judged; the
    decider must not be told a clip passed that no judge looked at, so the verdict is dropped
    and the frames the judge was given are kept."""
    from jobs.models import ProducedItem

    for clip in job.produced.filter(kind=ProducedItem.Kind.CLIP):
        ruling = dict(clip.ruling or {})
        if ruling.pop("verdict", None) is not None:
            clip.ruling = ruling
            clip.save(update_fields=["ruling"])


def _child(*args: str, log: Path) -> int:
    with log.open("a") as written:
        return subprocess.run(
            [sys.executable, "-m", "evals.decider", *args], stdout=written, stderr=written
        ).returncode


def run(
    cases: list[dict[str, Any]], out: Path, model: str, parallel: int, firsts: Path = FIRSTS
) -> list[dict[str, Any]]:
    kept = out / "results.json"
    if kept.exists() and json.loads(kept.read_text())["model"] != model:
        raise SystemExit(f"{out} holds a run on another model: give this one its own folder")
    out.mkdir(parents=True, exist_ok=True)
    (out / "logs").mkdir(exist_ok=True)

    def send(asked: dict[str, Any]) -> dict[str, Any] | None:
        made = firsts / f"{asked['ad']}.json"
        if not made.exists():
            print(f"{asked['id']}: skipped, #{asked['ad']} has no first version", flush=True)
            return None
        # One database per case and model, so runs on two models can go at once.
        name = f"{model}_{asked['id']}".replace("-", "_").replace(".", "_").lower()
        database = f"decider_{name}"[:63]
        try:
            _psql(f"DROP DATABASE IF EXISTS {database}")
            _psql(f"CREATE DATABASE {database} TEMPLATE owner_notes_first_{asked['ad'].lower()}")
        except subprocess.CalledProcessError as error:
            print(f"{asked['id']}: FAILED, no copy of #{asked['ad']}: {error.stderr}", flush=True)
            return None
        code = _child(
            "case",
            "--case",
            json.dumps(asked),
            "--session",
            json.loads(made.read_text())["session"],
            "--db",
            database,
            "--out",
            str(out),
            "--model",
            model,
            log=out / "logs" / f"{asked['id']}.log",
        )
        _psql(f"DROP DATABASE IF EXISTS {database}")
        found = out / asked["id"] / "result.json"
        print(f"{asked['id']}: {'done' if code == 0 else f'FAILED ({code})'}", flush=True)
        return json.loads(found.read_text()) if code == 0 and found.exists() else None

    with ThreadPoolExecutor(parallel) as pool:
        played = [each for each in pool.map(send, cases) if each is not None]
    failed = sorted({each["id"] for each in cases} - {each["id"] for each in played})
    _write_results(out, played, model, failed)
    return played


def _write_results(out: Path, played: list[dict[str, Any]], model: str, failed: list[str]) -> None:
    """results.json: every case's result, the run's spend and model, and which of told/ the
    cases' decider was told (evals.decider.record). A rerun of some cases into the same
    folder keeps the other cases' results; a case that failed this time loses its old result
    and is listed as failed until it runs again."""
    path = out / "results.json"
    before = json.loads(path.read_text()) if path.exists() else {"cases": [], "failed": []}
    by_id = {each["id"]: each for each in before["cases"]} | {each["id"]: each for each in played}
    for case_id in failed:
        by_id.pop(case_id, None)
    still_failed = sorted((set(before["failed"]) - set(by_id)) | set(failed))
    results = sorted(by_id.values(), key=lambda each: (each.get("layer", ""), each["id"]))
    spend = sum((Decimal(each["spend_usd"]) for each in results), Decimal(0))
    path.write_text(
        json.dumps(
            {
                "model": model,
                "spend_usd": str(spend),
                "failed": still_failed,
                "told": sorted({each.get("told", "") for each in results} - {""}),
                "cases": results,
            },
            indent=1,
            ensure_ascii=False,
        )
    )


def report(out: Path) -> str:
    """What every run ends by printing: each red notice, cases that failed to run, the spend."""
    results = json.loads((out / "results.json").read_text())
    lines = [f"Red notices in {out}:"]
    red = [(each["id"], text) for each in results["cases"] for text in each["red_notices"]]
    lines += [f"  RED {case_id}: {text}" for case_id, text in red] or ["  none"]
    failed_calls = sum(each["model_calls_failed"] for each in results["cases"])
    lines.append(f"Model calls that failed: {failed_calls}")
    if results["failed"]:
        lines.append(f"Cases that didn't run: {', '.join(results['failed'])}")
    lines.append(f"OpenAI spend: ${results['spend_usd']} ({results['model']})")
    return "\n".join(lines)


def load_cases(files: list[Path], only: list[str]) -> list[dict[str, Any]]:
    cases = [each for path in files for each in json.loads(path.read_text())]
    return [each for each in cases if not only or each["id"] in only]


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.decider")
    commands = parser.add_subparsers(dest="command", required=True)
    each_run = commands.add_parser("run")
    each_run.add_argument("--out", type=Path, required=True)
    each_run.add_argument("--cases", default=",".join(str(path) for path in CASES))
    each_run.add_argument("--only", default="", help="comma-separated case ids")
    each_run.add_argument("--model", default="gpt-6.1-sol")
    each_run.add_argument("--parallel", type=int, default=8)
    each_run.add_argument(
        "--firsts", type=Path, default=FIRSTS, help="the first versions' session ids, by ad"
    )
    one = commands.add_parser("case")
    one.add_argument("--case", required=True)
    one.add_argument("--session", required=True)
    one.add_argument("--db", required=True)
    one.add_argument("--out", type=Path, required=True)
    one.add_argument("--model", required=True)
    one_page = commands.add_parser("page")
    one_page.add_argument("run", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        only = [each for each in args.only.split(",") if each]
        cases = load_cases([Path(each) for each in args.cases.split(",")], only)
        run(cases, args.out, args.model, args.parallel, args.firsts)
        from .page import write

        print(report(args.out))
        print(f"Page: {write(args.out)}")
    elif args.command == "case":
        case(json.loads(args.case), args.session, args.db, args.out, args.model)
    else:
        from .page import write

        print(report(args.run))
        print(f"Page: {write(args.run)}")


if __name__ == "__main__":
    main()
