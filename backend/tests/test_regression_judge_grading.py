"""The regression page shows eval-judge findings and saves Julian's three taps."""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import sync_playwright
from pytest_httpserver import HTTPServer

from evals.regression.grading import main
from evals.regression.questions import judge_questions

from .test_regression_grading import DB, fixture_store, serve_page


def answer(
    verdict: str = "fail", *, sees: str = "The cap floats above the product."
) -> dict[str, Any]:
    return {
        "judge": "question:prompt-hash",
        "verdict": verdict,
        "asks": [
            {
                "verdict": value,
                "sees": sees,
                "when": "2.5 s",
                "on_what": "product",
                "model": "fake-model",
                "at": "2026-11-16T00:00:00Z",
                "input_tokens": 10,
                "cached_tokens": 0,
                "cache_write_tokens": 0,
                "output_tokens": 5,
                "cost_usd": 0.01,
            }
            for value in ("fail", "pass", "fail")
        ],
    }


def test_judge_findings_save_three_taps_and_notes_without_a_button_press(
    tmp_path: Path, httpserver: HTTPServer
) -> None:
    store = fixture_store(tmp_path)
    store.write_judge_answers(
        "new",
        {
            "mug": {
                "P1": {"s1": answer()},
                "P4": {"s1": answer(sees="The product is held by the wrong part.")},
                "C6": {"s1": answer()},
                **{q.id: {"s1": answer("pass")} for q in judge_questions("clips") if q.id != "C6"},
                "A11": {"ad": answer("pass")},
            }
        },
    )
    proof = os.environ.get("REGRESSION_JUDGE_GRADING_PROOF")
    target = Path(proof) / "page" if proof else tmp_path / "page"
    assert (
        main(["new", "--output", str(target), "--cache", str(tmp_path / "cache")], store=store) == 0
    )
    path = target / "grading.html"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1100})
        page.set_default_timeout(5000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(DB)
        page.goto(serve_page(path, httpserver) + "#mug")
        finding = page.locator('[data-judge-question="P1"]')
        assert finding.get_by_text("Real product", exact=True).count() == 1
        assert finding.get_by_text("The cap floats above the product.", exact=True).count() == 1
        assert finding.get_by_text("2.5 s · product", exact=True).count() == 1
        assert finding.get_by_text("Eval judge failed 2 of 3 asks", exact=True).count() == 1
        for question, tap, words in [
            ("P1", "not_a_flaw", "not a flaw"),
            ("P4", "real_flaw_ship", "real flaw, fine to ship"),
            ("C6", "real_flaw_dont_ship", "real flaw, don't ship"),
        ]:
            finding = page.locator(f'[data-judge-question="{question}"]')
            finding.get_by_role("button", name=words, exact=True).click()
            finding.get_by_role("textbox").fill("The written note saves while I type.")
            stage = "clip" if question.startswith("C") else "picture"
            doc_id = f"mug__s1__{stage}__{question}"
            page.wait_for_function(
                "([id,tap]) => window.savedDocs['grades/'+id]?.tap === tap && "
                "window.savedDocs['grades/'+id]?.note === 'The written note saves while I type.'",
                arg=[doc_id, tap],
            )
            assert (
                finding.get_by_role("button", name=words, exact=True).get_attribute("aria-pressed")
                == "true"
            )
            assert finding.locator('button[aria-pressed="true"]').evaluate(
                "element => getComputedStyle(element).backgroundColor"
            ) != finding.locator('button[aria-pressed="false"]').first.evaluate(
                "element => getComputedStyle(element).backgroundColor"
            )
        export = page.evaluate("gradesSnapshot()")
        if proof:
            directory = Path(proof)
            directory.mkdir(parents=True, exist_ok=True)
            page.locator("#s1").screenshot(path=directory / "judge-three-taps.png")
            page.locator('[data-judge-question="P1"]').screenshot(
                path=directory / "judge-finding.png"
            )
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.locator("#s1").screenshot(path=directory / "judge-three-taps-phone.png")
        assert page.get_by_text("null", exact=True).count() == 0
        assert not errors
        browser.close()
    exported = tmp_path / "grades.json"
    exported.write_text(json.dumps(export))
    assert main(["new", "--import-grades", str(exported)], store=store) == 0
    assert {key: value["tap"] for key, value in store.read_judge_taps("new").items()} == {
        "mug__s1__picture__P1": "not_a_flaw",
        "mug__s1__picture__P4": "real_flaw_ship",
        "mug__s1__clip__C6": "real_flaw_dont_ship",
    }


def test_trusted_passes_are_hidden_while_untrusted_and_unjudged_items_stay(
    tmp_path: Path, httpserver: HTTPServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evals.judge import trust

    store = fixture_store(tmp_path)
    answers = {
        "mug": {
            question.id: {"s1": answer("pass")}
            for stage in ("pictures", "clips")
            for question in judge_questions(stage)
        },
        "chair": {
            "P1": {"s1": answer("pass")},
            "P4": {
                "s1": {"judge": "P4:hash", "asks": [], "error": "The model refused this picture."}
            },
        },
    }
    store.write_judge_answers("new", answers)
    monkeypatch.setattr(trust, "trusted_questions", lambda: frozenset({"P1", "P4"}))
    target = tmp_path / "page"
    assert (
        main(["new", "--output", str(target), "--cache", str(tmp_path / "cache")], store=store) == 0
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(5000)
        page.goto(serve_page(target / "grading.html", httpserver) + "#mug")
        assert page.locator('[id="t-mug__s1__picture"]').count() == 0
        assert page.locator('[id="t-mug__s1__clip"]').count() == 1
        assert page.locator('[id="t-mug__ad"]').count() == 1
        assert page.locator('[id="t-mug__no-worse"]').count() == 1
        page.locator("#rail .navb").filter(has_text="chair").click()
        assert page.locator('[id="t-chair__s1__picture"]').count() == 1
        finding = page.locator('[data-judge-question="P4"]')
        assert finding.get_by_text("Not judged", exact=True).count() == 1
        assert finding.get_by_text("The model refused this picture.", exact=True).count() == 1
        browser.close()


def test_an_untapped_note_autosaves_but_does_not_import_a_judge_grade(
    tmp_path: Path, httpserver: HTTPServer
) -> None:
    store = fixture_store(tmp_path)
    store.write_judge_answers("new", {"mug": {"P1": {"s1": answer()}}})
    target = tmp_path / "page"
    assert (
        main(["new", "--output", str(target), "--cache", str(tmp_path / "cache")], store=store) == 0
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(5000)
        page.add_init_script(DB)
        page.goto(serve_page(target / "grading.html", httpserver) + "#mug")
        note = page.locator('[id="t-mug__s1__picture__P1"]')
        note.fill("I am still deciding.")
        page.wait_for_function(
            "window.savedDocs['grades/mug__s1__picture__P1']?.note === 'I am still deciding.'"
        )
        assert page.evaluate("window.savedDocs['grades/mug__s1__picture__P1'].tap") is None
        exported = tmp_path / "grades.json"
        exported.write_text(json.dumps(page.evaluate("gradesSnapshot()")))
        browser.close()
    assert main(["new", "--import-grades", str(exported)], store=store) == 0
    assert store.read_judge_taps("new") == {}


def test_removing_a_judge_tap_removes_its_previous_imported_grade(tmp_path: Path) -> None:
    store = fixture_store(tmp_path)
    doc_id = "mug__s1__picture__P1"
    store.write_judge_taps(
        "new", {doc_id: {"tap": "real_flaw_dont_ship", "note": "", "at": "2026-11-16T00:00:00Z"}}
    )
    exported = tmp_path / "grades.json"
    exported.write_text(json.dumps({doc_id: {"tap": None, "note": "Still deciding", "at": "now"}}))
    assert main(["new", "--import-grades", str(exported)], store=store) == 0
    assert store.read_judge_taps("new") == {}


def test_presenters_stay_visible_even_when_all_their_judge_questions_are_trusted(
    tmp_path: Path, httpserver: HTTPServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evals.judge import trust
    from evals.regression.store import write_json

    store = fixture_store(tmp_path)
    base = store.export_dir("new", "mug") / "mug"
    write_json(base / "grading-records.json", {"scenes": [{"number": 1, "kind": "talking"}]})
    questions = [q for q in judge_questions() if q.stage in ("pictures", "clips")]
    store.write_judge_answers("new", {"mug": {q.id: {"s1": answer("pass")} for q in questions}})
    monkeypatch.setattr(trust, "trusted_questions", lambda: frozenset(q.id for q in questions))
    target = tmp_path / "page"
    assert (
        main(["new", "--output", str(target), "--cache", str(tmp_path / "cache")], store=store) == 0
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(5000)
        page.goto(serve_page(target / "grading.html", httpserver) + "#mug")
        assert page.locator('[id="t-mug__s1__picture"]').count() == 1
        assert page.locator('[id="t-mug__s1__clip"]').count() == 1
        assert page.locator('[id="t-mug__ad"]').count() == 1
        browser.close()


def test_an_empty_judge_answers_file_does_not_change_the_existing_page(tmp_path: Path) -> None:
    store = fixture_store(tmp_path)
    first = tmp_path / "first"
    assert (
        main(["new", "--output", str(first), "--cache", str(tmp_path / "cache")], store=store) == 0
    )
    original = (first / "grading.html").read_text()
    store.write_judge_answers("new", {})
    second = tmp_path / "second"
    assert (
        main(["new", "--output", str(second), "--cache", str(tmp_path / "cache")], store=store) == 0
    )
    assert (second / "grading.html").read_text() == original
    assert "tapFields" not in json.loads((second / "page-data.json").read_text())
