"""Text test, version "pieces": the screen test's call, with the page's text cut into smaller
numbered pieces (at headings, at short bold or large lines, and every ~1,200 characters).
Writes /tmp/tt/pieces-<run>/<key>.json. Usage: python point_pieces.py <run> [keys...]"""
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
ROOT = Path("/tmp/tt")
OUT = ROOT / f"pieces-{RUN}"; OUT.mkdir(exist_ok=True)
TILE_H, MAX_TILES, SECTION_CHARS = 1200, 14, 2000

INSTRUCTIONS = """You look at one shop's product page the way a shopper would, to pick out the product's own photos and the text about it for an advert. Nothing about other products may get through.

You get screenshots of the page from top to bottom, cut into parts. Every picture has a blue box with a blue label "I<n>" in its top-left corner. Every piece of text has a red label "T<n>" just above its first line: a new piece starts at each heading, at each short bold or large line, and after about 1,200 characters (marked "(continued)"). Labels only appear on what a visitor can see; some numbers are missing from the screenshots. You also get a list of the numbered pictures (alt text, and where the picture links to if it links to another page) and a list of the numbered text pieces (the piece's first line or heading, and its text, including text a visitor has to click open).

First decide which ONE product the page sells: the one with the page title, price and buy button at the top. If the page lets the shopper choose a colour, flavour, shade or size, that choice is part of the same product.

Then answer:
- gallery_images: the pictures in that product's own photo gallery (the big photo next to the title and buy button, its thumbnails, and the rest of that gallery) that show the product itself: any angle, close-up, packaging, the product in use or worn by a model. Leave out gallery pictures in which the product can't be seen (text cards, charts, skin before/after, ingredients, badges).
- more_images: other pictures anywhere else on the page that clearly show this same product.
- product_sections: the T numbers of pieces whose text describes this product: what it is, materials or ingredients, sizes, fit, dimensions, how to use, benefits, specs, what's in the box, warranty, shipping and returns for this item, its FAQ. T0 is the text before the first heading.

Judge each piece by its own text, not only by the line above it. Never include pictures or pieces about other products: "you may also like", "shop the look", "pairs well with", "complete the set", bundles and kits, the rest of a collection, other flavours, scents, models or versions sold on their own pages, accessories sold separately, even from the same brand and even if they look alike. A picture that links to another product's page is another product. Also leave out reviews, Q&A, navigation, cart, promotions and footer text. When unsure whether a picture shows exactly this product, leave it out: a missing photo costs little, a wrong one puts another product in the advert."""


class Answer(BaseModel):
    product: str
    gallery_images: list[int]
    more_images: list[int]
    product_sections: list[int]
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
    img_lines = []
    for i in marks["imgs"]:
        if not i["n"]: continue
        link = other_page(i["href"], page["url"])
        img_lines.append(f"I{i['n']}: alt=\"{i['alt'][:80]}\"" + (f" links to {link}" if link else ""))
    sec_lines = []
    for s in marks["secs"]:
        text = s["text"] if len(s["text"]) <= SECTION_CHARS else s["text"][:900] + " … " + s["text"][-300:]
        sec_lines.append(f"T{s['i']} [{s['heading']}]\n{text}")
    content = [{"type": "input_text", "text": f"Page link: {page['url']}\nPage title: {title}\n\nScreenshots follow, top to bottom."}]
    for n, (top, b64) in enumerate(parts, 1):
        content.append({"type": "input_text", "text": f"Part {n} of {len(parts)} (from {top} px down the page):"})
        content.append({"type": "input_image", "image_url": "data:image/jpeg;base64," + b64, "detail": "high"})
    content.append({"type": "input_text", "text": "Numbered pictures:\n" + "\n".join(img_lines)})
    content.append({"type": "input_text", "text": "Numbered text pieces:\n\n" + "\n\n".join(sec_lines)})
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
                    f"gallery={len(ans.gallery_images)} more={len(ans.more_images)} secs={len(ans.product_sections)}")
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
