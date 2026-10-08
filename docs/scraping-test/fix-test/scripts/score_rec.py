"""Score step 1 + step 2 (record as reference) against the same truth as last round, on the pages
that have a record. Photos: python score_rec.py photo   Text: python score_rec.py text"""
import collections, glob, json, sys

V2 = "/tmp/st/v2"
GPT5_MINI = (0.25, 2.00)  # $ per million tokens in / out


def cents(tin, tout):
    return (tin * GPT5_MINI[0] + tout * GPT5_MINI[1]) / 1e6 * 100


def photo():
    truth = [r for r in json.load(open(f"{V2}/photo_truth.json")) if r["stage"] in ("kept", "ai")]
    links = {(r["key"], r["n"]): r["link"] for r in json.load(open(f"{V2}/link-rows.json"))}
    new = {d["key"]: d for d in (json.load(open(f)) for f in glob.glob(f"{V2}/photo-rec/*.json"))}
    old = {d["key"]: d for d in (json.load(open(f)) for f in glob.glob(f"{V2}/photo-out/*.json"))}
    rows = []
    for r in truth:
        if r["key"] not in new: continue
        v = next((x for x in new[r["key"]]["verdicts"] if x["n"] == r["n"]), None)
        o = next((x for x in old.get(r["key"], {}).get("verdicts", []) if x["n"] == r["n"]), None)
        if not v: continue
        last = ((o["ref"] or o["text"])["relation"]) if o else None
        rows.append({**r, "rec": v["relation"], "why": v["why"], "last": last,
                     "linked": links.get((r["key"], r["n"])) == "other-page"})
    print(f"{len(rows)} images on {len(new)} pages with a record")

    def table(name, keep):
        c = collections.Counter(("keep" if keep(r) else "drop", r["truth"]) for r in rows)
        kp, kn, dp = c[("keep", "product")], c[("keep", "not")], c[("drop", "product")]
        pages = len({r["key"] for r in rows if keep(r) and r["truth"] == "not"})
        print(f"| {name} | {kp:,} | {kn} | {dp} | {kp / max(kp + kn, 1):.0%} | {kp / max(kp + dp, 1):.0%} | {pages} |")

    both = lambda rel: rel in ("same_product", "same_product_other_variant")
    print("| Rule | Kept right | Kept wrong | Dropped wrong | Precision | Recall | Pages with ≥1 wrong keep |")
    print("|---|---|---|---|---|---|---|")
    table("Old: name only", lambda r: r["stage"] == "kept")
    table("Last round: guessed reference", lambda r: both(r["last"]))
    table("Last round: guessed reference + link rule", lambda r: both(r["last"]) and not r["linked"])
    table("**Record as reference**", lambda r: r["rec"] == "same_product")
    table("**Record as reference + link rule**", lambda r: r["rec"] == "same_product" and not r["linked"])
    print("relations:", dict(collections.Counter(r["rec"] for r in rows)))
    wk = collections.Counter(r["key"] for r in rows if r["rec"] == "same_product" and not r["linked"] and r["truth"] == "not")
    wd = collections.Counter(r["key"] for r in rows if not (r["rec"] == "same_product" and not r["linked"]) and r["truth"] == "product")
    print("wrong keeps by page (record + link rule):", wk.most_common())
    print("wrong drops by page (record + link rule):", wd.most_common(15))
    zero = sorted(k for k in new if not any(r["key"] == k and r["rec"] == "same_product" and not r["linked"] for r in rows))
    print("pages with 0 kept:", zero)
    calls = [v for d in new.values() for v in d["verdicts"]]
    tin, tout = sum(v["in"] for v in calls), sum(v["out"] for v in calls)
    secs = sorted(v["secs"] for v in calls)
    print(f"calls {len(calls)}, tokens in {tin:,} out {tout:,} (per call {tin / len(calls):.0f} / {tout / len(calls):.0f}), "
          f"cost ${cents(tin, tout) / 100:.2f} ({cents(tin, tout) / len(calls):.2f} cents a call)")
    print(f"per call seconds: median {secs[len(secs) // 2]}, p90 {secs[int(len(secs) * .9)]}, max {secs[-1]}; "
          f"per page wall: median {sorted(d['wall'] for d in new.values())[len(new) // 2]}, max {max(d['wall'] for d in new.values())}")
    print("errors:", sum(v["relation"] == "unclear" for v in calls))
    refs = collections.Counter(len(d["refs"]) for d in new.values())
    print("official photos sent per page:", sorted(refs.items()))
    json.dump(rows, open(f"{V2}/photo-rec-scored.json", "w"))


def text():
    """Run after score_text.py on both outputs; compares on the same pages."""
    cls = {c["id"]: c["category"] for c in json.load(open(sys.argv[2] + "/old-wrong-216-classified.json"))}
    other = {(o["page"], o["sentence"]) for o in json.load(open(sys.argv[2] + "/old-wrong-216.json")) if cls.get(o["id"]) == "other_product"}
    new = json.load(open(f"{V2}/sections-rec-scored.json"))
    last = json.load(open(f"{V2}/sections-mini-scored.json"))
    pages = {r["key"] for r in new}
    last = [r for r in last if r["key"] in pages]
    for name, rows in (("Last round: guessed name + description", last), ("Record as context", new)):
        oth = [r for r in rows if (r["key"], r["text"]) in other]
        real = [r for r in rows if r["truth"] == "product_info" and r["ai"] is not None]
        print(f"| {name} | {sum(r['ai'] == 'this_product' for r in oth)} of {len(oth)} | {sum(r['ai'] == 'this_product' for r in real)} of {len(real)} |")
    out = json.load(open(f"{V2}/sections-rec.json"))
    tin, tout = sum(p["in"] for p in out.values()), sum(p["out"] for p in out.values())
    s = sorted(p["secs"] for p in out.values())
    print(f"{len(out)} pages, cost ${cents(tin, tout) / 100:.2f}, per page seconds median {s[len(s) // 2]} max {s[-1]}, "
          f"unanswered {sum(p['unanswered'] for p in out.values())}, errors {[p['errors'] for p in out.values() if p['errors']]}")
    leaks = [(r["key"], r["heading"], r["text"][:80]) for r in new if (r["key"], r["text"]) in other and r["ai"] == "this_product"]
    for l in leaks: print("  leak:", l)


if __name__ == "__main__":
    photo() if sys.argv[1] == "photo" else text()
