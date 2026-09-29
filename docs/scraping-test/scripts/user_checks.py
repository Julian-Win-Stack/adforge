"""Prepare the user's two check files.
Usage: python3 user_checks.py <sheets-dir> <verdicts-dir> <labels.json> <label-checks-dir> <out-dir>
- photo-disagreements.md: ~20 photos where Claude's review disagreed with the filter.
- label-sample.md: 20 random sentence labels.
"""
import json, random, sys
from pathlib import Path
sheets, verdicts, labels_f, lchecks, out = map(Path, sys.argv[1:6])
random.seed(20260928)
rows = []
for vf in sorted(verdicts.glob("*.json")):
    v = json.loads(vf.read_text()); sj = json.loads((sheets / vf.name).read_text())
    items = {it["id"]: it for it in sj["items"]}
    for pid, x in v["verdicts"].items():
        if x["verdict"] != "correct" and pid in items:
            rows.append((sj["key"], sj["product"], pid, x["verdict"], x.get("note", ""), items[pid], sj["sheets"]))
random.shuffle(rows)
pick = rows[:20]
lines = ["# Photo check: 20 disagreements between the filter and Claude's review", "",
         "For each row, open the sheet (or the image URL), find the tile, and mark **Filter** if the filter was right, **Claude** if Claude's review was right, or **?** if unsure.", "",
         f"(There were {len(rows)} disagreements in total; these 20 are a random sample.)", "",
         "| # | Page | Tile | Filter said | Claude said | Claude's note | Sheet | Image | Your mark |", "|---|---|---|---|---|---|---|---|---|"]
for i, (key, product, pid, verdict, note, it, sheet_files) in enumerate(pick, 1):
    filt = "kept" if pid.startswith("K") else f"dropped ({it.get('drop')})"
    lines.append(f"| {i} | {product[:40]} | {pid} | {filt} | {verdict} | {note} | {', '.join(sheet_files)} | {it['url'][:90]} |  |")
(out / "photo-disagreements.md").write_text("\n".join(lines) + "\n")

labels = json.loads(labels_f.read_text())
allrows = []
for key, r in labels.items():
    cf = lchecks / f"{key}.json"
    c = json.loads(cf.read_text())["checks"] if cf.exists() else {}
    for l in r["labels"]:
        allrows.append((key, r["product"], l["n"], l["label"], l["text"], c.get(str(l["n"]), "")))
random.shuffle(allrows)
sample = allrows[:20]
lines = ["# Label check: 20 random sentences", "",
         "Each sentence was on the product page (Firecrawl found it) but the current scraper missed it. gpt-5-mini labelled it. Mark **right** or **wrong** in the last column.", "",
         "- product_info = describes the product (ingredients, sizes, directions, specs, claims, warranty...)", "- noise = everything else (prices, stock lines, buttons, menus, reviews, other products, legal text...)", "",
         f"(Sampled from {len(allrows)} labelled sentences across {len(labels)} pages.)", "",
         "| # | Page | Sentence | Label | Claude's check | Your mark |", "|---|---|---|---|---|---|"]
for i, (key, product, n, label, text, claude) in enumerate(sample, 1):
    t = text.replace("|", "\\|").replace("\n", " ")[:220]
    lines.append(f"| {i} | {product[:35]} | {t} | {label} | {claude} |  |")
(out / "label-sample.md").write_text("\n".join(lines) + "\n")
print(f"wrote {out/'photo-disagreements.md'} ({len(pick)} of {len(rows)}) and {out/'label-sample.md'} (20 of {len(allrows)})")
