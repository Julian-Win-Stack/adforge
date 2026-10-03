"""Text test, version "copy": show the model only the page's screenshot (cut into parts) and ask it
to copy out, word for word, the text about the product being sold. The copied sentences are later
kept only if they are found in the page's own text (score step), so a misread word can't get in.
Writes $TT_ROOT/copy-<run>/<key>.json (default /tmp/tt). Usage: python point_copy.py <run> [keys...]"""
import base64, io, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
import openai
from PIL import Image, ImageStat
from pydantic import BaseModel

Image.MAX_IMAGE_PIXELS = None
MODEL = "gpt-5.6-sol"
RUN = sys.argv[1]
ONLY = set(sys.argv[2:])
ROOT = Path(os.environ.get("TT_ROOT", "/tmp/tt"))
OUT = ROOT / f"copy-{RUN}"; OUT.mkdir(exist_ok=True)
TILE_H, MAX_TILES, SECTION_CHARS = 1200, 24, 2000

INSTRUCTIONS = """You look at one shop's product page the way a shopper would, to copy out the text about the product for an advert. Nothing about other products may get through.

You get screenshots of the page from top to bottom, cut into parts. Ignore the blue and red labels drawn on it.

First decide which ONE product the page sells: the one with the page title, price and buy button at the top. If the page lets the shopper choose a colour, flavour, shade or size, that choice is part of the same product.

Then copy, word for word as printed on the page, every passage that describes this product: its name, what it is, materials or ingredients, sizes, fit, dimensions, how to use, benefits, claims, specs, what's in the box, warranty, shipping and returns for this item, its FAQ. Copy exactly; do not fix, shorten, join or rephrase. One passage per paragraph or list item. Skip the price.

Never copy text about other products: "you may also like", "shop the look", "pairs well with", "complete the set", bundles and kits, the rest of a collection, other flavours, scents, models or versions sold on their own pages, accessories sold separately, comparisons with the brand's other products, even from the same brand. Also leave out reviews, Q&A from shoppers, navigation, cart, promotions, newsletter and footer text. When unsure whether a passage is about exactly this product, leave it out: missing text costs little, wrong text puts another product in the advert."""


class Answer(BaseModel):
    product: str
    passages: list[str]
    notes: str


def tiles(shot: Path) -> list[str]:
    im = Image.open(shot).convert("RGB")
    parts = []
    for top in range(0, im.height, TILE_H):
        tile = im.crop((0, top, im.width, min(top + TILE_H, im.height)))
        small = tile.resize((96, max(1, tile.height // 20)))
        if max(hi - lo for lo, hi in ImageStat.Stat(small).extrema) < 12:
            continue  # a blank stretch
        buf = io.BytesIO(); tile.save(buf, "JPEG", quality=80)
        parts.append((top, base64.b64encode(buf.getvalue()).decode()))
        if len(parts) >= MAX_TILES:
            break
    return parts


def other_page(href: str, page_url: str) -> str:
    if not href: return ""
    u, p = urlparse(href), urlparse(page_url)
    if u.scheme not in ("http", "https") or (u.path.rstrip("/") == p.path.rstrip("/") and u.netloc == p.netloc):
        return ""
    if u.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")):
        return ""
    return (u.netloc if u.netloc != p.netloc else "") + u.path


def run(key: str) -> str:
    path = OUT / f"{key}.json"
    if path.exists():
        return f"{key} cached"
    page = json.load(open(ROOT / "pages" / f"{key}.json"))
    marks, shot = page.get("marks"), ROOT / "shots" / f"{key}.png"
    if not marks or not shot.exists():
        return f"{key} no marks/screenshot"
    title = (page.get("metadata") or {}).get("title") or ""
    parts = tiles(shot)
    content = [{"type": "input_text", "text": f"Page link: {page['url']}\nPage title: {title}\n\nScreenshots follow, top to bottom."}]
    for n, (top, b64) in enumerate(parts, 1):
        content.append({"type": "input_text", "text": f"Part {n} of {len(parts)} (from {top} px down the page):"})
        content.append({"type": "input_image", "image_url": "data:image/jpeg;base64," + b64, "detail": "high"})
    client = openai.OpenAI()
    t = time.time()
    for attempt in range(4):
        try:
            raw = client.responses.with_raw_response.parse(
                model=MODEL, instructions=INSTRUCTIONS, input=[{"role": "user", "content": content}],
                text_format=Answer, max_output_tokens=30_000)
            usage = json.loads(raw.text).get("usage") or {}
            ans = raw.parse().output_parsed
            json.dump({"key": key, "model": MODEL, "answer": ans.model_dump(), "tiles": len(parts),
                       "in": usage.get("input_tokens", 0), "out": usage.get("output_tokens", 0),
                       "seconds": round(time.time() - t)}, open(path, "w"), indent=1)
            return (f"{key} ok {round(time.time()-t)}s tiles={len(parts)} in={usage.get('input_tokens')} "
                    f"passages={len(ans.passages)}")
        except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError) as e:
            time.sleep(10 * (attempt + 1)); err = e
        except Exception as e:  # noqa: BLE001
            return f"{key} ERR {type(e).__name__}: {str(e)[:300]}"
    return f"{key} gave up {err}"


keys = [k for k in sorted(json.load(open(ROOT / "pages.json"))) if (not ONLY or k in ONLY) and (ROOT / "pages" / f"{k}.json").exists()]
with ThreadPoolExecutor(40) as ex:
    for line in ex.map(run, keys):
        print(line, flush=True)
print("POINT DONE")
