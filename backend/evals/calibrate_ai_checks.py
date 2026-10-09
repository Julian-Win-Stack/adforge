"""Run every AI judge on the graded cases in rule_cases.json with one model, and print how
each agrees with Julian's grades: on the "tune" half, which a judge's question may be written
against, and on the held-out half, which it never is. Every disagreement is listed with the
judge's reason, for Julian to see, and every verdict is saved to `--out` as JSON. From
backend/:

    uv run python -m evals.calibrate_ai_checks --model gpt-6.1-sol [--out FILE] [<rule> ...]
"""

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
django.setup()

from evals.ai_checks import Case, RuleVerdict, ask_majority, load_cases, score  # noqa: E402
from gateway.types import ModelReply, UnusableReply  # noqa: E402

# Azure text allows 1,000 requests and 1M tokens a minute per model
# (reference/rate-limits.md, 2026-10-09); 8 at once stays far under both.
AT_ONCE = 8


def main(model: str, out: Path | None, rules: list[str]) -> None:
    cases = [case for case in load_cases() if not rules or case.rule in rules]

    def ask(case: Case) -> ModelReply[RuleVerdict]:
        try:
            return ask_majority(case, model)
        except UnusableReply as error:
            # A refusal or unreadable answer counts as the judge getting the case wrong.
            wrong = "fail" if case.label == "pass" else "pass"
            verdict = RuleVerdict(decision=wrong, reason=f"NO USABLE ANSWER: {error}"[:300])
            return ModelReply(output=verdict, input_tokens=0, output_tokens=0)

    with ThreadPoolExecutor(AT_ONCE) as pool:
        replies = dict(zip((case.id for case in cases), pool.map(ask, cases), strict=True))
    print(f"# {model}: {len(cases)} cases")
    print(
        f"tokens in {sum(r.input_tokens for r in replies.values())}, "
        f"out {sum(r.output_tokens for r in replies.values())}"
    )
    for half, held_out in (("tune", False), ("held out", True)):
        scores, wrong = score(
            [case for case in cases if case.held_out == held_out],
            lambda case: replies[case.id].output,
        )
        print(f"## {half}")
        for line in scores:
            print(f"- {line.line()}")
        for case, verdict in wrong:
            print(f"  WRONG {case.id} (Julian: {case.label}): {verdict.reason}")
    if out is not None:
        out.write_text(
            json.dumps(
                {
                    "model": model,
                    "verdicts": [
                        {
                            "id": case.id,
                            "rule": case.rule,
                            "julian": case.label,
                            "held_out": case.held_out,
                            "decision": replies[case.id].output.decision,
                            "reason": replies[case.id].output.reason,
                        }
                        for case in cases
                    ],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("rules", nargs="*")
    args = parser.parse_args()
    main(args.model, args.out, args.rules)
