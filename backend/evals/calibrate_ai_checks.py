"""Run every AI judge on the graded cases in rule_cases.json and print how each agrees with
Julian's grades: on the "tune" half, which a judge's question may be written against, and on
the held-out half, which it never is. Every disagreement is listed with the judge's reason,
for Julian to see. Costs a few cents (gpt-5-mini). From backend/:

    uv run python -m evals.calibrate_ai_checks [<rule> ...]
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
django.setup()

from evals.ai_checks import ask_model, load_cases, score  # noqa: E402

# Azure text allows 150 requests a minute per model (reference/rate-limits.md).
AT_ONCE = 4


def main(rules: list[str]) -> None:
    cases = [case for case in load_cases() if not rules or case.rule in rules]
    with ThreadPoolExecutor(AT_ONCE) as pool:
        verdicts = dict(zip((case.id for case in cases), pool.map(ask_model, cases), strict=True))
    for half, held_out in (("tune", False), ("held out", True)):
        scores, wrong = score(
            [case for case in cases if case.held_out == held_out], lambda case: verdicts[case.id]
        )
        print(f"## {half}")
        for line in scores:
            print(f"- {line.line()}")
        for case, verdict in wrong:
            print(f"  WRONG {case.id} (Julian: {case.label}): {verdict.reason}")


if __name__ == "__main__":
    main(sys.argv[1:])
