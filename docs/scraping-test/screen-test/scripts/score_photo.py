"""Score each method's top-10 photos per page with the blind judges' verdicts.
Methods: tested (fix test: reference photo + link rule), shop (the shop's own list), screen
(screenshot method), shop_else_screen (shop if it has 3+ photos, else screen).
Usage (host): python3 score_photo.py"""
import collections, json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
key_rows = json.load(open(HERE / "outputs" / "sheet-key.json"))
compare = {r["key"]: r for r in json.load(open(HERE / "outputs" / "compare.json"))}
verdicts = {}
for f in (HERE / "judged").glob("*.json"):
    for v in json.load(open(f)):
        verdicts[(f.stem, v["label"])] = v["verdict"]

missing = [(r["key"], r["label"]) for r in key_rows if (r["key"], r["label"]) not in verdicts]
judged_pages = {k for k, _ in verdicts}
picks = collections.defaultdict(lambda: collections.defaultdict(list))  # page -> method -> verdicts
for r in key_rows:
    v = verdicts.get((r["key"], r["label"]))
    if v is None: continue
    for method, rank in r["by"]:
        picks[r["key"]][method].append(v)
for key in picks:
    src = compare[key]["fallback"]
    picks[key]["shop_else_screen"] = picks[key][src]

METHODS = ("tested", "shop", "screen", "shop_else_screen")
V = ("product", "product_other_variant", "other_product", "no_product", "unclear")
print(f"judged pages: {len(judged_pages)}; labels without a verdict: {len(missing)}\n")
print(f"{'page':38s} " + " ".join(f"{m:>18s}" for m in METHODS))
print(f"{'':38s} " + " ".join(f"{'ok/wrong/none':>18s}" for m in METHODS))
tot = {m: collections.Counter() for m in METHODS}
for key in sorted(picks):
    cells = []
    for m in METHODS:
        c = collections.Counter(picks[key][m])
        ok = c["product"] + c["product_other_variant"]
        cells.append(f"{ok:>8d}/{c['other_product']:d}/{c['no_product'] + c['unclear']:d}")
        tot[m].update(c)
        tot[m]["pages"] += 1
        tot[m]["pages_with_wrong"] += c["other_product"] > 0
        tot[m]["pages_under_3_ok"] += ok < 3
    print(f"{key:38s} " + " ".join(f"{x:>18s}" for x in cells))
print()
for m in METHODS:
    c = tot[m]; n = sum(c[v] for v in V)
    ok = c["product"] + c["product_other_variant"]
    print(f"{m:17s} photos {n:3d}  right {ok:3d} ({ok / max(n, 1):.0%}; of which other colour/size {c['product_other_variant']})  "
          f"other product {c['other_product']:3d}  no product {c['no_product']:3d}  unclear {c['unclear']:2d}  "
          f"pages with a wrong product {c['pages_with_wrong']}/{c['pages']}  pages under 3 right photos {c['pages_under_3_ok']}")
