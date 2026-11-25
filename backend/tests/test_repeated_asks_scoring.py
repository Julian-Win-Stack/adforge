"""The repeated-ask report reads app filings and scores saved owner replies."""

from pathlib import Path
from typing import Any

import pytest

from evals.repeated_asks.cases import ASKED_CHANGES_CASES_FILE, load_cases
from evals.repeated_asks.per_ask import summarize


def test_scoring_marks_both_filing_mix_ups_and_counts_self_started_filming() -> None:
    cases = load_cases(ASKED_CHANGES_CASES_FILE)
    record = {
        "case": "solved-01-brush",
        "repeat": 1,
        "try_limit": 3,
        "asks": [
            {"number": 1, "message_id": 10, "runs": [{"origin": "owner"}]},
            {"number": 2, "message_id": 20, "runs": [{"origin": "producer"}]},
            {"number": 3, "message_id": 30, "runs": []},
            {"number": 4, "message_id": 40, "runs": [{"origin": "owner"}]},
        ],
        "asked_changes": [
            {
                "number": 1,
                "description": "Closer brush",
                "scenes": [
                    {
                        "scene": 2,
                        "tries": 1,
                        "closed": False,
                        "asks": [
                            {"message_id": 10, "runs": []},
                            {"message_id": 40, "runs": []},
                        ],
                    }
                ],
            },
            {
                "number": 2,
                "description": "That brush nearer",
                "scenes": [
                    {
                        "scene": 2,
                        "tries": 1,
                        "closed": False,
                        "asks": [{"message_id": 20, "runs": []}],
                    }
                ],
            },
        ],
    }

    scores = summarize([record], cases)["groups"]["normal"]["scoring"]

    assert scores["self_started"] == 1
    assert scores["repeat_filed_as_new"] == 1
    assert scores["different_filed_as_old"] == 1
    assert scores["tries_per_change"] == {1: 2}


def test_saved_replies_can_be_judged_explicitly_and_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from evals.repeated_asks.__main__ import main
    from gateway import gateway
    from gateway.types import ModelReply

    seen: list[dict[str, Any]] = []

    def answer(request: Any, *, azure_only: bool = False) -> ModelReply[Any]:
        assert azure_only
        seen.append(request.handoff.model_dump())
        return ModelReply(output=request.output(answer="yes"), input_tokens=20, output_tokens=2)

    monkeypatch.setattr(gateway, "ask_measured", answer)
    path = tmp_path / "report.json"
    checks: dict[str, dict[str, Any]] = {
        "gave_up_early": {
            "owner": "Still wrong",
            "reply": "I gave up",
            "changes": [],
            "answer": None,
        },
        "honest_close": {
            "owner": "Still wrong",
            "reply": "I failed; use this angle",
            "changes": [],
            "answer": None,
        },
    }
    path.write_text(
        json.dumps(
            {
                "groups": {"normal": {}},
                "sessions": [
                    {"group": "normal", "scoring": {}, "asks": [{"reply_checks": checks}]}
                ],
            }
        )
    )

    assert main(["judge", "--results", str(path)]) == 0
    assert seen == []
    assert main(["judge", "--results", str(path), "--yes"]) == 0
    assert [handoff["reply"] for handoff in seen] == ["I gave up", "I failed; use this angle"]
    scores = json.loads(path.read_text())["groups"]["normal"]["scoring"]
    assert scores["replies"] == {"gave_up_early": {"yes": 1}, "honest_close": {"yes": 1}}
    assert main(["judge", "--results", str(path), "--yes"]) == 0
    assert len(seen) == 2


def test_a_long_gap_still_scores_the_reply_to_the_original_closed_change() -> None:
    case_id = "much-later-01-brush"
    cases = load_cases(ASKED_CHANGES_CASES_FILE)
    record = {
        "case": case_id,
        "repeat": 1,
        "try_limit": 3,
        "asks": [
            {"number": number, "message_id": number, "runs": [], "said": message.say}
            for number, message in enumerate(cases[case_id].messages, 1)
        ],
        "asked_changes": [
            {
                "number": 1,
                "description": "Brush through the hair",
                "scenes": [
                    {
                        "scene": 2,
                        "tries": 3,
                        "closed": True,
                        "asks": [
                            {
                                "message_id": number,
                                "runs": [{"try_number": number, "outcome": "gave_up"}],
                            }
                            for number in (1, 2, 3)
                        ],
                    }
                ],
            }
        ],
    }

    reports = summarize([record], cases)["sessions"][0]["asks"]
    assert [ask["number"] for ask in reports if "honest_close" in ask["reply_checks"]] == [3, 4, 10]
    assert reports[-1]["reply_checks"]["honest_close"]["changes"] == [
        "Scene 2: Brush through the hair"
    ]


def test_a_billed_judge_failure_is_saved_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from adforge import monitoring
    from evals.repeated_asks.__main__ import main
    from gateway import gateway
    from gateway.types import ModelReply, UnusableReply
    from jobs.notices import EVAL_JUDGE_UNAVAILABLE

    failures = []

    def fail(request: Any, *, azure_only: bool) -> ModelReply[Any]:
        assert azure_only
        raise UnusableReply("The judge refused the reply", input_tokens=80, output_tokens=4)

    def record_notice(kind: str, text: str, **tags: Any) -> None:
        failures.append((kind, text, tags))

    monkeypatch.setattr(gateway, "ask_measured", fail)
    monkeypatch.setattr(monitoring, "failed", record_notice)
    checks = {
        "honest_close": {
            "owner": "Please try again",
            "reply": "Here is a choice",
            "changes": [],
            "answer": None,
        }
    }
    path = tmp_path / "report.json"
    path.write_text(
        json.dumps(
            {
                "groups": {"normal": {}},
                "sessions": [
                    {"group": "normal", "scoring": {}, "asks": [{"reply_checks": checks}]},
                ],
            }
        )
    )

    command = ["judge", "--results", str(path), "--yes"]
    assert main(command) == 1
    rejected = json.loads(path.read_text())
    check = rejected["sessions"][0]["asks"][0]["reply_checks"]["honest_close"]
    assert check["answer"] is None
    assert rejected["groups"]["normal"]["scoring"]["replies"] == {"honest_close": {"pending": 1}}
    (failure,) = check["failed_asks"]
    assert (failure["input_tokens"], failure["output_tokens"]) == (80, 4)
    assert failure["cost_usd"] > 0
    assert failures == [
        (EVAL_JUDGE_UNAVAILABLE.name, failure["error"], {"question": "honest_close"})
    ]

    def answer(request: Any, *, azure_only: bool) -> ModelReply[Any]:
        assert azure_only
        return ModelReply(output=request.output(answer="yes"), input_tokens=20, output_tokens=2)

    monkeypatch.setattr(gateway, "ask_measured", answer)
    assert main(command) == 0
    answered = json.loads(path.read_text())
    check = answered["sessions"][0]["asks"][0]["reply_checks"]["honest_close"]
    assert check["answer"] == "yes"
    assert check["failed_asks"] == [failure]


def test_results_page_uses_saved_data_and_switches_tabs_with_the_keyboard(tmp_path: Path) -> None:
    import json

    from playwright.sync_api import expect, sync_playwright

    from evals.repeated_asks.__main__ import main

    data = summarize([], load_cases(ASKED_CHANGES_CASES_FILE))
    data["sessions"] = [
        {
            "case": "saved-01",
            "repeat": 1,
            "asks": [
                {
                    "number": 4,
                    "tried": False,
                    "runs": [],
                    "chat": [],
                    "reply_checks": {
                        "honest_close": {
                            "owner": "Please try again.",
                            "reply": (
                                "I couldn't make it. I can show a close-up instead."
                                "</script><img src=x>"
                            ),
                            "answer": "yes",
                        }
                    },
                }
            ],
        }
    ]
    report = tmp_path / "report.json"
    report.write_text(json.dumps(data))
    result = tmp_path / "results.html"
    assert main(["page", "--results", str(report), "--out", str(result)]) == 0

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(result.as_uri())
        overview = page.get_by_role("tab", name="Overview")
        expect(overview).to_have_attribute("aria-selected", "true")
        expect(page.get_by_role("heading", name="Headlines")).to_be_visible()
        overview.focus()
        page.keyboard.press("ArrowRight")
        expect(page.get_by_role("tab", name="Per ask")).to_be_focused()
        expect(page.get_by_role("heading", name="Did the app try again?")).to_be_visible()
        page.get_by_role("tab", name="Sessions").click()
        page.get_by_role("tabpanel", name="Sessions").get_by_text("saved-01", exact=False).click()
        expect(page.get_by_text("Ask 4 · No filming")).to_be_visible()
        page.get_by_role("tab", name="Closing replies").click()
        closing = page.get_by_role("tabpanel", name="Closing replies")
        expect(closing.get_by_text("I couldn't make it.", exact=False)).to_be_visible()
        assert page.locator("img").count() == 0
        page.keyboard.press("End")
        expect(page.get_by_role("heading", name="The owner messages we played")).to_be_visible()
        page.get_by_label("Group").select_option("normal")
        expect(page.get_by_text("healthy-01-brush", exact=False)).to_be_visible()
        expect(page.get_by_text("hard-01-lens", exact=False)).to_be_hidden()
        assert not errors
        browser.close()
