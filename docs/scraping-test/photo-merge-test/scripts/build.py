"""Photo merge test, step 1-3: for each of the 34 judged pages, list the screenshot method's photos
(as they were scored) and the Firecrawl product call's photos, download both, drop small product
photos, build the two "added" sets and remove product photos that copy a screenshot pick.
Then draw blind contact sheets of every added photo (shuffled, labelled A1, A2...).

Screenshot picks:
- 15 hard pages: screen-test, method "screen", first 10 photos (compare.py's rule).
- 19 ordinary pages: text-test, "pieces" call run 1, every photo (build_judging.py's rule).
Product photos: /tmp/st/v2/product/<key>.json, data.product.variants[].images[].url.

No OpenAI or Firecrawl calls: images are fetched straight from the shops' image servers.
Writes outputs/pages/<key>.json, outputs/added-key.json, outputs/sheets/<key>-<k>.jpg.
Usage: backend/.venv/bin/python scripts/build.py"""
import collections, hashlib, io, json, random, re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse, unquote

import httpx
from PIL import Image, ImageDraw, ImageFont

Image.MAX_IMAGE_PIXELS = None
HERE = Path(__file__).resolve().parent.parent
TESTS = HERE.parent
SCREEN, TEXT = TESTS / "screen-test", TESTS / "text-test"
OUT = HERE / "outputs"
(OUT / "pages").mkdir(parents=True, exist_ok=True)
(OUT / "sheets").mkdir(parents=True, exist_ok=True)
IMG = Path("/tmp/pm/img")  # downloaded pictures (not kept in the repo)
IMG.mkdir(parents=True, exist_ok=True)
PRODUCT = Path("/tmp/st/v2/product")
CAP_SCREEN, CAP_ADDED = 10, 10
MIN_SHORT, MIN_LONG = 200, 300
HASH_BITS = 12  # same "same picture" threshold as compare.py
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
           "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}
PAGES = json.load(open(TEXT / "pages.json"))


# --- copied from screen-test/scripts/compare.py, so the picks are rebuilt the same way ---------
def best_url(rec: dict) -> str:
    url, best = rec["src"], 0
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
# -----------------------------------------------------------------------------------------------


def name_key(url: str) -> str:
    """File name without size suffixes, query or extension: the same picture at another size
    or from another CDN path gets the same key."""
    if url.startswith("//"): url = "https:" + url
    name = unquote(urlparse(url).path.rstrip("/").split("/")[-1])
    name = re.sub(r"\._[^/]*?_?\.(?=[a-z0-9]{2,5}$)", ".", name, flags=re.I)  # Amazon ._AC_SL1500_.jpg
    name = re.sub(r"\.[a-z0-9]{2,5}$", "", name, flags=re.I)
    name = re.sub(r"\._[A-Za-z0-9_,]+_?$", "", name)  # Amazon suffix left without extension
    name = re.sub(r"(_\d+x\d*|_\d+x|-\d+x\d+|_x\d+)$", "", name)  # Shopify _800x / _800x600
    return name.casefold()


def dhash(im: Image.Image) -> int:
    g = im.convert("L").resize((17, 16))
    px = list(g.getdata()); bits = 0
    for y in range(16):
        for x in range(16):
            bits = (bits << 1) | (px[y * 17 + x] > px[y * 17 + x + 1])
    return bits


def fetch(url: str, key: str) -> dict:
    """Download once; keep a 1024 px JPEG copy (on white) and the original size."""
    if url.startswith("//"): url = "https:" + url
    d = IMG / key; d.mkdir(exist_ok=True)
    stem = hashlib.sha1(url.encode()).hexdigest()[:12]
    dest, meta = d / f"{stem}.jpg", d / f"{stem}.json"
    if meta.exists(): return json.load(open(meta))
    try:
        r = httpx.get(url, headers=HEADERS, timeout=40, follow_redirects=True)
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content))
        w, h = im.size
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, "white"); bg.paste(im, mask=im.split()[3])
        bg.thumbnail((1024, 1024)); bg.save(dest, "JPEG", quality=85)
        out = {"url": url, "path": str(dest), "w": w, "h": h, "hash": str(dhash(bg)), "error": None}
    except Exception as e:  # noqa: BLE001
        out = {"url": url, "path": None, "w": None, "h": None, "hash": None, "error": f"{type(e).__name__}: {str(e)[:100]}"}
    json.dump(out, open(meta, "w"))
    return out


def verdicts(folder: Path) -> dict:
    v = {}
    for f in folder.glob("*.json"):
        for r in json.load(open(f)): v[(f.stem, r["label"])] = r["verdict"]
    return v


def screen_picks() -> dict[str, list[dict]]:
    """page -> screenshot picks as scored: [{rank, url, verdict, stored_hash}]"""
    out = {}
    # hard pages: compare.py's "screen" list, first 10
    sv = verdicts(SCREEN / "judged")
    by_rank = collections.defaultdict(dict)
    for r in json.load(open(SCREEN / "outputs" / "sheet-key.json")):
        for m, rank in r["by"]:
            if m == "screen": by_rank[r["key"]][rank] = (sv.get((r["key"], r["label"])), r["url"])
    stored = {}
    for c in json.load(open(SCREEN / "outputs" / "compare.json")):
        for p in c["pictures"]:
            for m, rank in p["by"]:
                if m == "screen": stored[(c["key"], rank)] = p["hash"]
    for c in json.load(open(SCREEN / "outputs" / "compare.json")):
        key = c["key"]
        a = json.load(open(SCREEN / "outputs" / "point" / f"{key}.json"))["answer"]
        marks = json.load(open(SCREEN / "outputs" / "pages" / f"{key}.json"))["marks"]
        by_n = {i["n"]: i for i in marks["imgs"] if i["n"]}
        order = sorted(n for n in a["gallery_images"] if n in by_n) + [n for n in a["more_images"] if n in by_n]
        seen, urls = set(), []
        for n in order:
            u = best_url(by_n[n])
            if same_key(u) in seen: continue
            seen.add(same_key(u)); urls.append(u)
        urls = urls[:CAP_SCREEN]
        out[key] = []
        for rank, u in enumerate(urls, 1):
            if rank not in by_rank[key]: continue  # failed to download in the screen test: not scored
            verdict, merged_url = by_rank[key][rank]
            out[key].append({"rank": rank, "url": u, "merged_url": merged_url, "verdict": verdict,
                             "stored_hash": str(stored.get((key, rank)))})
    # ordinary pages: pieces run 1, every photo
    pv = verdicts(TEXT / "judged" / "photos")
    by_rank = collections.defaultdict(dict)
    for r in json.load(open(TEXT / "outputs" / "photo-key.json")):
        for m, rank in r["by"]:
            if m == "pieces-1": by_rank[r["key"]][rank] = (pv.get((r["key"], r["label"])), r["url"])
    for key in sorted(by_rank):
        a = json.load(open(TEXT / "outputs" / "pieces-1" / f"{key}.json"))["answer"]
        marks = json.load(open(TEXT / "outputs" / "pages" / f"{key}.json"))["marks"]
        by_n = {i["n"]: i for i in marks["imgs"] if i["n"]}
        order = sorted(n for n in a["gallery_images"] if n in by_n) + [n for n in a["more_images"] if n in by_n]
        seen, rows = set(), []
        for rank, n in enumerate(order, 1):  # build_judging.py keeps the rank of the first copy
            u = best_url(by_n[n])
            if same_key(u) in seen: continue
            seen.add(same_key(u))
            if rank in by_rank[key]:
                verdict, merged_url = by_rank[key][rank]
                rows.append({"rank": rank, "url": u, "merged_url": merged_url, "verdict": verdict, "stored_hash": None})
        out[key] = rows
    return out


def record(key: str):
    p = PRODUCT / f"{key}.json"
    if not p.exists(): return None, "no product record file"
    rec = (json.load(open(p)).get("data") or {}).get("product")
    if not rec: return None, "product record is empty (call returned no product)"
    return rec, None


def product_photos(key: str, rec: dict) -> list[dict]:
    """Every photo in the record, page's variant first, then record order; one per file name."""
    page_url = unquote(PAGES[key]["url"]); rec_url = unquote(rec.get("url") or "")
    variants = rec.get("variants") or []

    def matches(v):
        ids = [str(v.get(f) or "") for f in ("id", "sku")]
        for i in ids:
            if len(i) >= 5 and (re.search(rf"(?<![A-Za-z0-9]){re.escape(i)}(?![A-Za-z0-9])", page_url)
                                or re.search(rf"variant={re.escape(i)}(?!\d)", rec_url)
                                or re.search(rf"/dp/{re.escape(i)}(?![A-Za-z0-9])", rec_url)):
                return True
        return False

    first = next((i for i, v in enumerate(variants) if matches(v)), 0)
    order = [first] + [i for i in range(len(variants)) if i != first]
    seen, out = set(), []
    for vi in order:
        v = variants[vi]
        for img in v.get("images") or []:
            u = img.get("url") if isinstance(img, dict) else img
            if not u: continue
            if u.startswith("//"): u = "https:" + u
            if not u.startswith("http"): continue
            nk = name_key(u)
            if nk in seen: continue
            seen.add(nk)
            out.append({"url": u, "name": nk, "variant": v.get("title") or json.dumps(v.get("values")),
                        "page_variant": vi == first, "order": len(out)})
    return out


def close(h1, h2) -> bool:
    return h1 not in (None, "None") and h2 not in (None, "None") and bin(int(h1) ^ int(h2)).count("1") <= HASH_BITS


def do_page(key: str, picks: list[dict]) -> dict:
    res = {"key": key, "set": PAGES[key]["set"], "product": PAGES[key]["product"], "url": PAGES[key]["url"],
           "screen": picks, "record_problem": None, "product_photos": []}
    for p in picks:
        f = fetch(p["url"], key)
        p["hash"] = f["hash"] or (p["stored_hash"] if p["stored_hash"] != "None" else None)
        p["fetch_error"] = f["error"]
        p["name"] = name_key(p["url"])
        p["names"] = sorted({name_key(p["url"]), name_key(p["merged_url"])})
    rec, problem = record(key)
    if problem:
        res["record_problem"] = problem; return res
    photos = product_photos(key, rec)
    if not photos:
        res["record_problem"] = "record has no photos"
    screen_names = {n for p in picks for n in p["names"]}
    screen_hashes = [h for p in picks for h in (p["hash"], p["stored_hash"]) if h not in (None, "None")]
    kept_hashes = []
    for ph in photos:
        f = fetch(ph["url"], key)
        ph.update({"path": f["path"], "w": f["w"], "h": f["h"], "hash": f["hash"], "fetch_error": f["error"]})
        if f["error"]:
            ph["drop"] = "download failed"; continue
        if min(f["w"], f["h"]) < MIN_SHORT or max(f["w"], f["h"]) < MIN_LONG:
            ph["drop"] = "small"; continue
        if any(close(ph["hash"], h) for h in kept_hashes):
            ph["drop"] = "copy of an earlier product photo"; continue
        kept_hashes.append(ph["hash"])
        ph["drop"] = None
    big = [ph for ph in photos if ph["drop"] is None]
    capped = {id(ph) for ph in big[:CAP_ADDED]}  # page's variant first, then record order
    for ph in photos:
        ph["in_capped"] = id(ph) in capped
        if ph["drop"]: continue
        if ph["name"] in screen_names:
            ph["copy_of_screen"] = "file name"
        elif any(close(ph["hash"], h) for h in screen_hashes):
            ph["copy_of_screen"] = "picture hash"
        else:
            ph["copy_of_screen"] = None
    res["product_photos"] = photos
    return res


def sheets(key: str, added: list[dict], rng: random.Random) -> list[dict]:
    added = added[:]
    rng.shuffle(added)
    font = ImageFont.load_default(size=36)
    rows_out, T, COLS, PER = [], 340, 4, 16
    for s in range(0, len(added), PER):
        chunk = added[s:s + PER]
        nrows = (len(chunk) + COLS - 1) // COLS
        sheet = Image.new("RGB", (COLS * T, nrows * T), "white"); d = ImageDraw.Draw(sheet)
        for i, p in enumerate(chunk):
            label = f"A{s + i + 1}"
            im = Image.open(p["path"]); im.thumbnail((T - 10, T - 10))
            x, y = (i % COLS) * T, (i // COLS) * T
            sheet.paste(im, (x + 5 + (T - 10 - im.width) // 2, y + 5 + (T - 10 - im.height) // 2))
            d.rectangle([x + 5, y + 5, x + 105, y + 50], fill="black"); d.text((x + 12, y + 8), label, fill="white", font=font)
            d.rectangle([x, y, x + T - 1, y + T - 1], outline="#bbbbbb")
            rows_out.append({"key": key, "label": label, "sheet": f"{key}-{s // PER + 1}.jpg", "url": p["url"],
                             "path": p["path"], "in_capped": p["in_capped"]})
        sheet.save(OUT / "sheets" / f"{key}-{s // PER + 1}.jpg", quality=88)
    return rows_out


if __name__ == "__main__":
    picks = screen_picks()
    keys = sorted(picks)
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda k: do_page(k, picks[k]), keys))
    key_rows = []
    for f in (OUT / "sheets").glob("*.jpg"): f.unlink()
    for res in results:
        json.dump(res, open(OUT / "pages" / f"{res['key']}.json", "w"), indent=1)
        added = [ph for ph in res["product_photos"] if ph["drop"] is None and ph["copy_of_screen"] is None]
        key_rows += sheets(res["key"], added, random.Random(res["key"]))
        c = collections.Counter(ph["drop"] or ("copy of screen: " + ph["copy_of_screen"] if ph["copy_of_screen"] else "added")
                                for ph in res["product_photos"])
        print(f"{res['key']:42s} screen {len(res['screen']):3d}  product {len(res['product_photos']):3d}  "
              f"{dict(c)}  {res['record_problem'] or ''}")
    json.dump(key_rows, open(OUT / "added-key.json", "w"), indent=1)
    print("BUILD DONE", len(key_rows), "added photos;", sum(r["in_capped"] for r in key_rows), "in the capped set")
