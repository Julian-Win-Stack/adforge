"""Build review/photos.html: for each page, the wrong photos each run picked (other product, no
product, unclear) and the right photos one run picked but the other missed. Images copied into
review/img/. Usage: python review_page.py"""
import hashlib, html, json, shutil
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
key = json.load(open(HERE / "outputs" / "photo-key.json"))
pages = json.load(open(HERE / "pages.json"))
RIGHT = ("product", "product_other_variant")
NAMES = {"product": "right", "product_other_variant": "right (other colour/size)", "other_product": "OTHER PRODUCT",
         "no_product": "no product visible", "unclear": "unclear"}
by_page = {}
for r in key:
    v = {x["label"]: x for x in json.load(open(HERE / "judged" / f"{r['key']}.json"))}[r["label"]]
    runs = {m for m, _ in r["by"]}
    who = "both runs" if len(runs) == 2 else ("with reference only" if "ref" in runs else "without reference only")
    by_page.setdefault(r["key"], []).append({**r, "verdict": v["verdict"], "why": v["why"], "who": who, "runs": runs})

paths = {}
for d in Path("/tmp/tt/img").iterdir():
    for f in d.iterdir(): paths[(d.name, f.stem)] = f


def img(row) -> str:
    h = hashlib.sha1(row["url"].encode()).hexdigest()[:12]
    src = paths.get((row["key"], h))
    if src is None:  # merged copy: the first run's url is kept, which is the one hashed; fall back to the link
        return f'<a href="{html.escape(row["url"])}">image link</a>'
    dest = HERE / "review" / "img" / f"{row['key']}-{h}.jpg"
    shutil.copy(src, dest)
    return f'<img src="img/{dest.name}">'


def tile(row) -> str:
    cls = "bad" if row["verdict"] == "other_product" else ("meh" if row["verdict"] not in RIGHT else "ok")
    return (f'<div class="t {cls}">{img(row)}<b>{NAMES[row["verdict"]]}</b><br>'
            f'<i>{row["who"]}</i><br>{html.escape(row["why"])}</div>')


out = ["""<html><head><meta charset="utf-8"><style>
body{font-family:sans-serif;margin:20px;max-width:1400px} h2{margin-top:40px;border-top:2px solid #ccc;padding-top:10px}
h3{font-size:15px;color:#444} .row{display:flex;flex-wrap:wrap;gap:10px}
.t{width:220px;font-size:12px;border:3px solid #ccc;padding:5px} .t img{width:210px;height:210px;object-fit:contain;background:#f4f4f4;display:block}
.bad{border-color:#d22} .meh{border-color:#e90} .ok{border-color:#2a2} .none{color:#888;font-size:13px}
</style></head><body><h1>Reference test: wrong and missed photos per page</h1>
<p>Red border = other product. Orange = no product visible / unclear. Green = right photo.<br>
"Missed" here means one run picked a right photo that the other run did not. Photos that <b>both</b> runs left out were never judged, so they are not shown.</p>"""]
totals = {"wrong": 0}
for k in sorted(by_page):
    rows = by_page[k]
    wrong = [r for r in rows if r["verdict"] not in RIGHT]
    miss_ref = [r for r in rows if r["verdict"] in RIGHT and r["runs"] == {"noref"}]
    miss_noref = [r for r in rows if r["verdict"] in RIGHT and r["runs"] == {"ref"}]
    out.append(f'<h2>{html.escape(pages[k]["product"])} <small>({k}, {len(rows)} photos picked in total) '
               f'<a href="{html.escape(pages[k]["url"])}">page</a></small></h2>')
    for title, lst in [("Wrong photos picked", sorted(wrong, key=lambda r: r["verdict"])),
                       ("Right photos missed WITH the reference (only the run without it picked them)", miss_ref),
                       ("Right photos missed WITHOUT the reference (only the run with it picked them)", miss_noref)]:
        out.append(f"<h3>{title}: {len(lst)}</h3>")
        out.append('<div class="row">' + "".join(tile(r) for r in lst) + "</div>" if lst else '<div class="none">none</div>')
out.append("</body></html>")
(HERE / "review" / "photos.html").write_text("\n".join(out))
print("ok", len(list((HERE / "review" / "img").iterdir())), "images")
