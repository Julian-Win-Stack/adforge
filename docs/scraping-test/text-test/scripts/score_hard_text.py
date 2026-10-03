"""Score the text each version keeps on the hard pages against the fix test's corrected sentence
labels (as screen-test/scripts/score_text.py). A sentence counts only if it is on today's page and
in the fix test's section output. Usage (host): python3 score_hard_text.py"""
import collections, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from textnorm import norm, found

HERE = Path(__file__).resolve().parent.parent
FIX = HERE.parent / "fix-test" / "outputs"
OUT = HERE / "outputs"
truth = json.load(open(FIX / "text_truth.json"))
wrong = {r["id"]: r for r in json.load(open(FIX / "old-wrong-216.json"))}
cls = {r["id"]: r["category"] for r in json.load(open(FIX / "old-wrong-216-classified.json"))}
mini = json.load(open(FIX / "sections-mini.json"))
kept = json.load(open(OUT / "kept.json"))
pages = json.load(open(HERE / "pages.json"))

other_text = collections.defaultdict(set)
for i, c in cls.items():
    if c == "other_product": other_text[wrong[i]["page"]].add(norm(wrong[i]["sentence"]))

VERSIONS = ("pieces-1", "copy-1")
tot = collections.Counter()
print(f"{'page':40s} " + "  ".join(f"{v + ' prod/other':>20s}" for v in VERSIONS) + "   (of n prod/other)")
for key in sorted(pages):
    if pages[key]["set"] != "hard" or key not in mini or key not in kept: continue
    pg = json.load(open(OUT / "pages" / f"{key}.json"))
    today = norm(" ".join(s["heading"] + " " + s["text"] for s in pg["marks"]["secs"]))
    mini_all = norm(" ".join(s["heading"] + " " + s["text"] for s in mini[key]["sections"]))
    keep = {v: norm(" ".join(kept[key].get(v, []))) for v in VERSIONS}
    c = collections.Counter()
    for r in truth:
        if r["key"] != key: continue
        cat = "other" if norm(r["text"]) in other_text[key] else ("product" if r["truth"] == "product_info" else "noise")
        if not (found(r["text"], today) and found(r["text"], mini_all)): continue
        c[(cat, "n")] += 1
        for v in VERSIONS: c[(cat, v)] += found(r["text"], keep[v])
    tot.update(c)
    print(f"{key:40s} " + "  ".join(f"{c[('product', v)]:>14d}/{c[('other', v)]:<5d}" for v in VERSIONS)
          + f"   ({c[('product', 'n')]}/{c[('other', 'n')]})")
for cat in ("product", "other", "noise"):
    print(f"{cat:8s}: scored {tot[(cat, 'n')]:4d}   " + "   ".join(f"kept by {v} {tot[(cat, v)]:4d}" for v in VERSIONS))
