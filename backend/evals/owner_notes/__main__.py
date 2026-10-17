"""The owner-notes run: the shop owner's notes on round 4's ads, sent through the real chat,
with every video faked, to find where the app gets what the owner asked for wrong.

Each ad is first made again from what round 4 saved (no video, picture or voice paid for),
into a database of its own. Each case then starts from a copy of its ad's database, sends the
owner's notes, says yes to what the producer restates, and keeps what happened in
<out>/<case id>/: chat.md to read, record.json, and each picture made. From backend/:

    uv run python -m evals.owner_notes run --cases evals/owner_notes/cases.json \\
        --out /mnt/project-files/runs/owner-notes/<name> [--only id,id] [--parallel 4]

Needs Postgres (adforge/adforge on localhost) and the OpenAI or Azure key. Costs the text
models and the pictures the notes ask for again, at most 2 a minute across the run, and, when
FIRECRAWL_API_KEY is set, Firecrawl's reads of each product page for the first versions (3 an
ad), so the page's photos are the ones round 4's plan points at.
"""

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PG = "postgres://adforge:adforge@localhost:5432/"
MEDIA = os.environ.get("OWNER_NOTES_MEDIA", "/home/user/owner-notes-media")
# Every service that costs money is faked by the provider; these keys are dropped anyway, so
# a call that slipped past it fails rather than pays.
PAID_KEYS = [
    "HEYGEN_API_KEY",
    "CREATIFY_API_KEY",
    "FAL_KEY",
    "INWORLD_API_KEY",
    "ELEVENLABS_API_KEY",
]


# A case marked "real_clip" makes its one B-roll clip
# with Creatify for real (fal stores its pictures) and has it judged for real.
REAL_CLIP_KEYS = ("CREATIFY_API_KEY", "FAL_KEY")


def _setup(database: str, keep: tuple[str, ...] = ()) -> None:
    os.environ["DATABASE_URL"] = PG + database
    os.environ["MEDIA_ROOT"] = MEDIA
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
    os.environ["DJANGO_ALLOWED_HOSTS"] = "testserver"
    os.environ["LANGFUSE_PUBLIC_KEY"] = ""
    os.environ["LANGFUSE_SECRET_KEY"] = ""
    for key in PAID_KEYS:
        if key not in keep:
            os.environ.pop(key, None)
    import django

    django.setup()
    from adforge import celery_app

    celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)


def first(ad_key: str, database: str, out: Path) -> None:
    """Make the ad's first version again in `database`, and keep its session's id."""
    _setup(database)
    from django.core.management import call_command

    from gateway.gateway import use_model

    from .chat import Chat, make_first_version
    from .provider import OwnerNotesProvider
    from .saved_ads import saved_ads

    call_command("migrate", verbosity=0)
    ad = saved_ads()[ad_key]
    provider = OwnerNotesProvider(ad)
    with use_model(provider):
        chat = Chat()
        make_first_version(chat, ad.url, ad.answers)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{ad_key}.json").write_text(json.dumps({"session": chat.session_id}))


def case(case_json: str, session: str, database: str, out: Path) -> None:
    """Send one case's notes on its ad, made in `database`, and keep what happened."""
    real_clip = json.loads(case_json).get("real_clip", False)
    _setup(database, keep=REAL_CLIP_KEYS if real_clip else ())
    from gateway.gateway import use_model

    from .chat import Chat, Notes, send_notes, since, speak_at
    from .provider import OwnerNotesProvider
    from .record import record
    from .saved_ads import saved_ads

    asked = json.loads(case_json)
    ad = saved_ads()[asked["ad"]]
    provider = OwnerNotesProvider(ad, real_clip=real_clip)
    provider.start_notes(ad.words_per_second())
    with use_model(provider):
        chat = Chat(session)
        speak_at(chat, ad.words_per_second())
        start = since(chat)
        turned = send_notes(
            chat,
            Notes(asked.get("messages", []), asked.get("yes_answers", 1), asked.get("turns", [])),
        )
    record(chat, start, turned, asked, out / asked["id"])


def _psql(sql: str) -> None:
    subprocess.run(
        ["psql", PG + "postgres", "-qc", sql], check=True, capture_output=True, text=True
    )


def _db_exists(name: str) -> bool:
    found = subprocess.run(
        ["psql", PG + "postgres", "-tAc", f"SELECT 1 FROM pg_database WHERE datname='{name}'"],
        check=True,
        capture_output=True,
        text=True,
    )
    return found.stdout.strip() == "1"


def _child(*args: str, log: Path) -> int:
    with log.open("a") as written:
        return subprocess.run(
            [sys.executable, "-m", "evals.owner_notes", *args], stdout=written, stderr=written
        ).returncode


def run(cases_file: Path, out: Path, only: list[str], parallel: int, fresh: bool) -> None:
    cases = json.loads(cases_file.read_text())
    if only:
        cases = [each for each in cases if each["id"] in only]
    out.mkdir(parents=True, exist_ok=True)
    firsts = out / "first-versions"
    ads = sorted({each["ad"] for each in cases})

    def make(ad: str) -> None:
        database = f"owner_notes_first_{ad.lower()}"
        made = firsts / f"{ad}.json"
        if _db_exists(database) and made.exists() and not fresh:
            return
        # A rebuild that fails leaves no first version for its cases to start from.
        made.unlink(missing_ok=True)
        try:
            _psql(f"DROP DATABASE IF EXISTS {database}")
            _psql(f"CREATE DATABASE {database}")
        except subprocess.CalledProcessError as error:
            print(f"first version of #{ad}: FAILED ({error.stderr.strip()})", flush=True)
            return
        code = _child(
            "first", "--ad", ad, "--db", database, "--out", str(firsts), log=out / f"first-{ad}.log"
        )
        print(f"first version of #{ad}: {'made' if code == 0 else f'FAILED ({code})'}", flush=True)

    with ThreadPoolExecutor(parallel) as pool:
        list(pool.map(make, ads))

    def send(asked: dict[str, object]) -> None:
        ad = str(asked["ad"])
        made = firsts / f"{ad}.json"
        if not made.exists():
            print(f"{asked['id']}: skipped, #{ad} has no first version", flush=True)
            return
        database = f"owner_notes_case_{str(asked['id']).replace('-', '_').lower()}"
        try:
            _psql(f"DROP DATABASE IF EXISTS {database}")
            _psql(f"CREATE DATABASE {database} TEMPLATE owner_notes_first_{ad.lower()}")
        except subprocess.CalledProcessError as error:
            print(f"{asked['id']}: FAILED ({error.stderr.strip()})", flush=True)
            return
        session = json.loads(made.read_text())["session"]
        code = _child(
            "case",
            "--case",
            json.dumps(asked),
            "--session",
            session,
            "--db",
            database,
            "--out",
            str(out),
            log=out / f"case-{asked['id']}.log",
        )
        print(f"{asked['id']}: {'done' if code == 0 else f'FAILED ({code})'}", flush=True)

    with ThreadPoolExecutor(parallel) as pool:
        list(pool.map(send, cases))


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.owner_notes")
    commands = parser.add_subparsers(dest="command", required=True)
    each_run = commands.add_parser("run", help="make the ads' first versions, then every case")
    each_run.add_argument("--cases", type=Path, required=True)
    each_run.add_argument("--out", type=Path, required=True)
    each_run.add_argument("--only", default="", help="comma-separated case ids")
    each_run.add_argument("--parallel", type=int, default=4)
    each_run.add_argument("--fresh", action="store_true", help="make first versions again")
    one_first = commands.add_parser("first")
    one_first.add_argument("--ad", required=True)
    one_first.add_argument("--db", required=True)
    one_first.add_argument("--out", type=Path, required=True)
    one_case = commands.add_parser("case")
    one_case.add_argument("--case", required=True)
    one_case.add_argument("--session", required=True)
    one_case.add_argument("--db", required=True)
    one_case.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run":
        only = [each for each in args.only.split(",") if each]
        run(args.cases, args.out, only, args.parallel, args.fresh)
    elif args.command == "first":
        first(args.ad, args.db, args.out)
    else:
        case(args.case, args.session, args.db, args.out)


if __name__ == "__main__":
    main()
