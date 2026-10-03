"""Compare the copy version before and after the new open.js (outputs/ against outputs/open/).
1. Hard pages: product sentences from the fix test's labels that are visible after opening, and
   that each copy run kept (as score_hard_text.py).
2. All pages: the new run's kept sentences with the old blind verdicts where the same sentence was
   judged before; writes text-<folder>/<key>.json with the sentences nobody judged yet.
Usage (host): python3 score_open.py [folder under outputs/, default open]"""
import collections, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from textnorm import norm, found, sentences

HERE = Path(__file__).resolve().parent.parent
FIX = HERE.parent / "fix-test" / "outputs"
OUT = HERE / "outputs"
NEW = OUT / (sys.argv[1] if len(sys.argv) > 1 else "open")
pages = json.load(open(HERE / "pages.json"))
kept_old = json.load(open(OUT / "kept.json"))
truth = json.load(open(FIX / "text_truth.json"))
wrong = {r["id"]: r for r in json.load(open(FIX / "old-wrong-216.json"))}
cls = {r["id"]: r["category"] for r in json.load(open(FIX / "old-wrong-216-classified.json"))}
mini = json.load(open(FIX / "sections-mini.json"))
other_text = collections.defaultdict(set)
for i, c in cls.items():
    if c == "other_product": other_text[wrong[i]["page"]].add(norm(wrong[i]["sentence"]))


def page_text(pg: dict) -> str:
    return norm(" ".join(s["heading"] + " " + s["text"] for s in pg["marks"]["secs"]) + " " + (pg.get("markdown") or ""))


def new_kept(key: str, run: str) -> list[str] | None:
    p = NEW / f"copy-{run}" / f"{key}.json"
    pg_path = NEW / "pages" / f"{key}.json"
    if not p.exists() or not pg_path.exists(): return None
    pg = json.load(open(pg_path))
    all_text = page_text(pg)
    return [x for ps in json.load(open(p))["answer"]["passages"] for x in sentences(ps) if found(x, all_text)]


RUNS = sorted(p.name.split("-", 1)[1] for p in NEW.glob("copy-*") if p.is_dir())
print("1. Hard pages: product sentences (fix-test labels)\n")
print(f"{'page':40s} {'n':>4s} {'visible':>8s} {'old copy-1':>11s} " + " ".join(f"{'new ' + r:>9s}" for r in RUNS) + "  other kept (new)")
tot = collections.Counter()
for key in sorted(pages):
    if pages[key]["set"] != "hard" or key not in mini or key not in kept_old: continue
    pg_path = NEW / "pages" / f"{key}.json"
    if not pg_path.exists(): continue
    pg = json.load(open(pg_path))
    old_pg = json.load(open(OUT / "pages" / f"{key}.json"))
    old_today = norm(" ".join(s["heading"] + " " + s["text"] for s in old_pg["marks"]["secs"]))
    mini_all = norm(" ".join(s["heading"] + " " + s["text"] for s in mini[key]["sections"]))
    visible = norm(pg.get("visible") or "")
    old = norm(" ".join(kept_old[key].get("copy-1", [])))
    new = {r: norm(" ".join(new_kept(key, r) or [])) for r in RUNS}
    c = collections.Counter()
    for r in truth:
        if r["key"] != key: continue
        if not (found(r["text"], old_today) and found(r["text"], mini_all)): continue
        if norm(r["text"]) in other_text[key]:
            c["other n"] += 1
            for run in RUNS: c[f"other {run}"] += found(r["text"], new[run])
            continue
        if r["truth"] != "product_info": continue
        c["n"] += 1; c["visible"] += found(r["text"], visible); c["old"] += found(r["text"], old)
        for run in RUNS: c[run] += found(r["text"], new[run])
    tot.update(c)
    print(f"{key:40s} {c['n']:4d} {c['visible']:8d} {c['old']:11d} " + " ".join(f"{c[r]:9d}" for r in RUNS)
          + "  " + " ".join(f"{c['other ' + r]}/{c['other n']}" for r in RUNS))
print(f"{'TOTAL':40s} {tot['n']:4d} {tot['visible']:8d} {tot['old']:11d} " + " ".join(f"{tot[r]:9d}" for r in RUNS)
      + "  " + " ".join(f"{tot['other ' + r]}/{tot['other n']}" for r in RUNS))

print("\n2. All pages: new kept sentences, with old blind verdicts where judged before\n")
TODO = HERE / f"text-{NEW.name}"; TODO.mkdir(exist_ok=True)
# Verdicts from every earlier judging round: text/ + judged/, text-<x>/ + judged-<x>/.
SOURCES = [(HERE / "text", HERE / "judged" / "text")] + [
    (d, HERE / f"judged-{d.name[5:]}") for d in sorted(HERE.glob("text-*")) if d != TODO]
grand = collections.Counter()
print(f"{'page':40s} {'kept':>5s} {'this':>5s} {'other':>5s} {'noise':>5s} {'uncl':>5s} {'new':>5s}   old copy-1 kept")
for key in sorted(pages):
    verdict = {}
    for tdir, jdir in SOURCES:
        tp, jp = tdir / f"{key}.json", jdir / f"{key}.json"
        if tp.exists() and jp.exists():
            v = {j["id"]: j["verdict"] for j in json.load(open(jp))}
            verdict.update({norm(s["text"]): v.get(s["id"]) for s in json.load(open(tp)) if v.get(s["id"])})
    c, todo = collections.Counter(), {}
    for run in RUNS:
        for s in new_kept(key, run) or []:
            n = norm(s)
            c["kept"] += 1
            if n in verdict and verdict[n]: c[verdict[n]] += 1
            else: c["new"] += 1; todo.setdefault(n, s)
    if not c: continue
    grand.update(c)
    json.dump([{"id": f"N{i}", "text": s} for i, s in enumerate(todo.values(), 1)],
              open(TODO / f"{key}.json", "w"), indent=1, ensure_ascii=False)
    print(f"{key:40s} {c['kept']:5d} {c['this_product']:5d} {c['other_product']:5d} {c['not_product_info']:5d} "
          f"{c['unclear']:5d} {c['new']:5d}   {len(kept_old.get(key, {}).get('copy-1', []))}")
print(f"{'TOTAL':40s} {grand['kept']:5d} {grand['this_product']:5d} {grand['other_product']:5d} "
      f"{grand['not_product_info']:5d} {grand['unclear']:5d} {grand['new']:5d}")
