"""The repeated-ask eval: scripted owner sessions against the real producer, everything else
faked. See evals/README.md. From backend/:

    uv run python -m evals.repeated_asks run --name before --out <folder> [--cases id,id]
        [--repeats 3] [--parallel 6] [--yes]
    uv run python -m evals.repeated_asks report --before <folder> --after <folder>
        [--json <file>]
"""

import argparse
import sys
from pathlib import Path

from .cases import CASES_FILE, BadCases, check_counts, load_cases
from .report import report
from .runner import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.repeated_asks")
    commands = parser.add_subparsers(dest="command", required=True)
    playing = commands.add_parser("run", help="play the sessions with the real producer")
    playing.add_argument("--name", required=True, help="the run's name, also its database's")
    playing.add_argument("--out", type=Path, required=True, help="the run's folder")
    playing.add_argument("--cases", default="", help="comma-separated case ids (default all)")
    playing.add_argument("--repeats", type=int, default=3)
    playing.add_argument("--parallel", type=int, default=4)
    playing.add_argument("--yes", action="store_true", help="spend the estimate printed")
    playing.add_argument("--cases-file", type=Path, default=CASES_FILE)
    reporting = commands.add_parser("report", help="compare a before run with an after run")
    reporting.add_argument("--before", type=Path, required=True)
    reporting.add_argument("--after", type=Path, required=True)
    reporting.add_argument("--json", type=Path, default=None)
    reporting.add_argument("--cases-file", type=Path, default=CASES_FILE)
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.cases_file)
        check_counts(cases)
    except BadCases as error:
        print(f"The case file is refused: {error}", file=sys.stderr)
        return 1
    if args.command == "report":
        report(args.before, args.after, cases, args.json, sys.stdout)
        return 0
    chosen = [each for each in args.cases.split(",") if each]
    unknown = [each for each in chosen if each not in cases]
    if unknown:
        print(f"Unknown case ids: {', '.join(unknown)}", file=sys.stderr)
        return 1
    return run(
        args.name,
        [cases[each] for each in chosen] if chosen else list(cases.values()),
        cases_file=args.cases_file.resolve(),
        out=args.out,
        repeats=args.repeats,
        parallel=args.parallel,
        yes=args.yes,
    )


if __name__ == "__main__":
    sys.exit(main())
