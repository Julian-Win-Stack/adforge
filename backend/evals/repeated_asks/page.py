"""Render a standalone results page from a saved report."""

import json
from pathlib import Path


def render(results: Path, out: Path) -> None:
    data = json.loads(results.read_text())
    embedded = json.dumps(data).replace("<", "\\u003c")
    template = Path(__file__).with_name("page.html").read_text()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(template.replace("__RESULTS_JSON__", embedded))
