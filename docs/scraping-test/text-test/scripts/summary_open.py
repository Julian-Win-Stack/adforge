"""Per-run verdict counts for the copy version: old pages (outputs/kept.json) against a new results
folder, using every judging round's verdicts. Usage (host): python3 summary_open.py [folder]"""
import collections, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from textnorm import norm, found, sentences

HERE = Path(__file__).resolve().parent.parent
NEW = HERE / "outputs" / (sys.argv[1] if len(sys.argv) > 1 else "final")
pages = json.load(open(HERE / "pages.json"))
kept_old = json.load(open(HERE / "outputs" / "kept.json"))
pairs = [(HERE / "text", HERE / "judged" / "text")] + [(d, HERE / f"judged-{d.name[5:]}") for d in sorted(HERE.glob("text-*"))]


def verdicts(key):
    out = {}
    for t, j in pairs:
        if (t / f"{key}.json").exists() and (j / f"{key}.json").exists():
            v = {x["id"]: x["verdict"] for x in json.load(open(j / f"{key}.json"))}
            out.update({norm(s["text"]): v[s["id"]] for s in json.load(open(t / f"{key}.json")) if s["id"] in v})
    return out


def new_kept(key, run):
    p, pg = NEW / f"copy-{run}" / f"{key}.json", NEW / "pages" / f"{key}.json"
    if not p.exists() or not pg.exists(): return []
    pg = json.load(open(pg))
    text = norm(" ".join(s["heading"] + " " + s["text"] for s in pg["marks"]["secs"]) + " " + (pg.get("markdown") or ""))
    return [x for ps in json.load(open(p))["answer"]["passages"] for x in sentences(ps) if found(x, text)]


rows = {}
for name, get in [("old copy-1", lambda k: kept_old.get(k, {}).get("copy-1", [])),
                  ("old copy-2", lambda k: kept_old.get(k, {}).get("copy-2", [])),
                  ("new run 1", lambda k: new_kept(k, 1)), ("new run 2", lambda k: new_kept(k, 2))]:
    c, bad_pages, others = collections.Counter(), set(), []
    for key in pages:
        v = verdicts(key)
        for s in get(key):
            x = v.get(norm(s), "unjudged"); c[x] += 1
            if x == "other_product": bad_pages.add(key); others.append((key, s))
    rows[name] = (c, bad_pages, others)
print(f"{'':12s} {'product':>8s} {'other':>6s} {'noise':>6s} {'unclear':>8s} {'unjudged':>9s} {'pages w/ other':>15s}")
for name, (c, b, _) in rows.items():
    print(f"{name:12s} {c['this_product']:8d} {c['other_product']:6d} {c['not_product_info']:6d} {c['unclear']:8d} {c['unjudged']:9d} {len(b):15d}")
for name in ("new run 1", "new run 2"):
    print(f"\n{name}: other-product sentences")
    for key, s in rows[name][2]: print(f"  {key[:30]:30s} {s[:150]}")
