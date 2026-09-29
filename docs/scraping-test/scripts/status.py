"""Job B step 3: did each scraper get the product page at all? Classifies from the saved
output (no re-scrape), the same rule for both scrapers.

Usage: python status.py <current.json> <firecrawl.json> <pages100-meta.json> <status.json>
"""
import json, re, sys
from collections import Counter
from pathlib import Path

BLOCK_WORDS = re.compile(
    r"access denied|verify you are human|verify you're human|are you a robot|robot check|"
    r"unusual traffic|request blocked|pardon our interruption|bot detection|press & hold|"
    r"checking your browser|just a moment|enable javascript and cookies to continue|"
    r"api-services-support@amazon\.com|reference #\d|human verification|security check|"
    r"you have been blocked|blocked by an extension",
    re.I,
)
THIN = 2500  # under this many characters of text, a 200 is a JavaScript shell, not the page


def classify(http_status, text: str, error: str | None) -> tuple[str, str]:
    text = (text or "").strip()
    if http_status in (403, 429, 503):
        return "blocked", f"HTTP {http_status}"
    if error and http_status is None:
        return "error", "no HTTP answer (connection hung or reset)" if "OutsideServiceDown" in error or "Timeout" in error or "Connect" in error else error[:80]
    if error:
        return "error", error[:80]
    if not text:
        return "empty", "200 with no text"
    if len(text) < 3000 and BLOCK_WORDS.search(text[:3000]):
        return "blocked", "200 with a bot-check page"
    if len(text) < THIN:
        return "thin", f"200 but only {len(text)} chars (JavaScript-rendered shell)"
    return "ok", ""


def main() -> None:
    cur = json.loads(Path(sys.argv[1]).read_text())
    fc = json.loads(Path(sys.argv[2]).read_text())
    meta = {m["key"]: m for m in json.loads(Path(sys.argv[3]).read_text())}
    out = {}
    for key in meta:
        c, f = cur.get(key, {}), fc.get(key, {})
        cs, cw = classify(c.get("http_status"), c.get("text", ""), c.get("error"))
        fs, fw = classify(f.get("http_status"), f.get("markdown", ""), f.get("error"))
        out[key] = {"store": meta[key]["store"], "store_type": meta[key]["store_type"], "category": meta[key]["category"],
                    "current": cs, "current_why": cw, "current_chars": len(c.get("text", "") or ""), "current_photos": len(c.get("photo_urls") or []),
                    "firecrawl": fs, "firecrawl_why": fw, "firecrawl_chars": len(f.get("markdown", "") or ""), "firecrawl_images": len(f.get("images") or [])}
    Path(sys.argv[4]).write_text(json.dumps(out, indent=1))
    for tool in ("current", "firecrawl"):
        print(tool, dict(Counter(v[tool] for v in out.values())))
        by_type = {}
        for v in out.values():
            by_type.setdefault(v["store_type"], Counter())[v[tool]] += 1
        print("  by store type:", {k: dict(c) for k, c in by_type.items()})
    print()
    print("| Page | Store | Current | Firecrawl |")
    print("|---|---|---|---|")
    for k, v in out.items():
        if v["current"] != "ok" or v["firecrawl"] != "ok":
            print(f"| {k} | {v['store']} | {v['current']} {v['current_why']} | {v['firecrawl']} {v['firecrawl_why']} |")


if __name__ == "__main__":
    main()
