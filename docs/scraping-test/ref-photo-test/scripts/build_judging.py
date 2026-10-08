"""Build the blind photo judging material for the reference test: every photo picked by the "ref"
and "noref" runs (gallery + more, no cap), downloaded, copies merged, shuffled into contact sheets
(sheets/<key>-<k>.jpg, labels P1, P2...). Which run picked which photo goes to outputs/photo-key.json,
which judges don't open. tops/<key>.jpg: the top of the page, so judges can see what it sells.
Usage: python build_judging.py"""
import hashlib, json, random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parents[1]
SRC = Path(__file__).resolve().parents[2] / "screen-test" / "scripts" / "compare.py"
# compare.py runs on import; take only its helpers (best_url, same_key, fetch, dhash), pointed at /tmp/tt
exec(open(SRC).read().split("def methods_for")[0].replace('ROOT = Path("/tmp/sp")', 'ROOT = Path("/tmp/tt")'))
SHEETS, TOPS = HERE / "sheets", HERE / "tops"
RUNS = ["ref", "noref"]


def load(run: str, key: str):
    p = HERE / "outputs" / run / f"{key}.json"
    return json.load(open(p))["answer"] if p.exists() else None


def photos(key: str, page: dict) -> dict:
    by_n = {i["n"]: i for i in page["marks"]["imgs"] if i["n"]}
    (IMG / key).mkdir(exist_ok=True)
    pics, errors, counts = [], [], {}
    for run in RUNS:
        a = load(run, key)
        if a is None: continue
        order = sorted(n for n in a["gallery_images"] if n in by_n) + [n for n in a["more_images"] if n in by_n]
        seen = set()
        for rank, n in enumerate(order, 1):
            url = best_url(by_n[n])
            if same_key(url) in seen: continue
            seen.add(same_key(url))
            counts[run] = counts.get(run, 0) + 1
            dest = IMG / key / (hashlib.sha1(url.encode()).hexdigest()[:12] + ".jpg")
            err = fetch(url, dest)
            if err: errors.append({"run": run, "url": url, "error": err}); continue
            pics.append((run, rank, {"url": url, "path": str(dest), "hash": dhash(dest)}))
    uniq = []
    for m, rank, it in pics:
        for u in uniq:
            if bin(u["hash"] ^ it["hash"]).count("1") <= 12:
                u["by"].append([m, rank]); break
        else:
            uniq.append({"path": it["path"], "hash": it["hash"], "url": it["url"], "by": [[m, rank]]})
    return {"key": key, "pictures": uniq, "errors": errors, "counts": counts}


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
    page = json.load(open(ROOT / "pages" / f"{key}.json"))
    if not page.get("marks"): return None
    top(key)
    return photos(key, page)


with ThreadPoolExecutor(12) as ex:
    done = [r for r in ex.map(do, sorted(pages)) if r]
photo_key, errors = [], {}
for res in done:
    if res["errors"]: errors[res["key"]] = res["errors"]
    if res["pictures"]: photo_key += sheets(res, random.Random(res["key"]))
    print(res["key"], res["counts"], "unique", len(res["pictures"]), "failed", len(res["errors"]))
json.dump(photo_key, open(HERE / "outputs" / "photo-key.json", "w"), indent=1)
json.dump(errors, open(HERE / "outputs" / "download-errors.json", "w"), indent=1)
print("BUILD DONE", len(photo_key), "photos on sheets")
