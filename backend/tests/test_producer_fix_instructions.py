"""The documented, dated limitations of scene fixes."""

from pathlib import Path


def test_the_fix_limits_are_written_down_and_dated() -> None:
    docs = Path(__file__).resolve().parents[2] / "docs"
    limitations = (docs / "limitations.md").read_text()
    for limit in [
        "**Nothing says a fix worked.**",
        "**No escalation on a repeated note.**",
    ]:
        entry = next(line for line in limitations.splitlines() if limit in line)
        assert "2026-10-11" in entry
    adr = (docs / "adr" / "0005-one-agent-first.md").read_text()
    assert "_Note, 2026-10-12 (route B step 1, #137): nothing stops a running step yet." in adr
