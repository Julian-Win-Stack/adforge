"""The video model's documented hard limits, told to the producer and every scene agent."""

from functools import cache
from pathlib import Path


@cache
def hard_limits() -> str:
    """The `Hard limit` bullets of docs/h3-limits.md, also shipped alongside the backend in
    Docker, so adding a limit is a one-line doc edit."""
    here = Path(__file__).resolve()
    paths = (here.parents[2] / "docs/h3-limits.md", here.parents[1] / "docs/h3-limits.md")
    path = next((path for path in paths if path.is_file()), paths[0])
    limits = [
        line for line in path.read_text().splitlines() if line.startswith("- **Hard limit:**")
    ]
    if not limits:
        raise ValueError(f"No hard limits found in {path}")
    return "Video-model hard limits (docs/h3-limits.md):\n" + "\n".join(limits)
