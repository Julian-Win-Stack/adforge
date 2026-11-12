"""The producer reads the current can't-do list from the shipped documentation."""

from pathlib import Path

import pytest

from agents import current_limits
from agents.producer import PRODUCER


def test_producer_is_told_every_documented_limitation() -> None:
    doc = Path(__file__).resolve().parents[2] / "docs/limitations.md"
    section = doc.read_text().split("## Can't do (told to the producer)\n")[1].split("\n## ")[0]
    bullets = [line for line in section.splitlines() if line.startswith("- ")]
    assert len(bullets) == 9
    assert all(bullet in PRODUCER.instructions for bullet in bullets)


@pytest.mark.parametrize(
    "document",
    [
        "# Limitations\n- **Can't do:** Developer note outside the producer's section.\n",
        "## Can't do (told to the producer)\n\n## Developer notes\n"
        "- **Can't do:** Developer note outside the producer's section.\n",
    ],
    ids=["missing-section", "empty-section"],
)
def test_unavailable_producer_limits_fail_loudly(
    document: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "backend/agents/current_limits.py"
    doc = tmp_path / "docs/limitations.md"
    doc.parent.mkdir()
    doc.write_text(document)
    monkeypatch.setattr(current_limits, "__file__", str(source))
    current_limits.current_limits.cache_clear()
    try:
        with pytest.raises(ValueError, match="No producer limits found"):
            current_limits.current_limits()
    finally:
        current_limits.current_limits.cache_clear()
