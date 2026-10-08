"""Build each method's top-10 photos per page, download them, merge copies of the same picture,
and draw blind contact sheets (pictures shuffled, labelled P1, P2...) for judging.
Methods: tested = the fix test's reference + link rule; shop = the shop's own list; screen = the
screenshot method; shop_else_screen = shop if it gives 3+ photos, else screen.
Writes /tmp/sp/compare.json, /tmp/sp/sheets/<key>-<k>.jpg and /tmp/sp/sheet-key.json."""
import hashlib, io, json, random, re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
import httpx
from PIL import Image, ImageDraw, ImageFont

Image.MAX_IMAGE_PIXELS = None
ROOT = Path("/tmp/sp"); IMG = ROOT / "img"; SHEETS = ROOT / "sheets"
IMG.mkdir(exist_ok=True); SHEETS.mkdir(exist_ok=True)
V2 = ROOT / "v2"  # the fix test's photo-final.json and photo-out/
CACHE = Path("/tmp/st/cache/100")  # the first test's downloaded images
CAP = 10
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
           "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}
pages = json.load(open(ROOT / "pages.json"))
final = json.load(open(V2 / "photo-final.json")) if (V2 / "photo-final.json").exists() else []


def best_url(rec: dict) -> str:
    url, best = rec["src"], 0
    # Candidates are split at a comma followed by a space: some image links hold commas.
    for part in re.split(r",\s+", (rec.get("srcset") or "").strip()):
        bits = part.strip().split()
        if (len(bits) == 2 and bits[0].startswith(("http", "//")) and bits[1].endswith("w")
                and bits[1][:-1].isdigit() and int(bits[1][:-1]) > best):
            best, url = int(bits[1][:-1]), bits[0]
    if url.startswith("//"): url = "https:" + url
    return full_size(url)


def full_size(url: str) -> str:
    u = urlparse(url)
    if "/cdn/shop/" in u.path or "cdn.shopify.com" in u.netloc:
        q = [(k, v) for k, v in parse_qsl(u.query) if k not in ("width", "height", "crop")]
        return urlunparse(u._replace(query=urlencode(q)))
    if "media-amazon.com" in u.netloc:
        return urlunparse(u._replace(path=re.sub(r"\._[^/]*_\.", ".", u.path), query=""))
    return url


def same_key(url: str) -> str:
    u = urlparse(full_size(url))
    return u.netloc + re.sub(r"_\d+x\d*(?=\.)", "", u.path)


def fetch(url: str, dest: Path) -> str | None:
    if dest.exists(): return None
    try:
        r = httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content)).convert("RGB"); im.thumbnail((1024, 1024))
        im.save(dest, "JPEG", quality=85); return None
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {str(e)[:80]}"


def dhash(path: Path) -> int:
    im = Image.open(path).convert("L").resize((17, 16))
    px = list(im.getdata()); bits = 0
    for y in range(16):
        for x in range(16):
            bits = (bits << 1) | (px[y * 17 + x] > px[y * 17 + x + 1])
    return bits


def methods_for(key: str) -> dict[str, list[dict]]:
    """Each method's photos in order: {url or file}."""
    out = {}
    # tested: reference first, then page order
    ref_path = V2 / "photo-out" / f"{key}.json"
    ref_n = json.load(open(ref_path)).get("ref_n") if ref_path.exists() else None
    rows = [r for r in final if r["key"] == key and r.get("new_keep")]
    rows.sort(key=lambda r: (r["n"] != ref_n, r["n"]))
    out["tested"] = [{"file": str(CACHE / key / r["file"]), "url": r["url"]} for r in rows]
    # shop's own list
    shop = json.load(open(ROOT / "shop" / f"{key}.json"))
    lst = []
    for src in ("shopify", "amazon", "json_ld"):
        if shop.get(src) and shop[src].get("photos"):
            lst = shop[src]["photos"]; out["shop_source"] = src; break
    out["shop"] = [{"url": full_size(u if not u.startswith("//") else "https:" + u)} for u in lst]
    # screenshot method: gallery in page order, then the rest
    pt = ROOT / "point" / f"{key}.json"
    marks = json.load(open(ROOT / "pages" / f"{key}.json"))["marks"]
    by_n = {i["n"]: i for i in marks["imgs"] if i["n"]}
    if pt.exists():
        a = json.load(open(pt))["answer"]
        order = sorted(n for n in a["gallery_images"] if n in by_n) + [n for n in a["more_images"] if n in by_n]
        out["screen"] = [{"url": best_url(by_n[n]), "n": n} for n in order]
    else:
        out["screen"] = []
    for m in ("tested", "shop", "screen"):  # one entry per picture, capped
        seen, kept = set(), []
        for it in out[m]:
            k = same_key(it["url"])
            if k in seen: continue
            seen.add(k); kept.append(it)
        out[m + "_all"] = len(kept); out[m] = kept[:CAP]
    out["shop_else_screen"] = out["shop"] if len(out["shop"]) >= 3 else out["screen"]
    return out


def build(key: str) -> dict:
    ms = methods_for(key)
    (IMG / key).mkdir(exist_ok=True)
    pics, errors = [], []
    for m in ("tested", "shop", "screen"):
        for rank, it in enumerate(ms[m], 1):
            if it.get("file"):
                dest = IMG / key / (hashlib.sha1(it["file"].encode()).hexdigest()[:12] + ".jpg")
                if not dest.exists():
                    try:
                        im = Image.open(it["file"]).convert("RGB"); im.thumbnail((1024, 1024)); im.save(dest, "JPEG", quality=85)
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"{m} {it['file']}: {e}"); continue
            else:
                dest = IMG / key / (hashlib.sha1(it["url"].encode()).hexdigest()[:12] + ".jpg")
                err = fetch(it["url"], dest)
                if err: errors.append(f"{m} {it['url'][:90]}: {err}"); continue
            it["path"] = str(dest); it["hash"] = dhash(dest)
            pics.append((m, rank, it))
    # merge copies of the same picture (near-identical hash)
    uniq = []
    for m, rank, it in pics:
        for u in uniq:
            if bin(u["hash"] ^ it["hash"]).count("1") <= 12:
                u["by"].append([m, rank]); break
        else:
            uniq.append({"path": it["path"], "hash": it["hash"], "url": it["url"], "by": [[m, rank]]})
    return {"key": key, "counts": {m: len(ms[m]) for m in ("tested", "shop", "screen")},
            "all": {m: ms[m + "_all"] for m in ("tested", "shop", "screen")},
            "shop_source": ms.get("shop_source"), "fallback": "shop" if len(ms["shop"]) >= 3 else "screen",
            "pictures": uniq, "errors": errors}


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
            keyrows.append({"key": key, "label": label, "sheet": f"{key}-{s // 16 + 1}.jpg", "by": p["by"], "url": p["url"], "path": p["path"]})
        sheet.save(SHEETS / f"{key}-{s // 16 + 1}.jpg", quality=85)
    return keyrows


with ThreadPoolExecutor(6) as ex:
    results = list(ex.map(build, sorted(pages)))
rng = random.Random(7)
key_rows = [row for r in results for row in sheets(r, rng)]
json.dump(results, open(ROOT / "compare.json", "w"), indent=1)
json.dump(key_rows, open(ROOT / "sheet-key.json", "w"), indent=1)
for r in results:
    print(r["key"], "top10:", r["counts"], "all:", r["all"], "shop from", r["shop_source"], "unique", len(r["pictures"]), "errors", len(r["errors"]))
print("COMPARE DONE", len(key_rows), "pictures to judge")
