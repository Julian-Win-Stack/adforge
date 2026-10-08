"""Score the reference test: join the blind verdicts (judged/<key>.json) to which run picked each
photo (outputs/photo-key.json), checking every photo has exactly one verdict.
Prints per-run totals and the photos only one run picked. Usage: python score.py"""
import json, collections
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
key = json.load(open(HERE / "outputs" / "photo-key.json"))
verdict, problems = {}, []
for k in sorted({r["key"] for r in key}):
    p = HERE / "judged" / f"{k}.json"
    if not p.exists(): problems.append(f"{k}: not judged"); continue
    rows = json.load(open(p))
    labels = [r["label"] for r in rows]
    want = [r["label"] for r in key if r["key"] == k]
    if sorted(labels) != sorted(want) or len(set(labels)) != len(labels):
        problems.append(f"{k}: labels {len(labels)} vs {len(want)} expected")
    for r in rows: verdict[(k, r["label"])] = r
print("PROBLEMS:", problems or "none")

RIGHT = ("product", "product_other_variant")
tot = {m: collections.Counter() for m in ("ref", "noref")}
bad_pages = {m: set() for m in tot}
only = []
for r in key:
    v = verdict.get((r["key"], r["label"]))
    if not v: continue
    runs = {m for m, _ in r["by"]}
    for m in runs:
        tot[m][v["verdict"]] += 1
        if v["verdict"] == "other_product": bad_pages[m].add(r["key"])
    if len(runs) == 1:
        only.append((r["key"], next(iter(runs)), v["verdict"], v["why"]))
for m, c in tot.items():
    n = sum(c.values()); right = sum(c[x] for x in RIGHT)
    print(f"{m}: photos {n}, right {right} ({right / n:.0%}), other_product {c['other_product']}, "
          f"no_product {c['no_product']}, unclear {c['unclear']}, pages with other_product {len(bad_pages[m])}")
    print("   pages:", sorted(bad_pages[m]))
print("\nPicked by one run only:", collections.Counter((m, v) for _, m, v, _ in only))
for k, m, v, why in sorted(only):
    print(f"  {m:5} {v:22} {k}: {why}")
json.dump({"totals": {m: dict(c) for m, c in tot.items()}, "bad_pages": {m: sorted(s) for m, s in bad_pages.items()},
           "only": only}, open(HERE / "outputs" / "scores.json", "w"), indent=1)
