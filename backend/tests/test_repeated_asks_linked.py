"""The linked-scene eval's case file and saved results."""

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from evals.repeated_asks import cases


def test_the_linked_case_set_covers_all_five_groups() -> None:
    linked = cases.load_cases(cases.LINKED_CASES_FILE)
    cases.check_counts(linked)

    assert Counter(case.group for case in linked.values()) == {
        "one_scene_fails": 8,
        "picks_way_out": 6,
        "all_scenes_fail": 4,
        "scene_joins_later": 4,
        "works_everywhere": 4,
    }
    assert Counter(case.way_out for case in linked.values() if case.way_out) == {
        "shot": 2,
        "approach": 2,
        "restore": 2,
    }
    joined = linked["joins-later-01-roller"]
    assert joined.initial_scenes == 4
    assert "scene 4" in joined.messages[1].say
    assert [message.expected_try for message in joined.messages] == [True, True, False]
    assert linked["one-scene-fails-01-brush"].messages[0].by_scene == {
        1: "passes",
        2: "gives_up",
        3: "passes",
    }


def test_page_shows_linked_choices_alongside_the_earlier_results(tmp_path: Path) -> None:
    from playwright.sync_api import expect, sync_playwright

    from evals.repeated_asks.__main__ import main
    from evals.repeated_asks.per_ask import summarize

    earlier = tmp_path / "earlier.json"
    earlier.write_text(json.dumps(summarize([], cases.load_cases(cases.ASKED_CHANGES_CASES_FILE))))
    linked = tmp_path / "linked.json"
    record = {
        "case": "picks-shot-01-brush",
        "repeat": 1,
        "asks": [{"number": 5, "runs": [{"scene": 2, "origin": "owner"}]}],
    }
    linked.write_text(json.dumps(summarize([record], cases.load_cases(cases.LINKED_CASES_FILE))))
    html = tmp_path / "results.html"
    assert (
        main(["page", "--results", str(earlier), "--results", str(linked), "--out", str(html)]) == 0
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(html.as_uri())
        page.get_by_role("tab", name="Per ask").click()
        expect(page.get_by_role("heading", name="One scene keeps failing")).to_be_visible()
        expect(page.get_by_role("heading", name="Normal sessions")).to_be_visible()
        page.get_by_role("tab", name="Linked scenes").click()
        choice = page.get_by_role("row").filter(has_text="picks-shot-01-brush")
        expect(choice.get_by_role("cell", name="2", exact=True)).to_be_visible()
        expect(choice.get_by_role("cell", name="Yes", exact=True)).to_be_visible()
        browser.close()


@pytest.mark.parametrize(
    ("case_id", "filmed", "restored", "exact"),
    [
        ("picks-shot-01-brush", [2], [], True),
        ("picks-shot-01-brush", [1, 2], [], False),
        ("picks-all-02-cream", [3, 1, 2], [], True),
        ("picks-all-02-cream", [3, 1, 2, 2], [], False),
        (
            "picks-back-03-floss",
            [],
            [{"scene": 1, "version": 2}, {"scene": 3, "version": 1}],
            False,
        ),
    ],
)
def test_the_choice_score_requires_exactly_the_scenes_and_versions_the_owner_picked(
    case_id: str, filmed: list[int], restored: list[dict[str, int]], exact: bool
) -> None:
    from evals.repeated_asks.per_ask import summarize

    record = {
        "case": case_id,
        "repeat": 1,
        "asks": [
            {
                "number": 5,
                "runs": [{"scene": scene, "origin": "owner"} for scene in filmed],
                "restored": restored,
            }
        ],
    }
    report = summarize([record], cases.load_cases(cases.LINKED_CASES_FILE))
    choices = report["groups"]["picks_way_out"]["linked"]["choices"]
    assert [choice["exact"] for choice in choices] == [exact]


def test_linked_reply_question_is_judged_from_the_saved_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evals.repeated_asks.__main__ import main
    from gateway import gateway
    from gateway.types import ModelReply

    seen: list[str] = []

    def answer(request: Any, *, azure_only: bool) -> ModelReply[Any]:
        assert azure_only
        seen.append(request.handoff.reply)
        return ModelReply(output=request.output(answer="yes"), input_tokens=20, output_tokens=2)

    monkeypatch.setattr(gateway, "ask_measured", answer)
    reply = "Scenes 1 and 3 match. For scene 2, I can try a new shot, change all three, or revert."
    data = {
        "groups": {"one_scene_fails": {}},
        "sessions": [
            {
                "group": "one_scene_fails",
                "scoring": {},
                "linked": {"refilmed_other_scenes": 0},
                "asks": [
                    {
                        "reply_checks": {
                            "linked_ways_out": {
                                "owner": "Please fix scene 2.",
                                "reply": reply,
                                "changes": ["Scene 2 still needs the change"],
                                "answer": None,
                            }
                        }
                    }
                ],
            }
        ],
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(data))
    assert main(["judge", "--results", str(path)]) == 0
    assert not seen
    assert main(["judge", "--results", str(path), "--yes"]) == 0
    judged = json.loads(path.read_text())
    assert seen == [reply]
    check = judged["sessions"][0]["asks"][0]["reply_checks"]["linked_ways_out"]
    assert (check["reply"], check["answer"]) == (reply, "yes")
    assert judged["groups"]["one_scene_fails"]["scoring"]["replies"] == {
        "linked_ways_out": {"yes": 1}
    }
