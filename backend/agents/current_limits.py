"""The app's current limitations, documented once and told to the producer."""

from functools import cache
from pathlib import Path


@cache
def current_limits() -> str:
    """Read only the producer's section, including in the shipped backend image."""
    here = Path(__file__).resolve()
    paths = (here.parents[2] / "docs/limitations.md", here.parents[1] / "docs/limitations.md")
    path = next((path for path in paths if path.is_file()), paths[0])
    section = path.read_text().partition("## Can't do (told to the producer)\n")[2]
    section = section.split("\n## ")[0]
    limits = [line for line in section.splitlines() if line.startswith("- **Can't do:**")]
    if not limits:
        raise ValueError(f"No producer limits found in {path}")
    return (
        'Current app limits (docs/limitations.md): say "can\'t, because" and offer what can '
        "be done before any paid call.\n" + "\n".join(limits)
    )
