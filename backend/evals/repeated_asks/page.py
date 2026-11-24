"""Render a standalone results page from a saved report."""

import json
from pathlib import Path


def render(results: list[Path], out: Path) -> None:
    first, *rest = results
    data = json.loads(first.read_text())
    for path in rest:
        more = json.loads(path.read_text())
        if duplicate_groups := set(data["groups"]).intersection(more["groups"]):
            raise ValueError(
                f"Groups occur in more than one report: {', '.join(sorted(duplicate_groups))}"
            )
        data["groups"].update(more["groups"])
        data["sessions"].extend(more["sessions"])
        data.setdefault("scripts", []).extend(more.get("scripts", []))
        if "main_baseline" in more:
            data["main_baseline"] = more["main_baseline"]
    embedded = json.dumps(data).replace("<", "\\u003c")
    template = Path(__file__).with_name("page.html").read_text()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(template.replace("__RESULTS_JSON__", embedded))
