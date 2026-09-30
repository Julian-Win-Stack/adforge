"""Score the new photo judge (both arms, several keep rules) against the review's truth."""
import json, glob, collections, sys
truth = [r for r in json.load(open("/tmp/st/v2/photo_truth.json")) if r["stage"] in ("kept", "ai")]
outs = {}
for f in glob.glob("/tmp/st/v2/photo-out/*.json"):
    d = json.load(open(f)); outs[d["key"]] = d
rows = []
for r in truth:
    d = outs.get(r["key"])
    if not d: continue
    v = next((x for x in d["verdicts"] if x["n"] == r["n"]), None)
    if not v: continue
    rows.append({**r, "text": v["text"]["relation"], "ref": (v["ref"] or {}).get("relation"), "why": (v["ref"] or v["text"])["why"], "has_ref": d["ref_n"] is not None,
                 "ref_ok": next((t["truth"] for t in truth if t["key"] == r["key"] and t["n"] == d["ref_n"]), None)})
print(len(rows), "images scored on", len(outs), "pages;", sum(r["has_ref"] for r in rows), "images on pages with a reference")
refs = {k: (d["ref_n"], d["reference"]["main_n"], d["reference"]["pick_n"]) for k, d in outs.items()}
ref_truth = collections.Counter(next((r["ref_ok"] for r in rows if r["key"] == k), None) for k in outs)
print("reference photo truth by page:", dict(ref_truth), "| pages: main photo found", sum(1 for v in refs.values() if v[1] is not None), "AI picked", sum(1 for v in refs.values() if v[2] is not None))

def table(name, keep_fn, subset=None):
    c = collections.Counter()
    for r in rows:
        if subset and not subset(r): continue
        c[("keep" if keep_fn(r) else "drop", r["truth"])] += 1
    kp, kn, dp, dn = c[("keep","product")], c[("keep","not")], c[("drop","product")], c[("drop","not")]
    print(f"{name:42s} kept right {kp:4d}  kept WRONG {kn:4d}  dropped WRONG {dp:4d}  dropped right {dn:4d}   precision {kp/max(kp+kn,1):.0%}  recall {kp/max(kp+dp,1):.0%}")

table("old (name only, unsure=keep)", lambda r: r["stage"] == "kept")
table("text+description: same only", lambda r: r["text"] == "same_product")
table("text+description: same+variant", lambda r: r["text"] in ("same_product", "same_product_other_variant"))
table("reference photo: same only", lambda r: (r["ref"] or r["text"]) == "same_product")
table("reference photo: same+variant", lambda r: (r["ref"] or r["text"]) in ("same_product", "same_product_other_variant"))
meta = json.load(open("/tmp/st/v2/meta.json"))
links = {(r["key"], r["n"]): r["link"] for r in json.load(open("/tmp/st/v2/link-rows.json"))}
def variant_ok(r, rel):
    return rel == "same_product" or (rel == "same_product_other_variant" and not meta[r["key"]].get("variant"))
table("ref: same, +variant if no variant given", lambda r: variant_ok(r, r["ref"] or r["text"]))
table("link rule + ref same+variant", lambda r: links.get((r["key"], r["n"])) != "other-page" and (r["ref"] or r["text"]) in ("same_product", "same_product_other_variant"))
table("link rule + ref same/+variant if none", lambda r: links.get((r["key"], r["n"])) != "other-page" and variant_ok(r, r["ref"] or r["text"]))
table("link rule + text same+variant", lambda r: links.get((r["key"], r["n"])) != "other-page" and r["text"] in ("same_product", "same_product_other_variant"))
table("link rule only (on old keeps)", lambda r: r["stage"] == "kept" and links.get((r["key"], r["n"])) != "other-page")
table("both arms agree same (else drop)", lambda r: r["text"] == "same_product" and (r["ref"] or "same_product") == "same_product")
table("either arm says same", lambda r: r["text"] == "same_product" or r["ref"] == "same_product")
print("-- pages with a reference only")
table("  old", lambda r: r["stage"] == "kept", lambda r: r["has_ref"])
table("  text same only", lambda r: r["text"] == "same_product", lambda r: r["has_ref"])
table("  ref same only", lambda r: r["ref"] == "same_product", lambda r: r["has_ref"])
print("-- pages with NO reference")
table("  old", lambda r: r["stage"] == "kept", lambda r: not r["has_ref"])
table("  text same only", lambda r: r["text"] == "same_product", lambda r: not r["has_ref"])
json.dump(rows, open("/tmp/st/v2/photo-scored.json", "w"))
wk = collections.Counter(r["key"] for r in rows if r["truth"] == "not" and (r["ref"] or r["text"]) == "same_product")
wd = collections.Counter(r["key"] for r in rows if r["truth"] == "product" and (r["ref"] or r["text"]) != "same_product")
print("wrong keeps by page (ref arm):", wk.most_common(10)); print("wrong drops by page (ref arm):", wd.most_common(10))
