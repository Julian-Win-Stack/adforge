"""The documented, dated limitations of scene fixes."""

from pathlib import Path


def test_the_fix_limits_are_written_down_and_dated() -> None:
    docs = Path(__file__).resolve().parents[2] / "docs"
    limitations = (docs / "limitations.md").read_text()
    entry = next(
        line for line in limitations.splitlines() if "**Nothing says a fix worked.**" in line
    )
    assert "2026-10-11" in entry
    asked_change = next(
        line
        for line in limitations.splitlines()
        if "**An asked change closes on a scene after three tries.**" in line
    )
    assert "for the rest of the session" in asked_change
    assert "how-it-works.md#asked-changes" in asked_change
    adr = (docs / "adr" / "0005-one-agent-first.md").read_text()
    assert "_Note, 2026-10-12 (route B step 1, #137): nothing stops a running step yet." in adr
