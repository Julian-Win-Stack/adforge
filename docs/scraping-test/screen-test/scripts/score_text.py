"""Score the text each method keeps against the corrected sentence labels (fix test), on the pages
of this test. A sentence counts only if it is still on today's page (the pages were re-read) and in
the fix test's section output, so both methods are scored on the same sentences.
Categories: product = true product info; other = another product's text (the 102 subagent-classified
wrong keeps of the first labeler); noise = everything else.
Usage (host): python3 score_text.py [point | point-v2]"""
import json, re, sys, collections
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
FIX = HERE.parent / "fix-test" / "outputs"
SP = HERE / "outputs"
truth = json.load(open(FIX / "text_truth.json"))
wrong = {r["id"]: r for r in json.load(open(FIX / "old-wrong-216.json"))}
cls = {r["id"]: r["category"] for r in json.load(open(FIX / "old-wrong-216-classified.json"))}
mini = json.load(open(FIX / "sections-mini.json"))


def norm(s: str) -> str:
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.casefold())).strip()


def found(sentence: str, text: str) -> bool:
    n = norm(sentence)
    if len(n) < 12: return False
    if n in text: return True
    w = n.split()
    grams = [" ".join(w[i:i + 3]) for i in range(len(w) - 2)]
    return bool(grams) and sum(g in text for g in grams) / len(grams) >= 0.8


other_text = collections.defaultdict(set)
for i, c in cls.items():
    if c == "other_product":
        other_text[wrong[i]["page"]].add(norm(wrong[i]["sentence"]))

POINT = sys.argv[1] if len(sys.argv) > 1 else "point"  # which run of the screenshot method
pages = json.load(open(HERE / "pages.json"))
tot = collections.Counter(); per_page = {}
for key in pages:
    marks_path, point_path = SP / "pages" / f"{key}.json", SP / POINT / f"{key}.json"
    if key not in mini or not point_path.exists(): continue
    secs = json.load(open(marks_path))["marks"]["secs"]
    keep = set(json.load(open(point_path))["answer"]["product_sections"])
    screen_all = norm(" ".join(s["heading"] + " " + s["text"] for s in secs))
    screen_keep = norm(" ".join(s["heading"] + " " + s["text"] for s in secs if s["i"] in keep))
    mini_all = norm(" ".join(s["heading"] + " " + s["text"] for s in mini[key]["sections"]))
    mini_keep = norm(" ".join(s["heading"] + " " + s["text"] for s in mini[key]["sections"] if s["label"] == "this_product"))
    c = collections.Counter()
    for r in truth:
        if r["key"] != key: continue
        cat = "other" if norm(r["text"]) in other_text[key] else ("product" if r["truth"] == "product_info" else "noise")
        if not (found(r["text"], screen_all) and found(r["text"], mini_all)):
            c[(cat, "gone")] += 1; continue
        c[(cat, "n")] += 1
        c[(cat, "screen")] += found(r["text"], screen_keep)
        c[(cat, "tested")] += found(r["text"], mini_keep)
    per_page[key] = c; tot.update(c)

print(f"{'page':40s} {'product kept (screen/tested of n)':>34s} {'other kept (screen/tested of n)':>32s}")
for key, c in per_page.items():
    print(f"{key:40s} {c[('product','screen')]:>14d}/{c[('product','tested')]:<3d} of {c[('product','n')]:<4d}"
          f"      {c[('other','screen')]:>10d}/{c[('other','tested')]:<3d} of {c[('other','n')]:<4d}")
for cat in ("product", "other", "noise"):
    print(f"{cat:8s}: scored {tot[(cat,'n')]:4d} (not on today's page: {tot[(cat,'gone')]:3d})  "
          f"kept by screen {tot[(cat,'screen')]:4d}   kept by tested sections {tot[(cat,'tested')]:4d}")
