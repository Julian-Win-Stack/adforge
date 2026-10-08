"""Score the section labeler against the corrected sentence labels."""
import json, re, sys, collections
sys.path.insert(0, "/tmp/st/v2")
out = json.load(open(sys.argv[1]))
truth = json.load(open("/tmp/st/v2/text_truth.json"))
CROSS = re.compile(r"(you may also like|you might also like|shop the look|wear it with|complete the (look|set)|customers also|frequently bought|related products|recommended|recently viewed|pairs well|goes well|similar items|more from|also bought|customer reviews|reviews|ratings|sign in|log in|your account|cart|newsletter|subscribe|footer)", re.I)

def norm(s):
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).casefold().strip()

sys.path.insert(0, "/Users/phyonyanwinn/Project/ProJect/adforge/docs/scraping-test/scripts")
from missing import markdown_to_text, normal
_plain = {}
def find_section(sections, sentence):
    n = normal(sentence)
    for s in sections:
        if id(s) not in _plain: _plain[id(s)] = normal(markdown_to_text(s["text"]))
    hits = [s for s in sections if n in _plain[id(s)]]
    if hits: return hits
    words = re.sub(r"[^\w\s]", "", n).split()
    if len(words) >= 5:
        probe = " ".join(words[:5])
        hits = [s for s in sections if probe in re.sub(r"[^\w\s]", "", _plain[id(s)])]
    return hits

rows = []; unmapped = 0
for r in truth:
    page = out.get(r["key"])
    if not page: continue
    secs = find_section(page["sections"], r["text"])
    if not secs:
        unmapped += 1; rows.append({**r, "ai": None, "heading": None}); continue
    labels = {s["label"] for s in secs}
    ai = "this_product" if "this_product" in labels else ("other_product" if "other_product" in labels else secs[0]["label"])
    heads = [s["heading"] for s in secs]
    rows.append({**r, "ai": ai, "heading": heads[0], "rule": any(CROSS.search(h) for h in heads)})

def table(name, keep_fn):
    c = collections.Counter()
    for r in rows:
        if r["ai"] is None: c[("unmapped", r["truth"])] += 1; continue
        c[("keep" if keep_fn(r) else "drop", r["truth"])] += 1
    kp, kn, dp, dn = c[("keep","product_info")], c[("keep","noise")], c[("drop","product_info")], c[("drop","noise")]
    print(f"{name:28s} kept product_info {kp:4d}  kept noise {kn:4d}  dropped product_info {dp:4d}  dropped noise {dn:4d}  unmapped {c[('unmapped','product_info')]+c[('unmapped','noise')]}  precision {kp/max(kp+kn,1):.0%} recall {kp/max(kp+dp,1):.0%}")

print(f"{len(rows)} sentences, {sum(r['truth']=='product_info' for r in rows)} product_info in truth")
table("old labeler (per sentence)", lambda r: r["old"] == "product_info")
table("sections: keep this_product", lambda r: r["ai"] == "this_product")
table("sections + heading rule", lambda r: r["ai"] == "this_product" and not r["rule"])
# what went wrong for the 216 other-product sentences?
c = collections.Counter(r["ai"] for r in rows if r["old"] == "product_info" and r["truth"] == "noise")
print("old wrong product_info (216) now labelled:", dict(c))
c = collections.Counter(r["ai"] for r in rows if r["truth"] == "product_info")
print("true product_info (437) now labelled:", dict(c))
json.dump(rows, open(sys.argv[1].replace(".json", "-scored.json"), "w"))
# per page misses
miss = collections.Counter(); leak = collections.Counter()
for r in rows:
    if r["truth"] == "product_info" and r["ai"] != "this_product": miss[r["key"]] += 1
    if r["truth"] == "noise" and r["ai"] == "this_product": leak[r["key"]] += 1
print("dropped real product info by page:", miss.most_common(12))
print("kept noise by page:", leak.most_common(12))
