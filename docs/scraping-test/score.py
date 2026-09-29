"""Score each scraper's saved output against the hand-checked must-have facts.

Run from the repo root:  python3 docs/scraping-test/score.py
Reads facts.json and outputs/{current,firecrawl,gallery}.json; writes results.md.
"""

import json
import re
from pathlib import Path

HERE = Path(__file__).parent


def normal(text: str) -> str:
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.split()).casefold()


def photo_key(url: str) -> str:
    """The same picture at different sizes gives the same key."""
    name = url.split("?")[0].rstrip("/").split("/")[-1]
    stem = name.split(".")[0]
    return re.sub(r"_\d+x\d*.*$", "", stem).casefold()


def scraped(tool: str, page: dict) -> tuple[str, list[str]]:
    if "error" in page:
        return "", []
    if tool == "current":
        return page["text"], page["photo_urls"]
    return page.get("markdown", "") + "\n" + "\n".join(page.get("json_ld", [])), page.get("images", [])


def main() -> None:
    facts = json.loads((HERE / "facts.json").read_text())
    gallery = json.loads((HERE / "outputs/gallery.json").read_text())
    outputs = {
        tool: json.loads((HERE / f"outputs/{tool}.json").read_text())
        for tool in ("current", "firecrawl")
    }

    lines = ["# Scraper scores", "", "| Page | Facts: current | Facts: Firecrawl | Gallery photos: current | Gallery photos: Firecrawl | All images Firecrawl returned |", "|---|---|---|---|---|---|"]
    detail = ["", "## Missed facts", ""]
    totals = {"current": [0, 0], "firecrawl": [0, 0]}
    photo_totals = {"current": [0, 0], "firecrawl": [0, 0]}

    for key, entry in facts.items():
        row = [entry["product"]]
        photo_cells = []
        misses = {}
        for tool in ("current", "firecrawl"):
            text, photos = scraped(tool, outputs[tool][key])
            text_n = normal(text)
            found = [f for f in entry["facts"] if any(normal(s) in text_n for s in f["look_for"])]
            misses[tool] = [f["fact"] for f in entry["facts"] if f not in found]
            totals[tool][0] += len(found)
            totals[tool][1] += len(entry["facts"])
            row.append(f"{len(found)}/{len(entry['facts'])}" if entry["facts"] else "n/a")

            wanted = gallery.get(key)
            if wanted:
                have = {photo_key(u) for u in photos}
                got = sum(photo_key(u) in have for u in wanted)
                photo_totals[tool][0] += got
                photo_totals[tool][1] += len(wanted)
                photo_cells.append(f"{got}/{len(wanted)}")
            else:
                photo_cells.append("n/a")
        firecrawl_images = len(scraped("firecrawl", outputs["firecrawl"][key])[1])
        lines.append("| " + " | ".join(row + photo_cells + [str(firecrawl_images)]) + " |")

        if misses["current"] or misses["firecrawl"]:
            detail.append(f"**{entry['product']}**")
            for tool in ("current", "firecrawl"):
                for fact in misses[tool]:
                    detail.append(f"- {tool}: {fact}")
            detail.append("")

    lines.append(
        f"| **Total** | **{totals['current'][0]}/{totals['current'][1]}** | "
        f"**{totals['firecrawl'][0]}/{totals['firecrawl'][1]}** | "
        f"**{photo_totals['current'][0]}/{photo_totals['current'][1]}** | "
        f"**{photo_totals['firecrawl'][0]}/{photo_totals['firecrawl'][1]}** | |"
    )
    (HERE / "results.md").write_text("\n".join(lines + detail) + "\n")
    print("\n".join(lines + detail))


if __name__ == "__main__":
    main()
