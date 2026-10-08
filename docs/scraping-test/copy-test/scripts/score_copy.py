"""Score the copy test against the fix test's corrected sentence labels, with one ruler for every
method: a labelled sentence counts as kept if it is found in the method's kept text (exact, or 80% of
its word triples). The section filter (gpt-5-mini, two runs) is re-scored with the same ruler.
Usage: python score_copy.py <model> [<model> ...]   Writes copy-test/outputs/scores.json"""
import collections, json, re, statistics, sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE.parent / "scripts"))
sys.path.insert(0, str(HERE.parent / "text-test" / "scripts"))
from missing import markdown_to_text  # noqa: E402
from textnorm import found, norm  # noqa: E402

FIX = HERE.parent / "fix-test" / "outputs"
truth = json.load(open(FIX / "text_truth.json"))
cls = {c["id"]: c["category"] for c in json.load(open(FIX / "old-wrong-216-classified.json"))}
OTHER = {(o["page"], o["sentence"]) for o in json.load(open(FIX / "old-wrong-216.json")) if cls.get(o["id"]) == "other_product"}
USEFUL = {u["i"]: u["label"] for u in json.load(open(HERE / "outputs" / "useful_437.json"))}
# $ per million tokens (in, out); OpenAI pricing page, 2026-10-02
PRICE = {"gpt-5-mini": (0.25, 2.00), "gpt-5.4-mini": (0.75, 4.50), "gpt-5.6-sol": (4.00, 20.00), "gpt-5.6-terra": (2.00, 12.00),
         "gpt-6-luna": (0.10, 0.50), "gpt-6-sol": (2.00, 10.00)}


def copy_kept(model: str) -> tuple[dict, list]:
    pages = {}
    for f in (HERE / "outputs" / model).glob("*.json"):
        if f.name.endswith(".error.json"): continue
        d = json.load(open(f)); pages[d["key"]] = d
    return {k: " ".join(norm(s) for s in d["kept"]) for k, d in pages.items()}, list(pages.values())


def sections_kept(path: str) -> dict:
    out = json.load(open(path))
    return {k: norm(markdown_to_text("\n\n".join(s["text"] for s in p["sections"] if s.get("label") == "this_product")))
            for k, p in out.items()}


def score(kept: dict, keys: set) -> dict:
    c = collections.Counter(); leaks, misses = [], collections.Counter()
    for i, r in enumerate(truth):
        if r["key"] not in keys: continue
        hit = found(r["text"], kept.get(r["key"], ""))
        if (r["key"], r["text"]) in OTHER:
            c["other"] += 1; c["other_kept"] += hit
            if hit: leaks.append((r["key"], r["text"][:100]))
        elif r["truth"] == "product_info":
            c["real"] += 1; c["real_kept"] += hit
            if USEFUL.get(i) == "useful": c["useful"] += 1; c["useful_kept"] += hit
            if not hit: misses[r["key"]] += 1
        else:
            c["noise"] += 1; c["noise_kept"] += hit
    return {**c, "leaks": leaks, "misses": misses.most_common(10)}


models = sys.argv[1:]
runs = {m: copy_kept(m) for m in models}
keys = {r["key"] for r in truth}
for m, (kept, _) in runs.items():
    keys &= set(kept)
base = {"sections gpt-5-mini run 1": sections_kept(str(HERE / "inputs" / "sections-mini.json")),
        "sections gpt-5-mini run 2": sections_kept(str(HERE / "inputs" / "sections-mini-b.json"))}
for k in base.values():
    keys &= set(k)
print(f"{len(keys)} pages scored (pages every method answered)\n")
print("| Method | Useful product sentences kept | All real product sentences kept | Other-product sentences leaked | Other noise kept |")
print("|---|---|---|---|---|")
results = {}
for name, kept in [*base.items(), *((m, runs[m][0]) for m in models)]:
    s = score(kept, keys); results[name] = s
    print(f"| {name} | {s['useful_kept']} of {s['useful']} ({s['useful_kept'] / s['useful']:.0%}) | {s['real_kept']} of {s['real']} ({s['real_kept'] / s['real']:.0%}) | {s['other_kept']} of {s['other']} | {s['noise_kept']} of {s['noise']} |")
print()
for m in models:
    pages = [p for p in runs[m][1] if p["key"] in keys]
    tin, tout = sum(p["in"] for p in pages), sum(p["out"] for p in pages)
    pr = PRICE.get(re.sub(r"-v\d.*$", "", m.replace("remove-", "")))
    cost = f"${(tin * pr[0] + tout * pr[1]) / 1e6:.2f}" if pr else "price unknown"
    secs = sorted(p["seconds"] for p in pages)
    near = sum(len(p.get("near", [])) for p in pages); dropped = sum(len(p.get("dropped", [])) for p in pages); kept = sum(len(p["kept"]) for p in pages)
    print(f"{m}: {len(pages)} pages, tokens in {tin:,} out {tout:,} ({cost}); seconds median {statistics.median(secs)} max {secs[-1]}; "
          f"copied sentences kept {kept} (of which near-match replaced {near}), dropped {dropped}; "
          f"incomplete {[p['key'] for p in pages if p.get('status') != 'completed']}")
for name, s in results.items():
    print(f"\n{name} leaks:"); [print("  ", l) for l in s["leaks"]]
    print(f"{name} most missed:", s["misses"])
json.dump(results, open(HERE / "outputs" / "scores.json", "w"), indent=1)
