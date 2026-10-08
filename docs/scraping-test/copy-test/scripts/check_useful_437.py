"""Check useful_437.json: every product_info index exactly once, nothing else."""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
rows = json.loads((ROOT / "docs/scraping-test/fix-test/outputs/text_truth.json").read_text())
labels = json.loads((ROOT / "docs/scraping-test/copy-test/outputs/useful_437.json").read_text())

want = {i for i, r in enumerate(rows) if r["truth"] == "product_info"}
got = Counter(r["i"] for r in labels)
dupes = [i for i, c in got.items() if c > 1]
missing = sorted(want - set(got))
extra = sorted(set(got) - want)
bad_label = [r for r in labels if r["label"] not in {"useful", "not_useful", "unclear"}]
bad_key = [r for r in labels if r["key"] != rows[r["i"]]["key"]]

print(f"product_info rows: {len(want)}  labelled rows: {len(labels)}")
print(f"duplicates: {dupes}  missing: {missing}  extra: {extra}")
print(f"bad labels: {len(bad_label)}  key mismatches: {len(bad_key)}")
ok = len(want) == 437 and not (dupes or missing or extra or bad_label or bad_key) and len(labels) == 437
print("OK" if ok else "FAIL")
raise SystemExit(0 if ok else 1)
