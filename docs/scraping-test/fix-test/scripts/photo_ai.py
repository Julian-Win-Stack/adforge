"""Photo test on the 3,261 images that reached the AI stage in the earlier run.
Step 1 (one call a page): pick a reference photo of the product from the shop's main photo and
the first 8 page images. Step 2 (one call an image, two arms): judge each image against the
product, once with the reference photo ("ref") and once with text only ("text").
Writes /tmp/st/v2/photo-out/<key>.json. Usage: python photo_ai.py [model] [keys...]"""
import base64, io, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
import openai
from PIL import Image
from pydantic import BaseModel

MODEL = sys.argv[1] if len(sys.argv) > 1 else "gpt-5-mini"
ONLY = set(sys.argv[2:])
CACHE = Path("/tmp/st/cache/100")
OUT = Path("/tmp/st/v2/photo-out"); OUT.mkdir(parents=True, exist_ok=True)
RELATIONS = ("same_product", "same_product_other_variant", "other_product", "no_product_visible", "unclear")

PICK_INSTRUCTIONS = """You are shown numbered pictures taken from a shop's product page, plus the product's name and the shop's description. Pick the ONE picture that best shows exactly this product on its own, clearly and fully (a plain product shot is ideal; packaging with the product's name is good). Answer 0 if none of the pictures clearly shows this product. Give the number and one sentence why."""

JUDGE_INSTRUCTIONS = """You judge whether a picture from a shop's product page shows the product being sold, so that only that product's photos go into its advert. You get the product's name, its variant if known, the shop's description and the page link.
{ref}
Answer with one relation for the picture to judge:
- same_product: it shows the product sold on this page (same item: any angle, close-up, packaging, in use, on a model) and the product is clearly visible.
- same_product_other_variant: it shows the same product but in a different colour, flavour, pattern or size from the one sold.
- other_product: it shows a different product, even from the same brand, set or collection; a lookalike with a different name, label, shape or colour is a different product.
- no_product_visible: the product is not visible: scenery, a texture, ingredients, a logo, a chart, text, a person or pet without the product.
- unclear: you cannot tell.
Be strict. Give one sentence why."""
REF_TEXT = "Image A is the shop's own photo of this product: compare against it. Image B is the picture to judge."
NOREF_TEXT = "There is one picture to judge."


class Pick(BaseModel):
    pick: int
    why: str


class Verdict(BaseModel):
    relation: str
    why: str


def to_jpeg_b64(path: Path, side: int = 768) -> str:
    with Image.open(path) as im:
        im = im.convert("RGB"); im.thumbnail((side, side))
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def image_part(path: Path) -> dict:
    return {"type": "input_image", "image_url": "data:image/jpeg;base64," + to_jpeg_b64(path), "detail": "low"}


def call(client, instructions, content, model_cls):
    last = None
    for attempt in range(5):
        try:
            raw = client.responses.with_raw_response.parse(
                model=MODEL, instructions=instructions, input=[{"role": "user", "content": content}], text_format=model_cls)
            usage = json.loads(raw.text).get("usage") or {}
            return raw.parse().output_parsed, usage.get("input_tokens", 0), usage.get("output_tokens", 0), None
        except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError) as e:
            last = e; time.sleep(4 * (attempt + 1))
        except Exception as e:  # noqa: BLE001
            return None, 0, 0, f"{type(e).__name__}: {str(e)[:200]}"
    return None, 0, 0, f"gave up: {type(last).__name__}"


def product_text(meta: dict) -> str:
    return (f"Product: {meta['product']}" + (f"\nVariant: {meta['variant']}" if meta.get('variant') else "")
            + f"\nShop's description: {meta.get('description') or '(none)'}\nPage: {meta.get('url')}")


def name_of(url: str) -> str:
    return urlparse(url).path.rstrip("/").split("/")[-1].split("?")[0].casefold()


def pick_reference(client, key, meta, rows, main_urls):
    """Candidates: the shop's main photo (if it is one of the page's usable images) and the
    first 8 usable images in page order."""
    main = None
    names = {name_of(u) for u in main_urls}
    for r in rows:
        if name_of(r["url"]) in names or name_of(r.get("fetched_url", r["url"])) in names:
            main = r; break
    cands = []
    if main: cands.append(main)
    for r in sorted(rows, key=lambda r: r["n"]):
        if r not in cands and min(r["w"], r["h"]) >= 300:
            cands.append(r)
        if len(cands) >= 9: break
    if not cands:
        return {"main_n": main["n"] if main else None, "pick_n": None, "candidates": [], "why": "no candidates"}
    content = [{"type": "input_text", "text": product_text(meta) + "\n\nPictures:"}]
    for i, r in enumerate(cands, 1):
        content.append({"type": "input_text", "text": f"Picture {i}:"})
        content.append(image_part(CACHE / key / r["file"]))
    out, tin, tout, err = call(client, PICK_INSTRUCTIONS, content, Pick)
    pick = cands[out.pick - 1]["n"] if out and 1 <= out.pick <= len(cands) else None
    return {"main_n": main["n"] if main else None, "pick_n": pick, "candidates": [r["n"] for r in cands],
            "why": out.why if out else err, "in": tin, "out": tout}


def judge(client, key, meta, row, ref_row):
    res = {"n": row["n"]}
    cand = CACHE / key / row["file"]
    # text-only arm
    content = [{"type": "input_text", "text": product_text(meta)}, image_part(cand)]
    out, tin, tout, err = call(client, JUDGE_INSTRUCTIONS.format(ref=NOREF_TEXT), content, Verdict)
    res["text"] = {"relation": out.relation if out and out.relation in RELATIONS else "unclear", "why": out.why if out else err, "in": tin, "out": tout}
    # reference arm
    if ref_row is None:
        res["ref"] = None
    elif ref_row["n"] == row["n"]:
        res["ref"] = {"relation": "same_product", "why": "is the reference", "in": 0, "out": 0}
    else:
        content = [{"type": "input_text", "text": product_text(meta) + "\n\nImage A (the shop's photo of the product):"},
                   image_part(CACHE / key / ref_row["file"]), {"type": "input_text", "text": "Image B (to judge):"}, image_part(cand)]
        out, tin, tout, err = call(client, JUDGE_INSTRUCTIONS.format(ref=REF_TEXT), content, Verdict)
        res["ref"] = {"relation": out.relation if out and out.relation in RELATIONS else "unclear", "why": out.why if out else err, "in": tin, "out": tout}
    return res


def run_page(key, meta, rows, main_urls):
    path = OUT / f"{key}.json"
    if path.exists():
        return key, "cached"
    client = openai.OpenAI()
    rows = [r for r in rows if (CACHE / key / r["file"]).exists()]
    ref = pick_reference(client, key, meta, rows, main_urls)
    ref_n = ref["pick_n"] if ref["pick_n"] is not None else ref["main_n"]
    ref_row = next((r for r in rows if r["n"] == ref_n), None)
    with ThreadPoolExecutor(8) as ex:
        verdicts = list(ex.map(lambda r: judge(client, key, meta, r, ref_row), rows))
    json.dump({"key": key, "reference": ref, "ref_n": ref_n, "verdicts": verdicts}, open(path, "w"))
    return key, f"{len(rows)} images, ref={ref_n} (main={ref['main_n']}, pick={ref['pick_n']})"


if __name__ == "__main__":
    truth = json.load(open("/tmp/st/v2/photo_truth.json"))
    meta = json.load(open("/tmp/st/v2/meta.json"))
    mains = json.load(open("/tmp/st/v2/main_urls.json"))
    by_key = {}
    for r in truth:
        if r["stage"] in ("kept", "ai"):
            by_key.setdefault(r["key"], []).append(r)
    keys = sorted(k for k in by_key if not ONLY or k in ONLY)
    with ThreadPoolExecutor(8) as pool:  # 5 pages at once, 10 images each: 50 calls in flight
        for key, msg in pool.map(lambda k: run_page(k, meta[k], by_key[k], mains.get(k, [])), keys):
            print(key, msg, flush=True)
    print("PHOTO DONE")
