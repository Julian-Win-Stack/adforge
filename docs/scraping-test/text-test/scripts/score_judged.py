"""Score each version with the blind judges' verdicts (judged/text, judged/photos).
Text: for each version (pieces-1/2, copy-1/2), how many kept sentences are this_product /
other_product / not_product_info / unclear. Photos: the pieces version's picks per run.
Stability: how much run 1 and run 2 agree. Usage (host): python3 score_judged.py"""
import collections, json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "outputs"
pages = json.load(open(HERE / "pages.json"))
text_key = json.load(open(OUT / "text-key.json"))
photo_key = json.load(open(OUT / "photo-key.json"))


def verdicts(kind: str, field: str) -> dict:
    v = {}
    for f in (HERE / "judged" / kind).glob("*.json"):
        for r in json.load(open(f)): v[(f.stem, r[field])] = r["verdict"]
    return v


tv, pv = verdicts("text", "id"), verdicts("photos", "label")
VERS = ("pieces-1", "pieces-2", "copy-1", "copy-2")
for group in ("hard", "normal"):
    tot = {v: collections.Counter() for v in VERS}
    per = collections.defaultdict(dict)
    for r in text_key:
        if pages[r["key"]]["set"] != group or (r["key"], r["id"]) not in tv: continue
        for v in r["by"]:
            tot[v][tv[(r["key"], r["id"])]] += 1
            per[r["key"]].setdefault(v, collections.Counter())[tv[(r["key"], r["id"])]] += 1
    judged = sorted({k for k, _ in tv if pages[k]["set"] == group})
    print(f"\n== TEXT, {group} pages judged: {len(judged)}")
    for v in VERS:
        c = tot[v]; n = sum(c.values())
        print(f"  {v:9s} kept {n:4d}: this {c['this_product']:4d}  OTHER {c['other_product']:3d}  noise {c['not_product_info']:4d}  unclear {c['unclear']:3d}")
    print("  pages with any other-product sentence:")
    for v in VERS:
        bad = [k for k in judged if per[k].get(v, {}).get("other_product")]
        print(f"    {v:9s} {len(bad)}: " + ", ".join(f"{k.split('-', 2)[-1]}({per[k][v]['other_product']})" for k in bad))
    # this_product sentences found by one version only (what each misses)
    miss = collections.Counter()
    for r in text_key:
        if pages[r["key"]]["set"] != group or tv.get((r["key"], r["id"])) != "this_product": continue
        fam = {v.split("-")[0] for v in r["by"]}
        miss["only pieces" if fam == {"pieces"} else "only copy" if fam == {"copy"} else "both"] += 1
    print(f"  this_product sentences kept by: {dict(miss)}")

print("\n== PHOTOS (pieces version, normal pages)")
tot = {r: collections.Counter() for r in ("pieces-1", "pieces-2")}
bad = collections.defaultdict(list)
for r in photo_key:
    v = pv.get((r["key"], r["label"]))
    if v is None: continue
    for run, _ in r["by"]:
        tot[run][v] += 1
        if v == "other_product": bad[run].append(r["key"].split("-", 2)[-1])
for run, c in tot.items():
    n = sum(c.values()); ok = c["product"] + c["product_other_variant"]
    print(f"  {run}: photos {n}  right {ok}  OTHER {c['other_product']}  no product {c['no_product']}  unclear {c['unclear']}   other on: {collections.Counter(bad[run])}")

print("\n== STABILITY (run 1 vs run 2)")
for fam in ("pieces", "copy"):
    same = diff = 0
    for r in text_key:
        s = {f"{fam}-1", f"{fam}-2"} & set(r["by"])
        if s: same += len(s) == 2; diff += len(s) == 1
    print(f"  text {fam}: sentences kept by both runs {same}, by one run only {diff}")
same = diff = 0
for r in photo_key:
    runs = {x for x, _ in r["by"]}
    same += len(runs) == 2; diff += len(runs) == 1
print(f"  photos: picked by both runs {same}, by one run only {diff}")
