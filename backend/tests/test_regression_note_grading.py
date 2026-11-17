"""A note run's grading page: one card per case, two taps, and the judge's taps on remade clips."""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, sync_playwright
from pytest_httpserver import HTTPServer

from evals.regression.grading import main
from evals.regression.store import Store, write_json
from gateway.fake import FakeModel

from .test_regression_grading import DB, serve_page
from .test_regression_judge_grading import answer

RUN = "notes-1"
FIXED = "ridge-s3"
GAVE_UP = "crocs-s3"


def change_check(verdict: str = "not_made") -> dict[str, Any]:
    return {
        "judge": "N1:prompt-hash",
        "verdict": verdict,
        "asks": [
            {
                "verdict": value,
                "sees": "Scene 3 still shows the cash sliding through the closed wallet.",
                "model": "fake-model",
                "at": "2026-11-17T00:00:00Z",
                "input_tokens": 10,
                "cached_tokens": 0,
                "cache_write_tokens": 0,
                "output_tokens": 5,
                "cost_usd": 0.02,
            }
            for value in ("not_made", "made", "not_made")
        ],
    }


def note_store(tmp_path: Path) -> Store:
    """A saved note run in the shared #696-#699 layout: one fix, one give-up."""
    store = Store(tmp_path / "store")
    fake = FakeModel()
    clip = {n: fake.download(url=f"https://fake.fal/video-{n}.mp4") for n in range(1, 6)}
    store.write_run(
        RUN,
        {
            "kind": "notes",
            "name": RUN,
            "app_commit": "abc123",
            "cases": [FIXED, GAVE_UP],
            "estimate_usd": 8.0,
            "started_at": "2026-11-17T00:00:00Z",
            "results": {
                FIXED: {"status": "done", "why": None, "cost_usd": 4.1, "minutes": 9.5},
                GAVE_UP: {"status": "done", "why": None, "cost_usd": 2.0, "minutes": None},
            },
        },
    )
    for case, gave_up in [(FIXED, False), (GAVE_UP, True)]:
        scenario = case.removesuffix("-s3")
        write_json(
            store.scenario_dir(RUN, case) / "case.json",
            {
                "id": case,
                "scenario": scenario,
                "source_run": "baseline-2",
                "scenes": [1, 3],
                "note": f"In the {scenario} part with the cash, it goes through the wallet.",
                "grade": "Cash passes through the closed wallet in scene 3.",
                "graded_from": "baseline-2 grades",
                "source": {"run": "baseline-2", "scenario": scenario, "session_id": 7, "ad": 70},
                "copy_session_id": 8,
            },
        )
        base = store.export_dir(RUN, case) / case
        (base / "note-1").mkdir(parents=True)
        (base / "note-1/before.mp4").write_bytes(clip[1])
        scenes = []
        for number in (1, 2, 3):
            (base / f"note-1/before-s{number}.mp4").write_bytes(clip[number + 1])
            changed = number == 3 and not gave_up
            after = None
            if not gave_up:
                after = f"note-1/after-s{number}.mp4"
                (base / after).write_bytes(clip[5] if changed else clip[number + 1])
            scenes.append(
                {
                    "number": number,
                    "before_clip": f"note-1/before-s{number}.mp4",
                    "after_clip": after,
                    "changed": changed,
                }
            )
        note: dict[str, Any] = {
            "number": 1,
            "text": f"In the {scenario} part with the cash, it goes through the wallet.",
            "before": "note-1/before.mp4",
        }
        if not gave_up:
            (base / "note-1/after.mp4").write_bytes(clip[5])
            note["after"] = "note-1/after.mp4"
        write_json(
            base / "regression.json",
            {
                "scenario": {"key": scenario, "kind": "full", "link": "https://shop.test/p"},
                "scenes": [],
                "notes": [note],
                "note_case": {"id": case, "expected_scenes": [1, 3], "scenes": scenes},
            },
        )
        new_ad = (
            {"verdict": "fail", "reason": "The app said: I couldn't remake scene 3, sorry."}
            if gave_up
            else {"verdict": "pass", "reason": "A new finished ad was made."}
        )
        store.write_answers(
            RUN,
            case,
            {
                "answers": {
                    "note_new_ad": new_ad,
                    "note_right_scenes": {
                        "verdict": "fail",
                        "items": [
                            {"item": "s1", "verdict": "fail", "reason": "Scene 1 was not remade."},
                            {
                                "item": "s3",
                                "verdict": "fail" if gave_up else "pass",
                                "reason": "Remade." if not gave_up else "No new ad.",
                            },
                        ],
                    },
                    "note_untouched_same": {
                        "verdict": "pass",
                        "reason": "Untouched scenes kept their clips.",
                        "items": [],
                    },
                },
                "cost_usd": 2.0 if gave_up else 4.1,
                "minutes": None if gave_up else 9.5,
            },
        )
    # The change check (N1) and the eval judge's clip questions: s2 kept its clip, so its
    # saved answer must not reach the page.
    write_json(
        store.run_dir(RUN) / "judge-answers.json",
        {
            FIXED: {
                "N1": {"note": change_check()},
                "C6": {
                    "s3": answer(sees="The cash slides through the closed wallet."),
                    "s2": answer(),
                },
                "C5": {"s3": answer("pass")},
            }
        },
    )
    store.write_grades(RUN, {"round__notes": {"grade": None, "note": "Earlier words", "at": "x"}})
    return store


def build(store: Store, tmp_path: Path, name: str = "page") -> Path:
    target = tmp_path / name
    assert (
        main([RUN, "--output", str(target), "--cache", str(tmp_path / "cache")], store=store) == 0
    )
    return target


def test_a_note_run_page_has_one_card_per_case_with_changed_and_expected_scenes(
    tmp_path: Path,
) -> None:
    store = note_store(tmp_path)
    data = json.loads((build(store, tmp_path) / "page-data.json").read_text())
    assert data["kind"] == "notes"
    assert list(data["ads"]) == [FIXED, GAVE_UP]

    fixed = data["ads"][FIXED]
    assert fixed["note"].startswith("In the ridge part")
    assert fixed["grade"] == "Cash passes through the closed wallet in scene 3."
    # Scene 2 kept its clip and wasn't expected: only scenes 1 (expected) and 3 (remade) show.
    assert [(s["n"], s["changed"], s["expected"]) for s in fixed["scenes"]] == [
        (1, False, True),
        (3, True, True),
    ]
    assert fixed["before"] and fixed["after"]
    assert fixed["change"]["verdict"] == "not_made"
    # The judge's taps are on the remade clip only, and only where it failed.
    assert fixed["tapIds"] == [
        f"{FIXED}__note__gone",
        f"{FIXED}__note__no_new_break",
        f"{FIXED}__s3__clip__C6",
    ]
    # Only counted fails show; C5 passed on s3.
    assert [f["id"] for f in fixed["scenes"][1]["judge"]] == ["C6"]
    assert "judge" not in fixed["scenes"][0]
    assert data["tapFields"][f"{FIXED}__note__gone"] == "tap"
    assert data["tapFields"][f"{FIXED}__s3__clip__C6"] == "tap"

    gave_up = data["ads"][GAVE_UP]
    assert gave_up["gaveUp"] is True
    assert gave_up["tapIds"] == []
    assert gave_up["why"] == "The app said: I couldn't remake scene 3, sorry."
    assert gave_up["after"] is None
    # A give-up has nothing to grade, so its clips don't take room on the page.
    assert gave_up["scenes"] == []


def test_only_builds_part_of_a_note_run(tmp_path: Path) -> None:
    store = note_store(tmp_path)
    target = tmp_path / "part"
    assert (
        main(
            [RUN, "--output", str(target), "--only", GAVE_UP, "--cache", str(tmp_path / "c")],
            store=store,
        )
        == 0
    )
    assert list(json.loads((target / "page-data.json").read_text())["ads"]) == [GAVE_UP]


def _open(page: Page, path: Path, server: HTTPServer, case: str) -> list[str]:
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script(DB)
    page.goto(serve_page(path, server) + "#" + case)
    return errors


def test_two_taps_and_one_words_box_save_without_a_button_press_and_import(
    tmp_path: Path, httpserver: HTTPServer
) -> None:
    store = note_store(tmp_path)
    proof = os.environ.get("NOTE_GRADING_PROOF")
    target = build(store, Path(proof) if proof else tmp_path)
    words = "The cash now stays outside the wallet."
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1100})
        page.set_default_timeout(5000)
        errors = _open(page, target / "grading.html", httpserver, FIXED)
        card = page.locator(f'[data-note-case="{FIXED}"]')
        assert card.get_by_text("Cash passes through the closed wallet in scene 3.").count() == 1
        assert card.get_by_text("not remade", exact=True).count() == 1
        assert card.get_by_text("Change check: not made (2 of 3 asks)", exact=True).count() == 1
        gone = card.locator('[data-note-tap="gone"]')
        gone.get_by_role("button", name="Yes", exact=True).click()
        broke = card.locator('[data-note-tap="no_new_break"]')
        broke.get_by_role("button", name="No", exact=True).click()
        card.get_by_role("textbox", name=f"{FIXED} owner note words").fill(words)
        for doc_id, tap in [
            (f"{FIXED}__note__gone", "yes"),
            (f"{FIXED}__note__no_new_break", "no"),
        ]:
            page.wait_for_function(
                "([id, tap, words]) => window.savedDocs['grades/'+id]?.tap === tap && "
                "window.savedDocs['grades/'+id]?.note === words",
                arg=[doc_id, tap, words],
            )
        finding = card.locator('[data-judge-question="C6"]')
        assert finding.get_by_text("The cash slides through the closed wallet.").count() == 1
        finding.get_by_role("button", name="real flaw, don't ship", exact=True).click()
        page.wait_for_function(
            "window.savedDocs['grades/" + FIXED + "__s3__clip__C6']?.tap === 'real_flaw_dont_ship'"
        )
        if proof:
            for scheme in ("light", "dark"):
                page.emulate_media(color_scheme=scheme)
                card.screenshot(path=Path(proof) / f"note-card-{scheme}.png")
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            card.screenshot(path=Path(proof) / "note-card-phone.png")
            page.set_viewport_size({"width": 1400, "height": 1100})
        exported = tmp_path / "grades.json"
        exported.write_text(json.dumps(page.evaluate("gradesSnapshot()")))

        # A give-up shows what happened and takes no taps.
        page.locator("#rail .navb").filter(has_text=GAVE_UP).click()
        stopped = page.locator(f'[data-note-case="{GAVE_UP}"]')
        assert stopped.get_by_text("The app said: I couldn't remake scene 3, sorry.").count() == 1
        assert stopped.get_by_role("button").count() == 0
        assert stopped.get_by_role("textbox").count() == 0
        assert not errors
        browser.close()
    assert main([RUN, "--import-grades", str(exported)], store=store) == 0
    grades = store.read_grades(RUN)
    assert {k: (v["tap"], v["note"]) for k, v in grades.items() if "__note__" in k} == {
        f"{FIXED}__note__gone": ("yes", words),
        f"{FIXED}__note__no_new_break": ("no", words),
    }
    assert grades["round__notes"]["note"] == "Earlier words"
    assert store.read_judge_taps(RUN)[f"{FIXED}__s3__clip__C6"]["tap"] == "real_flaw_dont_ship"
    assert "## ridge-s3__note__gone [yes]" in store.read_grades_markdown(RUN)


def test_the_words_box_saves_after_a_pause_in_typing_and_on_leaving_it(
    tmp_path: Path, httpserver: HTTPServer
) -> None:
    store = note_store(tmp_path)
    target = build(store, tmp_path)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(5000)
        page.clock.install()
        _open(page, target / "grading.html", httpserver, FIXED)
        box = page.get_by_role("textbox", name=f"{FIXED} owner note words")
        gone = f"window.savedDocs['grades/{FIXED}__note__gone']?.note"
        box.fill("Typing")
        page.clock.run_for(200)
        assert page.evaluate(gone) is None
        page.clock.run_for(1000)
        assert page.evaluate(gone) == "Typing"
        box.fill("Typing, then leaving")
        box.blur()
        page.clock.run_for(10)
        assert page.evaluate(gone) == "Typing, then leaving"
        assert page.evaluate(f"window.savedDocs['grades/{FIXED}__note__no_new_break']?.note") == (
            "Typing, then leaving"
        )
        browser.close()


def test_import_keeps_a_note_tap_without_a_tap_out_of_the_grades(tmp_path: Path) -> None:
    store = note_store(tmp_path)
    doc_id = f"{FIXED}__note__gone"
    store.write_grades(RUN, {doc_id: {"tap": "no", "note": "", "at": "earlier"}})
    exported = tmp_path / "grades.json"
    exported.write_text(json.dumps({doc_id: {"tap": None, "note": "Still deciding", "at": "now"}}))
    assert main([RUN, "--import-grades", str(exported)], store=store) == 0
    assert store.read_grades(RUN) == {}


@pytest.mark.parametrize(
    "doc",
    [
        {"tap": "maybe", "note": "", "at": "now"},
        {"tap": "yes", "note": "", "at": "now", "grade": "pass"},
        {"grade": "pass", "note": "", "at": "now"},
    ],
)
def test_import_refuses_a_malformed_note_tap(tmp_path: Path, doc: dict[str, Any]) -> None:
    store = note_store(tmp_path)
    exported = tmp_path / "grades.json"
    exported.write_text(json.dumps({f"{FIXED}__note__gone": doc}))
    assert main([RUN, "--import-grades", str(exported)], store=store) == 1
    assert "note__gone" not in json.dumps(store.read_grades(RUN))


def test_import_refuses_a_tap_for_a_case_outside_the_run(tmp_path: Path) -> None:
    store = note_store(tmp_path)
    exported = tmp_path / "grades.json"
    exported.write_text(
        json.dumps({"other-s1__note__gone": {"tap": "yes", "note": "", "at": "now"}})
    )
    assert main([RUN, "--import-grades", str(exported)], store=store) == 1


def test_saved_words_on_either_tap_come_back_in_the_box_and_typing_keeps_them(
    tmp_path: Path, httpserver: HTTPServer
) -> None:
    store = note_store(tmp_path)
    # Julian tapped only "Nothing new broke?" and wrote words; the untapped gone doc was
    # dropped on import, so only the other doc holds his words.
    broke = f"{FIXED}__note__no_new_break"
    store.write_grades(RUN, {broke: {"tap": "yes", "note": "Saved words", "at": "2026-11-17"}})
    target = build(store, tmp_path)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(5000)
        _open(page, target / "grading.html", httpserver, FIXED)
        box = page.get_by_role("textbox", name=f"{FIXED} owner note words")
        page.wait_for_function(
            "name => document.querySelector(`[aria-label='${name}']`).value === 'Saved words'",
            arg=f"{FIXED} owner note words",
        )
        box.press("End")
        box.type(" and more")
        for doc_id in (f"{FIXED}__note__gone", broke):
            page.wait_for_function(
                "id => window.savedDocs['grades/'+id]?.note === 'Saved words and more'",
                arg=doc_id,
            )
        assert page.evaluate(f"window.savedDocs['grades/{broke}'].tap") == "yes"
        browser.close()


def test_a_case_without_saved_answers_keeps_its_taps_and_says_so(
    tmp_path: Path, httpserver: HTTPServer, capsys: pytest.CaptureFixture[str]
) -> None:
    store = note_store(tmp_path)
    (store.scenario_dir(RUN, FIXED) / "answers.json").unlink()
    target = build(store, tmp_path)
    assert f"{FIXED}: no saved code answers yet" in capsys.readouterr().err
    data = json.loads((target / "page-data.json").read_text())
    fixed = data["ads"][FIXED]
    assert fixed["gaveUp"] is False
    assert fixed["tapIds"][:2] == [f"{FIXED}__note__gone", f"{FIXED}__note__no_new_break"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(5000)
        errors = _open(page, target / "grading.html", httpserver, FIXED)
        card = page.locator(f'[data-note-case="{FIXED}"]')
        assert card.get_by_text("Code answers not ready", exact=False).count() == 1
        assert card.locator('[data-note-tap="gone"]').get_by_role("button").count() == 2
        assert not errors
        browser.close()


def test_a_stopped_case_takes_no_taps(tmp_path: Path) -> None:
    store = note_store(tmp_path)
    record = store.read_run(RUN)
    record["results"][FIXED] = {"status": "stopped", "why": "Spending reached 1.5x the estimate."}
    store.write_run(RUN, record)
    (store.scenario_dir(RUN, FIXED) / "answers.json").unlink()
    fixed = json.loads((build(store, tmp_path) / "page-data.json").read_text())["ads"][FIXED]
    assert fixed["gaveUp"] is True
    assert fixed["tapIds"] == []
    assert fixed["why"] == "Spending reached 1.5x the estimate."


@pytest.mark.parametrize(
    ("check", "words"),
    [
        (change_check("made"), "Change check: made"),
        (
            {"judge": "N1:prompt-hash", "asks": [], "error": "Azure refused the frames."},
            "Change check: not checked",
        ),
        (None, "Change check: not run yet."),
    ],
)
def test_the_change_checks_other_answers_are_shown(
    tmp_path: Path, httpserver: HTTPServer, check: dict[str, Any] | None, words: str
) -> None:
    store = note_store(tmp_path)
    answers = json.loads((store.run_dir(RUN) / "judge-answers.json").read_text())
    if check is None:
        del answers[FIXED]["N1"]
    else:
        answers[FIXED]["N1"]["note"] = check
    write_json(store.run_dir(RUN) / "judge-answers.json", answers)
    target = build(store, tmp_path)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(5000)
        _open(page, target / "grading.html", httpserver, FIXED)
        card = page.locator(f'[data-note-case="{FIXED}"]')
        assert card.get_by_text(words, exact=True).count() == 1
        if check and check.get("error"):
            assert card.get_by_text("Azure refused the frames.", exact=True).count() == 1
        browser.close()
