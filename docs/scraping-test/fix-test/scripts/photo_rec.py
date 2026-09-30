"""Step 1 + step 2 photo test. The reference is the Firecrawl `product` record (step 1): all its
official photos (one per colour/shade/flavour variant, up to 6) plus its text. One call per page
photo answers same_product / other_product / no_product_visible.
Usage: python photo_rec.py fetch            download record photos to /tmp/st/v2/recimg/<key>/
       python photo_rec.py run [keys...]    judge; writes /tmp/st/v2/photo-rec/<key>.json"""
import hashlib, io, json, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

MODEL = "gpt-5-mini"
CACHE = Path("/tmp/st/cache/100")
V2 = Path("/tmp/st/v2")
RECIMG = V2 / "recimg"
OUT = V2 / "photo-rec"
MAX_REF = 6
RELATIONS = ("same_product", "other_product", "no_product_visible")
SIZE_KEYS = re.compile(r"size|pack|count|quantity|qty|capacity|weight|volume|length|width|amount", re.I)

INSTRUCTIONS = """You check whether a picture from a shop's product page shows the product being sold, so that only that product's photos go into its advert. You get the shop's official record of the product (title, brand, category, description, variant names) and its official photos, one per variant, then the picture to judge.
Answer with one relation for the picture to judge:
- same_product: it shows this product (any of its variants in the record: colour, shade, flavour, size), from any angle, close-up, packaging, in use or on a model, and the product is clearly visible.
- other_product: it shows a different product, even from the same brand, set or collection; a lookalike with a different name, label or shape is a different product.
- no_product_visible: the product is not visible: scenery, a texture, ingredients, a logo, a chart, text, a person or pet without the product.
Be strict. Give one sentence why."""


def load_record(key: str) -> dict | None:
    p = Path(f"/tmp/st/v2/product/{key}.json")
    if not p.exists():
        return None
    return (json.load(open(p)).get("data") or {}).get("product")


def base(url: str) -> str:
    name = urlparse(url).path.rstrip("/").split("/")[-1]
    name = re.sub(r"\.[a-z0-9]{2,5}$", "", name, flags=re.I)
    name = re.sub(r"(_\d+x\d*|_\d+x|-\d+x\d+|\._[A-Z0-9_,]+_)$", "", name)
    return name.casefold()


def ref_urls(rec: dict) -> list[str]:
    """First image of each distinct look (variant values without size-like keys), deduped, max 6.
    If there are fewer looks than 6, the first look's other images are not added: one per variant."""
    seen_look, seen_img, out = set(), set(), []
    for v in rec.get("variants") or []:
        look = tuple(sorted((k, str(x)) for k, x in (v.get("values") or {}).items() if not SIZE_KEYS.search(k)))
        if look in seen_look:
            continue
        for img in v.get("images") or []:
            u = img.get("url") or ""
            if u.startswith("//"): u = "https:" + u
            if not u.startswith("http") or base(u) in seen_img:
                continue
            seen_look.add(look); seen_img.add(base(u)); out.append(u)
            break
        if len(out) >= MAX_REF:
            break
    for img in rec.get("images") or []:  # product-level images, if the record has any
        if len(out) >= MAX_REF: break
        u = img.get("url") if isinstance(img, dict) else img
        if u and base(u) not in seen_img:
            seen_img.add(base(u)); out.append(u)
    return out


def record_text(rec: dict) -> str:
    desc = re.sub(r"\s+", " ", rec.get("description") or "(none)").strip()
    if len(desc) > 2000: desc = desc[:2000] + " [...]"
    names, seen = [], set()
    for v in rec.get("variants") or []:
        t = v.get("title") or ""
        if t and t not in seen:
            seen.add(t); names.append(t)
    vn = "; ".join(names[:40]) + (f"; ... ({len(names)} in all)" if len(names) > 40 else "")
    return (f"Title: {rec.get('title')}\nBrand: {rec.get('brand') or '(none)'}\nCategory: {rec.get('category') or '(none)'}\n"
            f"Page: {rec.get('url')}\nVariants: {vn or '(none)'}\nDescription: {desc}")


def fetch_refs(key: str) -> tuple[str, int, int]:
    import httpx
    from PIL import Image
    rec = load_record(key)
    if not rec: return key, 0, 0
    d = RECIMG / key; d.mkdir(parents=True, exist_ok=True)
    got, urls = [], ref_urls(rec)
    for u in urls:
        f = d / (hashlib.md5(u.encode()).hexdigest()[:12] + ".jpg")
        if not f.exists():
            try:
                r = httpx.get(u, timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"})
                r.raise_for_status()
                with Image.open(io.BytesIO(r.content)) as im:
                    im.convert("RGB").save(f, "JPEG", quality=90)
            except Exception as e:  # noqa: BLE001
                print("  fail", key, u[:100], type(e).__name__, flush=True); continue
        got.append({"url": u, "file": f.name})
    json.dump(got, open(d / "refs.json", "w"))
    return key, len(urls), len(got)


def run_page(key, rows):
    import openai
    import photo_ai
    from photo_ai import call, image_part
    photo_ai.MODEL = MODEL
    from pydantic import BaseModel

    class Verdict(BaseModel):
        relation: str
        why: str

    path = OUT / f"{key}.json"
    if path.exists(): return key, "cached"
    rec = load_record(key)
    refs = json.load(open(RECIMG / key / "refs.json")) if (RECIMG / key / "refs.json").exists() else []
    client = openai.OpenAI()
    rows = [r for r in rows if (CACHE / key / r["file"]).exists()]
    head = [{"type": "input_text", "text": "Official product record:\n" + record_text(rec)}]
    for i, ref in enumerate(refs, 1):
        head.append({"type": "input_text", "text": f"Official photo {i} of this product:"})
        head.append(image_part(RECIMG / key / ref["file"]))
    if not refs:
        head.append({"type": "input_text", "text": "(The record has no official photos.)"})

    def judge(row):
        t0 = time.time()
        content = head + [{"type": "input_text", "text": "Picture to judge:"}, image_part(CACHE / key / row["file"])]
        out, tin, tout, err = call(client, INSTRUCTIONS, content, Verdict)
        rel = out.relation if out and out.relation in RELATIONS else "unclear"
        return {"n": row["n"], "relation": rel, "why": out.why if out else err, "in": tin, "out": tout, "secs": round(time.time() - t0, 1)}

    t0 = time.time()
    with ThreadPoolExecutor(8) as ex:
        verdicts = list(ex.map(judge, rows))
    json.dump({"key": key, "refs": refs, "verdicts": verdicts, "wall": round(time.time() - t0, 1)}, open(path, "w"))
    return key, f"{len(rows)} images, {len(refs)} refs, {time.time() - t0:.0f}s"


if __name__ == "__main__":
    sys.path.insert(0, str(V2))
    truth = json.load(open(V2 / "photo_truth.json"))
    by_key = {}
    for r in truth:
        if r["stage"] in ("kept", "ai"):
            by_key.setdefault(r["key"], []).append(r)
    keys = sorted(k for k in by_key if load_record(k) and (len(sys.argv) < 3 or k in sys.argv[2:]))
    if sys.argv[1] == "fetch":
        with ThreadPoolExecutor(8) as ex:
            for k, want, got in ex.map(fetch_refs, keys):
                print(k, f"{got}/{want}", flush=True)
        print("FETCH DONE")
    else:
        OUT.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        with ThreadPoolExecutor(8) as pool:  # 8 pages x 8 images = 64 calls in flight
            for key, msg in pool.map(lambda k: run_page(k, by_key[k]), keys):
                print(key, msg, flush=True)
        print(f"PHOTO DONE {time.time() - t0:.0f}s")
