"""Job A: filter a page's full image list down to product photos.

Junk rules first (deterministic, every drop recorded with a reason), then one gpt-5-mini
call per surviving image: product / not_product / unsure (unsure = keep).

Usage: python filter.py <input.json> <output.json> <image-cache-dir>
input.json: {key: {"product": str, "variant": str|null, "images": [url, ...]}}
"""

import base64
import io
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import httpx
import openai
from PIL import Image
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))
from score import photo_key  # noqa: E402

MODEL = "gpt-5-mini"
MAX_PARALLEL = 10
MIN_SIDE = 200      # smaller than this on the short side is an icon or a thumbnail
MIN_LONG_SIDE = 300
MAX_ASPECT = 5.0    # wider or taller than this is a banner, not a photo
UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}
NAME_JUNK = re.compile(
    r"(^|[/_\-.])(icon|icons|logo|logos|favicon|sprite|badge|badges|payment|paypal|visa|"
    r"mastercard|amex|flag|flags|arrow|star_rating|rating|pixel|spacer|blank|loading|"
    r"placeholder)([/_\-.]|$)",
    re.I,
)

INSTRUCTIONS = """You judge one image taken from a shop's product page. You are told the product's name (and variant, if any).

Answer with exactly one verdict:
- "product": the image shows THIS product itself (the item, its packaging, a close-up, a lifestyle or in-use shot where this product is visible, or a graphic made specifically about this product such as its ingredients, claims, size chart, before/after, or an award seal shown on its gallery).
- "not_product": the image clearly shows something else: a DIFFERENT product (read any visible label or name; a sibling product from the same brand with a different name, format or colourway is not_product), a brand logo on its own, an icon, a payment or shipping badge, a decoration, a screenshot of the site's UI, a generic stock photo, a review photo of a different item, or a text-only graphic not about this product.
- "unsure": you cannot tell. Use this for lifestyle or marketing photos that fit this product's category but where the product itself is not visible or readable (a person applying a serum for a serum product, a lifestyle scene with a slogan). Also use it when the image is too small or too cropped to identify.

If a variant (colour, size, flavour) is given: this product in that variant is "product"; this same product in a different colour or size is "unsure"; a different product is "not_product".

Pages often show a gallery of the product next to recommended sibling products; the label and shape decide, not the brand.

Give a reason of at most 15 words."""


class Verdict(BaseModel):
    verdict: str  # product | not_product | unsure
    reason: str


def _ext(content_type: str, fmt: str | None) -> str:
    return {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif", "AVIF": "avif"}.get(fmt or "", "bin")


AMAZON_SIZED = re.compile(r"\._[A-Za-z0-9_,]+_\.(jpg|jpeg|png|webp)$", re.I)
SHOPIFY_SIZED = re.compile(r"_(\d+x\d*|\d*x\d+|pico|icon|thumb|small|compact|medium|large|grande|original|master)(@[23]x)?(_crop_\w+)?(\.[a-z]+)$", re.I)
LISTED_PX = re.compile(r"(?:_|\b)(?:AC_|)(?:SS|SX|SY|US|UC|UL|SR)(\d{2,4})(?:[,_x]|\b)|_(\d{2,4})x(\d{0,4})(?:@|_|\.)|[?&](?:width|height)=(\d{2,4})", re.I)


def listed_px(url: str) -> int | None:
    """The size the page itself shows the picture at, if its URL says so. A page showing a
    picture at under 100 px is showing a thumbnail of something else, not a gallery photo."""
    sizes = [int(g) for m in LISTED_PX.finditer(url) for g in m.groups() if g]
    return min(sizes) if sizes else None


def full_size_url(url: str) -> str | None:
    """The same picture at its largest, for CDNs whose URLs carry the size. None if unknown."""
    p = urlparse(url)
    host, path = p.netloc.lower(), p.path
    if "media-amazon.com" in host or "images-amazon.com" in host:
        new = AMAZON_SIZED.sub(r".\1", path)
        return p._replace(path=new, query="").geturl() if new != path else None
    if "/cdn/shop/" in path or "cdn.shopify.com" in host:
        new = SHOPIFY_SIZED.sub(r"\4", path)
        q = "&".join(kv for kv in p.query.split("&") if kv and not kv.split("=")[0] in ("width", "height", "crop"))
        if new != path or q != p.query:
            return p._replace(path=new, query=q).geturl()
    return None


def fetch(url: str, path_stem: Path) -> dict:
    """Download one image at its largest known size; return its record (with size), or the
    reason it was dropped."""
    listed = listed_px(url)
    if listed is not None and listed < 100:
        return {"drop": f"tiny-as-listed-{listed}px"}
    try:
        with httpx.Client(headers=UA, timeout=20, follow_redirects=True) as client:
            r = None
            full = full_size_url(url)
            fetched = url
            if full:
                try:
                    rf = client.get(full)
                    if rf.status_code == 200 and rf.headers.get("content-type", "").startswith("image/"):
                        r, fetched = rf, full
                except httpx.HTTPError:
                    pass
            if r is None:
                r = client.get(url)
        if r.status_code != 200:
            return {"drop": f"download-failed-{r.status_code}"}
        data = r.content
        if len(data) > 15_000_000:
            return {"drop": "too-large"}
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
            fmt = im.format
        ext = _ext(r.headers.get("content-type", ""), fmt)
        path = path_stem.with_suffix("." + ext)
        path.write_bytes(data)
        rec = {"w": w, "h": h, "bytes": len(data), "file": path.name, "format": fmt}
        if fetched != url:
            rec["fetched_url"] = fetched
        return rec
    except Exception as e:  # noqa: BLE001
        return {"drop": f"download-failed-{type(e).__name__}"}


def to_jpeg_b64(path: Path, max_side: int = 768) -> str:
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def judge(client: openai.OpenAI, product: str, variant: str | None, path: Path) -> dict:
    text = f"Product: {product}" + (f"\nVariant: {variant}" if variant else "")
    last = None
    for attempt in range(4):
        try:
            raw = client.responses.with_raw_response.parse(
                model=MODEL,
                instructions=INSTRUCTIONS,
                input=[{"role": "user", "content": [
                    {"type": "input_text", "text": text},
                    {"type": "input_image", "image_url": "data:image/jpeg;base64," + to_jpeg_b64(path), "detail": "low"},
                ]}],
                text_format=Verdict,
            )
            usage = json.loads(raw.text).get("usage") or {}
            out = raw.parse().output_parsed
            v = out.verdict if out and out.verdict in ("product", "not_product", "unsure") else "unsure"
            return {"verdict": v, "reason": out.reason if out else "no answer", "in": usage.get("input_tokens", 0), "out": usage.get("output_tokens", 0)}
        except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError) as e:
            last = e
            time.sleep(3 * (attempt + 1))
        except Exception as e:  # noqa: BLE001
            return {"verdict": "unsure", "reason": f"call failed: {type(e).__name__}", "in": 0, "out": 0, "error": str(e)[:200]}
    return {"verdict": "unsure", "reason": f"call failed after retries: {type(last).__name__}", "in": 0, "out": 0}


def filter_page(key: str, entry: dict, cache: Path, client: openai.OpenAI) -> dict:
    cache.mkdir(parents=True, exist_ok=True)
    product, variant = entry["product"], entry.get("variant")
    urls = list(dict.fromkeys(entry["images"]))  # exact-URL dedupe, keep order
    records = [{"n": i, "url": u} for i, u in enumerate(urls)]

    # Rule 1: things that are obviously not photos, by URL alone.
    to_fetch = []
    for rec in records:
        u = rec["url"]
        path = urlparse(u).path if not u.startswith("data:") else ""
        if u.startswith("data:"):
            rec["drop"] = "data-uri"
        elif path.lower().endswith(".svg"):
            rec["drop"] = "svg"
        elif urlparse(u).scheme not in ("http", "https"):
            rec["drop"] = "not-http"
        elif NAME_JUNK.search(path):
            rec["drop"] = "icon-or-logo-by-name"
        else:
            to_fetch.append(rec)

    # Rule 2: download (at full size where the URL carries a size), and drop what's tiny or
    # banner-shaped.
    with ThreadPoolExecutor(MAX_PARALLEL) as pool:
        got = list(pool.map(lambda r: fetch(r["url"], cache / f"{r['n']:03d}"), to_fetch))
    for rec, info in zip(to_fetch, got):
        rec.update(info)
        if "drop" in rec:
            continue
        w, h = rec["w"], rec["h"]
        if min(w, h) < MIN_SIDE or max(w, h) < MIN_LONG_SIDE:
            rec["drop"] = f"tiny-{w}x{h}"
        elif max(w, h) / max(1, min(w, h)) > MAX_ASPECT:
            rec["drop"] = f"banner-shaped-{w}x{h}"

    # Rule 3: one size per picture, the largest.
    best: dict[str, dict] = {}
    for rec in records:
        if "drop" in rec:
            continue
        k = photo_key(rec["url"])
        if k not in best or rec["w"] * rec["h"] > best[k]["w"] * best[k]["h"]:
            best[k] = rec
    for rec in records:
        if "drop" in rec:
            continue
        k = photo_key(rec["url"])
        if best[k] is not rec:
            rec["drop"] = f"smaller-copy-of-{best[k]['n']}"

    # AI judgment, one call per surviving image, in parallel.
    survivors = [r for r in records if "drop" not in r]
    with ThreadPoolExecutor(MAX_PARALLEL) as pool:
        verdicts = list(pool.map(lambda r: judge(client, product, variant, cache / r["file"]), survivors))
    for rec, v in zip(survivors, verdicts):
        rec.update(v)
        if v["verdict"] == "not_product":
            rec["drop"] = "ai-not-product"

    kept = [r for r in records if "drop" not in r]
    dropped = [r for r in records if "drop" in r]
    return {
        "product": product, "variant": variant, "images_in": len(urls),
        "kept": kept, "dropped": dropped,
        "api_calls": len(survivors),
        "input_tokens": sum(r.get("in", 0) for r in survivors),
        "output_tokens": sum(r.get("out", 0) for r in survivors),
    }


def main() -> None:
    src, dst, cache_root = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    pages = json.loads(src.read_text())
    done = json.loads(dst.read_text()) if dst.exists() else {}
    client = openai.OpenAI(max_retries=0, timeout=120)
    for key, entry in pages.items():
        if key in done:
            continue
        t = time.time()
        done[key] = filter_page(key, entry, cache_root / key, client)
        dst.write_text(json.dumps(done, indent=1))
        r = done[key]
        print(f"{key}: {r['images_in']} in -> {len(r['kept'])} kept, {r['api_calls']} calls, {time.time()-t:.0f}s", flush=True)


if __name__ == "__main__":
    main()
