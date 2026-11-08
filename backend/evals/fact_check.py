"""Re-ask today's fact check on every case in `cases/fact_check.jsonl` and append its verdict
to each case, so `python -m evals.boxes fact_check` counts the 4 boxes on today's check. Free
when the text models run on Azure.

    uv run python -m evals.fact_check [--ids <id> ...]

A fact_check case is one scene of a script: `inputs.handoff` is the whole fact check
handoff as the app sent it (`jobs.checks.FactCheckHandoff`), `inputs.scene` the scene the case
is about, and `inputs.files` the product photos the app showed, in their order. Cases of the
same script are checked in one call, as the app checks a script; photos are shown only when
a scene has a "shows", as the app does.
"""

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.cases import FOLDER, BadCase, Case, Verdict, load

QUESTION = "fact_check"
# Where `project-files:` paths live: the project's shared folder.
PROJECT_FILES = Path(os.environ.get("PROJECT_FILES", "/mnt/project-files"))
# Scripts checked at once: enough to be quick, few enough for Azure's rate limit.
AT_ONCE = 6


def judge_id(instructions: str) -> str:
    """The id a verdict is saved under: the fact check's instructions, hashed, so a prompt
    change is a new judge and `boxes --judge` can count the old and new apart."""
    return "fact_check:" + hashlib.sha256(instructions.encode()).hexdigest()[:12]


def photo_path(file: str) -> Path:
    """Where a case's photo is on disk. Only `project-files:` photos can be read here."""
    place, _, path = file.partition(":")
    if place != "project-files":
        raise ValueError(f"{file!r} can't be read here: only project-files: photos can")
    return PROJECT_FILES / path


def check(handoff: dict[str, Any], files: Sequence[str]) -> dict[int, Verdict]:
    """Today's fact check of one script: each scene's verdict. `handoff` is the fact check
    handoff as the app sent it and `files` the photos it showed, in their order."""
    from gateway import catalog
    from gateway.gateway import ask_unrecorded, shrunk_image
    from gateway.types import Image, ModelRequest
    from jobs.checks import FACT_CHECK_INSTRUCTIONS, FactCheckHandoff, fact_check_for

    sent = FactCheckHandoff.model_validate(handoff)
    scenes = [line.scene for line in sent.lines]
    showing = [line.scene for line in sent.lines if line.shows]
    # As the app does: a script where the person talks throughout is checked on text alone.
    images = (
        tuple(
            shrunk_image(Image(label=f"Photo {n}", key=file), photo_path(file).read_bytes())
            for n, file in enumerate(files, start=1)
        )
        if showing
        else ()
    )
    model = catalog.MODEL_FOR_PURPOSE[QUESTION]
    answer = ask_unrecorded(
        ModelRequest(
            purpose=QUESTION,
            model=model,
            instructions=FACT_CHECK_INSTRUCTIONS,
            handoff=sent,
            output=fact_check_for(scenes, showing=showing),
            images=images,
        )
    )
    judge = judge_id(FACT_CHECK_INSTRUCTIONS)
    at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        line.scene: Verdict(
            verdict="pass" if line.verdict == "ok" else "fail",
            reason=""
            if line.verdict == "ok"
            else f"{line.wrong} is wrong: {line.problem} Page says: {line.page_says}",
            judge=judge,
            model=model,
            at=at,
        )
        for line in answer.lines
    }


def script_key(case: Case) -> str:
    """Cases with the same key are scenes of one script, checked in one call."""
    return json.dumps([case.inputs["handoff"], case.inputs["files"]], sort_keys=True)


@dataclass
class Rechecked:
    """What one re-check found: today's verdict on each case, by case id; the verdict on each
    scene no case is about (the rest of each script, as the planner wrote it), by the script's
    first case id and scene; and each script whose check failed to run, by its first case id."""

    verdicts: dict[str, Verdict] = field(default_factory=dict)
    others: dict[tuple[str, int], Verdict] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


def recheck(
    cases: Sequence[Case],
    ask: Callable[[dict[str, Any], Sequence[str]], dict[int, Verdict]] = check,
) -> Rechecked:
    """Today's verdicts on `cases`: one `ask` per script. A script whose check fails to run
    is named in `errors`, and the others are kept."""
    scripts: dict[str, list[Case]] = {}
    for case in cases:
        if not isinstance(case.inputs.get("scene"), int):
            raise ValueError(f"case {case.id!r}: inputs.scene must be the scene's number")
        scripts.setdefault(script_key(case), []).append(case)

    def asked(same: list[Case]) -> dict[int, Verdict] | str:
        try:
            return ask(same[0].inputs["handoff"], same[0].inputs["files"])
        except Exception as error:  # noqa: BLE001 - one script's failure is reported, not fatal
            return f"{type(error).__name__}: {error}"

    found = Rechecked()
    with ThreadPoolExecutor(AT_ONCE) as pool:
        for same, answer in zip(scripts.values(), pool.map(asked, scripts.values()), strict=True):
            if isinstance(answer, str):
                found.errors[same[0].id] = answer
                continue
            about = {case.inputs["scene"] for case in same}
            for case in same:
                found.verdicts[case.id] = answer[case.inputs["scene"]]
            for scene, verdict in answer.items():
                if scene not in about:
                    found.others[(same[0].id, scene)] = verdict
    return found


def appended(path: Path, verdicts: dict[str, Verdict]) -> None:
    """Append each case's new verdict to its line of the case file at `path`, keeping every
    other line as it was."""
    lines = path.read_text(encoding="utf-8").split("\n")
    for number, line in enumerate(lines):
        if not line.strip():
            continue
        raw = json.loads(line)
        if raw["id"] in verdicts:
            raw["verdicts"].append(asdict(verdicts[raw["id"]]))
            lines[number] = json.dumps(raw, ensure_ascii=False)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None, folder: Path = FOLDER) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.fact_check",
        description="Re-ask today's fact check on every fact_check case and save its verdict.",
    )
    parser.add_argument("--ids", nargs="+", help="only these cases")
    args = parser.parse_args(argv)
    path = folder / f"{QUESTION}.jsonl"
    try:
        cases = load(path)
    except BadCase as wrong:
        print(wrong, file=sys.stderr)
        return 1
    if args.ids:
        unknown = set(args.ids) - {case.id for case in cases}
        if unknown:
            print(f"No such case: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 1
        cases = [case for case in cases if case.id in args.ids]
    found = recheck(cases)
    appended(path, found.verdicts)
    failed = sum(verdict.verdict == "fail" for verdict in found.verdicts.values())
    print(
        f"{len(found.verdicts)} cases re-checked: {failed} failed, "
        f"{len(found.verdicts) - failed} passed."
    )
    # Most cases are planted fails, so a check that fails everything would look perfect on
    # them: the rest of each script, unchanged, shows whether it has grown too strict.
    strict = {key: v for key, v in found.others.items() if v.verdict == "fail"}
    print(
        f"Unchanged scenes in the same scripts (no label): {len(strict)} of "
        f"{len(found.others)} failed."
    )
    for (case_id, scene), verdict in sorted(strict.items()):
        print(f"  - {case_id} scene {scene}: {verdict.reason}")
    for case_id, error in found.errors.items():
        print(f"Not checked, the script of {case_id}: {error}", file=sys.stderr)
    print(f"Count them: uv run python -m evals.boxes {QUESTION}")
    return 1 if found.errors else 0


if __name__ == "__main__":
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
    django.setup()
    sys.exit(main())
