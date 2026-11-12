"""The regression-eval command. Subcommands are loaded only when selected."""

import argparse
import importlib
import sys
from collections.abc import Sequence
from pathlib import Path

# One entry per line: parallel tickets add their own command without changing others.
# Values are (help text, module[:function]); handlers accept (argv, *, store) -> int.
COMMANDS: dict[str, tuple[str, str | None]] = {
    "save-pages": ("Save the scenarios' product pages", "evals.regression.pages"),
    "run": ("Replay scenarios through the app", "evals.regression.runner"),
    "answer": ("Answer the code questions on a saved run", "evals.regression.answers"),
    "grading-page": ("Build the old/new grading page", "evals.regression.grading"),
    "file-grades": ("File Julian's grades into cases", "evals.regression.filing"),
    "move-case": ("Correct a complaint's question", "evals.regression.filing:move_main"),
    "intend": ("Declare an intended change before a run", "evals.regression.reports:intend_main"),
    "report": ("Compare a run with its baselines", "evals.regression.reports:report_main"),
    "promote": ("Promote a graded passing run", "evals.regression.reports:promote_main"),
}


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # Accept --store before or after a subcommand. The rest belongs to that command.
    store_parser = argparse.ArgumentParser(add_help=False)
    store_parser.add_argument("--store", type=Path, help="Lasting store (default: EVAL_STORE)")
    location, remaining = store_parser.parse_known_args(args)
    commands = "\n".join(f"  {name:14} {help_text}" for name, (help_text, _) in COMMANDS.items())
    parser = argparse.ArgumentParser(
        prog="python -m evals.regression",
        description="Replay fixed scenarios after a change.",
        epilog=f"Subcommands:\n{commands}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[store_parser],
    )
    parser.add_argument("command", nargs="?", choices=COMMANDS)
    parser.add_argument("args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    parsed = parser.parse_args(remaining)
    if parsed.command is None:
        parser.print_help()
        return 0
    _, module_name = COMMANDS[parsed.command]
    if module_name is None:
        parser.error(f"{parsed.command} is not implemented yet")
    from .store import Store

    module_path, _, function_name = module_name.partition(":")
    module = importlib.import_module(module_path)
    handler = getattr(module, function_name or "main")
    return int(handler(parsed.args, store=Store(location.store)))


if __name__ == "__main__":
    raise SystemExit(main())
