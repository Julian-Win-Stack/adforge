"""Confirm every photo number on every contact sheet got a verdict.
Usage: python check_verdicts.py <sheets-dir> <verdicts-dir>
verdicts/<key>.json: {"key":..., "verdicts": {"K1": {"verdict": "correct|wrongly_kept|wrongly_dropped", "note": "..."}, ...}}"""
import json, sys
from collections import Counter
from pathlib import Path
sheets, verdicts = Path(sys.argv[1]), Path(sys.argv[2])
ok = True
totals = Counter()
per_page = {}
for sj in sorted(sheets.glob("*.json")):
    items = {it["id"] for it in json.loads(sj.read_text())["items"]}
    vf = verdicts / sj.name
    if not vf.exists():
        print(f"MISSING verdict file: {sj.name}"); ok = False; continue
    v = json.loads(vf.read_text())["verdicts"]
    missing = items - set(v)
    extra = set(v) - items
    bad = {k: x for k, x in v.items() if x.get("verdict") not in ("correct", "wrongly_kept", "wrongly_dropped")}
    if missing or extra or bad:
        ok = False
        print(f"{sj.stem}: missing {sorted(missing)[:10]}{'...' if len(missing)>10 else ''} extra {sorted(extra)} bad {list(bad)[:5]}")
    c = Counter(x["verdict"] for x in v.values())
    per_page[sj.stem] = c
    totals.update(c)
print("ALL COVERED" if ok else "GAPS FOUND")
print("totals:", dict(totals))
