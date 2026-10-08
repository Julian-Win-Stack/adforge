"""Build the blind judging material for the text test.
- Photos (normal pages): the "pieces" version's gallery + more pictures from runs 1 and 2, copies
  merged, shuffled into contact sheets (sheets/<key>-<k>.jpg, key in photo-key.json).
- Text (all pages): every sentence kept by pieces-1/2 and copy-1/2 (copy sentences only if found in
  the page's own text), merged and shuffled (text/<key>.json, key in text-key.json).
- tops/<key>.jpg: the top of the page, so judges can see what it sells.
Usage (backend container, /tmp/tt): python build_judging.py"""
import hashlib, json, random, re, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, "/tmp/sp")
ROOT = Path("/tmp/tt")
sys.argv = ["x"]  # compare.py reads nothing from argv, but runs on import: copy what we need instead
exec(open("/tmp/sp/compare.py").read().split("def methods_for")[0].replace('ROOT = Path("/tmp/sp")', 'ROOT = Path("/tmp/tt")'))
from textnorm import norm, found, sentences  # noqa: E402

TOPS, TEXT = ROOT / "tops", ROOT / "text"
TOPS.mkdir(exist_ok=True); TEXT.mkdir(exist_ok=True)
RUNS = {"pieces": ["1", "2"], "copy": ["1", "2"]}


def load(kind: str, run: str, key: str):
    p = ROOT / f"{kind}-{run}" / f"{key}.json"
    return json.load(open(p))["answer"] if p.exists() else None


def page_text(page: dict) -> str:
    secs = page["marks"]["secs"]
    return norm(" ".join(s["heading"] + " " + s["text"] for s in secs) + " " + (page.get("markdown") or ""))


def kept_sentences(key: str, page: dict) -> dict[str, list[str]]:
    secs = {s["i"]: s for s in page["marks"]["secs"]}
    all_text = page_text(page)
    out = {}
    for run in RUNS["pieces"]:
        a = load("pieces", run, key)
        if a is None: continue
        out[f"pieces-{run}"] = [x for i in a["product_sections"] if i in secs
                                for x in sentences(secs[i]["heading"] + ". " + secs[i]["text"])]
    for run in RUNS["copy"]:
        a = load("copy", run, key)
        if a is None: continue
        out[f"copy-{run}"] = [x for p in a["passages"] for x in sentences(p) if found(x, all_text)]
        out[f"copy-{run}-dropped"] = [x for p in a["passages"] for x in sentences(p) if not found(x, all_text)]
    return out


def photos(key: str, page: dict) -> dict:
    by_n = {i["n"]: i for i in page["marks"]["imgs"] if i["n"]}
    (IMG / key).mkdir(exist_ok=True)
    pics, errors = [], []
    for run in RUNS["pieces"]:
        a = load("pieces", run, key)
        if a is None: continue
        order = sorted(n for n in a["gallery_images"] if n in by_n) + [n for n in a["more_images"] if n in by_n]
        seen = set()
        for rank, n in enumerate(order, 1):
            url = best_url(by_n[n])
            if same_key(url) in seen: continue
            seen.add(same_key(url))
            dest = IMG / key / (hashlib.sha1(url.encode()).hexdigest()[:12] + ".jpg")
            err = fetch(url, dest)
            if err: errors.append(f"{url[:90]}: {err}"); continue
            pics.append((f"pieces-{run}", rank, {"url": url, "path": str(dest), "hash": dhash(dest)}))
    uniq = []
    for m, rank, it in pics:
        for u in uniq:
            if bin(u["hash"] ^ it["hash"]).count("1") <= 12:
                u["by"].append([m, rank]); break
        else:
            uniq.append({"path": it["path"], "hash": it["hash"], "url": it["url"], "by": [[m, rank]]})
    return {"key": key, "pictures": uniq, "errors": errors}


def top(key: str):
    im = Image.open(ROOT / "shots" / f"{key}.png").convert("RGB")
    im = im.crop((0, 0, im.width, min(im.height, 2400))); im.thumbnail((1400, 2400))
    im.save(TOPS / f"{key}.jpg", quality=80)


def sheets(res: dict, rng: random.Random) -> list[dict]:
    key, pics = res["key"], res["pictures"][:]
    rng.shuffle(pics)
    font = ImageFont.load_default(size=40)
    keyrows, T, COLS = [], 340, 4
    for s in range(0, len(pics), 16):
        chunk = pics[s:s + 16]
        rows = (len(chunk) + COLS - 1) // COLS
        sheet = Image.new("RGB", (COLS * T, rows * T), "white"); d = ImageDraw.Draw(sheet)
        for i, p in enumerate(chunk):
            label = f"P{s + i + 1}"
            im = Image.open(p["path"]); im.thumbnail((T - 10, T - 10))
            x, y = (i % COLS) * T, (i // COLS) * T
            sheet.paste(im, (x + 5 + (T - 10 - im.width) // 2, y + 5 + (T - 10 - im.height) // 2))
            d.rectangle([x + 5, y + 5, x + 110, y + 55], fill="black"); d.text((x + 12, y + 8), label, fill="white", font=font)
            keyrows.append({"key": key, "label": label, "sheet": f"{key}-{s // 16 + 1}.jpg", "by": p["by"], "url": p["url"]})
        sheet.save(SHEETS / f"{key}-{s // 16 + 1}.jpg", quality=85)
    return keyrows


def do(key: str):
    if not (ROOT / "pages" / f"{key}.json").exists(): return key, None, None
    page = json.load(open(ROOT / "pages" / f"{key}.json"))
    if not page.get("marks"): return key, None, None
    top(key)
    kept = kept_sentences(key, page)
    res = photos(key, page) if pages[key]["set"] == "normal" else None
    return key, kept, res


with ThreadPoolExecutor(12) as ex:
    done = list(ex.map(do, sorted(pages)))
photo_key, text_key, kept_all = [], [], {}
for key, kept, res in done:
    if kept is None: print(key, "NO MARKS"); continue
    if not any(k in kept for k in ("pieces-1", "pieces-2", "copy-1", "copy-2")): continue
    rng = random.Random(key)  # same shuffle on every rebuild, so finished judging stays valid
    kept_all[key] = kept
    if res: photo_key += sheets(res, rng)
    units = {}
    for version, lst in kept.items():
        if version.endswith("-dropped"): continue
        for s in lst:
            units.setdefault(norm(s), {"text": s, "by": set()})["by"].add(version)
    items = list(units.values()); rng.shuffle(items)
    rows = [{"id": f"S{i + 1}", "text": u["text"]} for i, u in enumerate(items)]
    json.dump(rows, open(TEXT / f"{key}.json", "w"), indent=1, ensure_ascii=False)
    text_key += [{"key": key, "id": f"S{i + 1}", "by": sorted(u["by"])} for i, u in enumerate(items)]
    print(key, {v: len(l) for v, l in kept.items()}, "units", len(rows), "photos", len(res["pictures"]) if res else "-")
json.dump(photo_key, open(ROOT / "photo-key.json", "w"), indent=1)
json.dump(text_key, open(ROOT / "text-key.json", "w"), indent=1)
json.dump(kept_all, open(ROOT / "kept.json", "w"), indent=1, ensure_ascii=False)
print("BUILD DONE", len(photo_key), "photos,", len(text_key), "sentences")
