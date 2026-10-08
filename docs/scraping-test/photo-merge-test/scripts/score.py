"""Score: screenshot picks alone, + added (a: all product photos), + added (b: at most 10 product
photos a page). Screenshot verdicts come from the earlier blind judges (screen-test/judged,
text-test/judged/photos); added photos' verdicts from judged/<key>.json (this test).
First checks that every added photo has exactly one verdict and that the screenshot numbers
reproduce the published ones. Usage: python3 scripts/score.py"""
import collections, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "outputs"
OK = ("product", "product_other_variant")
VERDICTS = ("product", "product_other_variant", "other_product", "no_product", "unclear")

key_rows = json.load(open(OUT / "added-key.json"))
added_v = {}
problems = []
for f in (HERE / "judged").glob("*.json"):
    for r in json.load(open(f)):
        if r["verdict"] not in VERDICTS: problems.append(f"bad verdict {f.stem} {r}")
        if (f.stem, r["label"]) in added_v: problems.append(f"two verdicts {f.stem} {r['label']}")
        added_v[(f.stem, r["label"])] = r["verdict"]
wanted = {(r["key"], r["label"]) for r in key_rows}
missing = sorted(wanted - set(added_v))
extra = sorted(set(added_v) - wanted)
print(f"added photos: {len(wanted)}; with a verdict: {len(wanted & set(added_v))}; missing: {len(missing)}; "
      f"verdicts for unknown labels: {len(extra)}")
for p in problems + [f"missing {m}" for m in missing] + [f"extra {e}" for e in extra]: print("  ", p)
if missing or extra or problems: sys.exit("every added photo needs exactly one verdict")

near = {}  # added photos found (after judging) to be the same shot as a screenshot pick
nc = OUT / "near-copies.json"
if nc.exists():
    for r in json.load(open(nc)):
        if r["same_shot"]: near[(r["key"], r["label"])] = r["of"]

pages = {}
for f in sorted((OUT / "pages").glob("*.json")):
    pages[f.stem] = json.load(open(f))
added_by_page = collections.defaultdict(list)
for r in key_rows:
    added_by_page[r["key"]].append({**r, "verdict": added_v[(r["key"], r["label"])], "near": (r["key"], r["label"]) in near})
# other cap order, for comparison: drop copies of screenshot picks first, then keep the first 10 left
for k, lst in added_by_page.items():
    order = [ph["url"] for ph in pages[k]["product_photos"] if ph["drop"] is None and ph["copy_of_screen"] is None]
    first10 = set(order[:10])
    for x in lst: x["in_capped_after"] = x["url"] in first10

# published numbers
pub = {"hard": (110, 92, 1, 16), "normal": (206, 180, 12, 12)}
for group, (n, ok, other, none) in pub.items():
    c = collections.Counter(p["verdict"] for k, pg in pages.items() if pg["set"] == group for p in pg["screen"])
    got = (sum(c.values()), c["product"] + c["product_other_variant"], c["other_product"], c["no_product"])
    print(f"screenshot {group:6s}: photos {got[0]} right {got[1]} other {got[2]} no product {got[3]} unclear {c['unclear']}"
          f"  -> published {n}/{ok}/{other}/{none}: {'REPRODUCED' if got == (n, ok, other, none) else 'DIFFERENT'}")

SETS = {"screenshot alone": lambda a: [],
        "+ added (a) all": lambda a: a,
        "+ added (b) capped 10": lambda a: [x for x in a if x["in_capped"]],
        "+ added (b2) 10 after removing copies": lambda a: [x for x in a if x["in_capped_after"]],
        "+ added (a), near-copies removed": lambda a: [x for x in a if not x["near"]],
        "+ added (b), near-copies removed": lambda a: [x for x in a if x["in_capped"] and not x["near"]]}


def row(keys, pick):
    c = collections.Counter(); wrong_pages = under3 = 0; n_added = 0
    for k in keys:
        vs = [p["verdict"] for p in pages[k]["screen"]] + [x["verdict"] for x in pick(added_by_page[k])]
        n_added += len(pick(added_by_page[k]))
        pc = collections.Counter(vs); c.update(pc)
        wrong_pages += pc["other_product"] > 0
        under3 += pc["product"] + pc["product_other_variant"] < 3
    n = sum(c.values()); ok = c["product"] + c["product_other_variant"]
    return {"photos": n, "added": n_added, "right": ok, "right_pct": round(100 * ok / max(n, 1)), "other_colour": c["product_other_variant"],
            "other": c["other_product"], "none": c["no_product"], "unclear": c["unclear"],
            "pages_wrong": wrong_pages, "pages_under3": under3, "pages": len(keys)}


groups = {"hard (15)": [k for k in pages if pages[k]["set"] == "hard"],
          "ordinary (19)": [k for k in pages if pages[k]["set"] == "normal"],
          "all (34)": list(pages)}
table = {}
for g, keys in groups.items():
    print(f"\n== {g}")
    print(f"{'':36s} {'photos':>6s} {'added':>5s} {'right':>11s} {'(colour)':>8s} {'other':>5s} {'none':>5s} {'uncl':>4s} {'pg wrong':>8s} {'pg <3 right':>11s}")
    for name, pick in SETS.items():
        r = row(keys, pick); table[(g, name)] = r
        print(f"{name:36s} {r['photos']:6d} {r['added']:5d} {r['right']:5d} ({r['right_pct']:2d}%) {r['other_colour']:8d} {r['other']:5d} "
              f"{r['none']:5d} {r['unclear']:4d} {r['pages_wrong']:5d}/{r['pages']:<2d} {r['pages_under3']:8d}")

print("\n== per page: screenshot right/other/none | added (a) right/other/none (near-copies) | added (b) right/other/none")
for k in sorted(pages):
    s = collections.Counter(p["verdict"] for p in pages[k]["screen"])
    a = collections.Counter(x["verdict"] for x in added_by_page[k])
    b = collections.Counter(x["verdict"] for x in added_by_page[k] if x["in_capped"])
    nn = sum(x["near"] for x in added_by_page[k])
    f = lambda c: f"{c['product'] + c['product_other_variant']:3d}/{c['other_product']}/{c['no_product'] + c['unclear']:<2d}"
    print(f"{k:42s} {f(s)} | {f(a)} ({nn:2d}) | {f(b)}  {pages[k]['record_problem'] or ''}")

print("\n== other-product photos the product call added")
for k in sorted(added_by_page):
    for x in sorted(added_by_page[k], key=lambda x: int(x["label"][1:])):
        if x["verdict"] == "other_product":
            why = next(r["why"] for r in json.load(open(HERE / "judged" / f"{k}.json")) if r["label"] == x["label"])
            print(f"  {k} {x['label']} {'(b) ' if x['in_capped'] else '    '}{why}")
json.dump({f"{g} | {n}": r for (g, n), r in table.items()}, open(OUT / "score.json", "w"), indent=1)
