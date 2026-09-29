"""Confirm every labelled sentence was checked, and summarise. Usage: python3 check_labels.py <labels.json> <checks-dir>"""
import json, sys
from collections import Counter
from pathlib import Path
labels = json.loads(Path(sys.argv[1]).read_text()); checks = Path(sys.argv[2])
ok, tot = True, Counter()
for key, r in labels.items():
    f = checks / f"{key}.json"
    if not r["labels"]:
        continue
    if not f.exists():
        print("MISSING", key); ok = False; continue
    c = json.loads(f.read_text())["checks"]
    ns = {str(l["n"]) for l in r["labels"]}
    gap = ns - set(c)
    if gap:
        print(f"{key}: {len(gap)} unchecked, e.g. {sorted(gap, key=int)[:8]}"); ok = False
    for l in r["labels"]:
        v = c.get(str(l["n"]))
        if v in ("right", "wrong"):
            tot[(l["label"], v)] += 1
print("ALL CHECKED" if ok else "GAPS FOUND")
for (lab, v), n in sorted(tot.items()):
    print(f"  {lab:13} {v:5} {n}")
