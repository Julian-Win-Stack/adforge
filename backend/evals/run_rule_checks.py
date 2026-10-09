"""Run the rule evals' code checks on saved runs and print what failed, per ad and scene.
Free: no model is called. From backend/:

    uv run python -m evals.run_rule_checks <run folder> [<run folder> ...]
"""

import sys
from pathlib import Path

from evals.rule_checks import check_run


def main(runs: list[str]) -> None:
    for run in runs:
        results = check_run(Path(run))
        failed = [result for result in results if not result.passed]
        print(f"# {Path(run).name}: {len(results) - len(failed)}/{len(results)} checks pass")
        for result in failed:
            print(f"FAIL {result.ad} scene {result.scene}: {result.check} ({result.why})")


if __name__ == "__main__":
    main(sys.argv[1:])
