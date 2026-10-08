"""Copy test: give the model the big Firecrawl page text (markdown) and ask it to copy out, word for
word, every passage about the product sold on the page, and nothing about other products. Each
copied sentence is then matched to the page: an exact match is kept, a near match (small spelling or
grammar changes) is replaced by the page's own sentence, anything else is dropped.
Input: copy-test/inputs/text_input.json (the fix test's saved Firecrawl markdown, 71 pages).
Writes copy-test/outputs/<model>/<key>.json. Usage: python copy_text.py <model> [keys...]"""
import difflib, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
import openai
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "text-test" / "scripts"))
from missing import markdown_to_text  # noqa: E402
from textnorm import norm, sentences  # noqa: E402

MODEL = sys.argv[1]
ONLY = set(sys.argv[2:])
PROMPT = os.environ.get("PROMPT", "1")
V2 = PROMPT in ("2", "3")  # round 2: keep the brand's answers in Q&A, don't trust headings
V3 = PROMPT == "3"  # round 4: the brand's own text can sit after or between reviews
RUN = os.environ.get("RUN", "")  # a second run of the same setup writes to "<dir>-<RUN>"
OUT = Path(__file__).resolve().parents[1] / "outputs" / (MODEL + (f"-v{PROMPT}" if PROMPT != "1" else "") + (f"-{RUN}" if RUN else ""))
OUT.mkdir(parents=True, exist_ok=True)
NEAR = 0.85  # difflib ratio on normalised text for "same sentence, small changes"

INSTRUCTIONS = """You clean the text of one shop's product page before it is given to an advert writer. The advert is about ONE product: the one this page sells (its name, link and the shop's own description are given). Product pages also carry text about OTHER products, and none of it may reach the advert.

Copy out, word for word as written on the page, every passage that describes this product: its name, what it is, materials or ingredients, sizes, fit, dimensions, how to use, benefits, claims, research and awards, specs, what's in the box, warranty, shipping and returns for this item, and its FAQ. If the page lets the shopper choose a colour, flavour, shade or size, those choices are part of this product. Text in tabs, accordions and "read more" blocks counts: it is all in the text you get. Copy exactly: do not fix, shorten, join or rephrase. One passage per paragraph or list item. Skip the price.

Do not copy text about other products, even single sentences inside a passage that is otherwise about this product: "you may also like", "shop the look", "pairs well with", "complete your purchase", "frequently bought together", bundles and kits, the rest of a collection, other flavours, scents, models or versions sold on their own pages, accessories sold separately, recipes or tips built around another product, and comparisons with the brand's other products. Also leave out reviews, shoppers' Q&A, navigation, cart, promotions, newsletter, cookie and footer text.

Both mistakes matter: leaving out real text about this product loses facts the advert needs, and copying text about another product puts the wrong product in the advert. Copy everything about this product and nothing else. Links are shown as [text](path); a passage whose link goes to another product's page is about that product."""


if V2:
    INSTRUCTIONS = INSTRUCTIONS.replace(
        "Also leave out reviews, shoppers' Q&A,",
        "Also leave out reviews, shoppers' own questions and answers (but do copy the shop's or brand's answers about this product),",
    ) + """

Headings can mislead: a product FAQ or description can sit under a heading like "Shop the Collection" or "Got questions?". Judge each passage by what it says, not by its heading."""

if V3:
    INSTRUCTIONS += """ The shop's or brand's own text can also sit after or between customer reviews: a brand story, a "why it's better" block, the shop's answers to questions. Judge each passage by who wrote it: copy what the shop or brand wrote about this product, leave out what shoppers wrote."""


class Answer(BaseModel):
    product: str
    passages: list[str]


def shorten_links(md: str) -> str:
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", md)  # images carry no text
    return re.sub(r"\[([^\]]*)\]\(([^)\s]*)[^)]*\)", lambda m: f"[{m[1]}]({urlparse(m[2]).path or m[2][:60]})", md)


def snap(copied: list[str], page_text: str) -> dict:
    """Match each copied sentence to the page. Exact = found in the page text as is; near = the
    closest page sentence is >= NEAR similar, and the page's sentence replaces the copy."""
    page_norm = norm(page_text)
    pool = sentences(page_text)
    pool_norm = [norm(s) for s in pool]
    words = [set(n.split()) for n in pool_norm]
    kept, near, dropped = [], [], []
    for s in copied:
        n = norm(s)
        if n and n in page_norm:
            kept.append(s); continue
        w = set(n.split())
        cands = sorted(range(len(pool)), key=lambda i: -len(w & words[i]))[:25]
        best, score = None, 0.0
        for i in cands:
            r = difflib.SequenceMatcher(None, n, pool_norm[i]).ratio()
            if r > score: best, score = i, r
        if best is not None and score >= NEAR:
            kept.append(pool[best]); near.append({"copied": s, "page": pool[best], "score": round(score, 3)})
        else:
            dropped.append({"copied": s, "closest": pool[best] if best is not None else None, "score": round(score, 3)})
    return {"kept": kept, "near": near, "dropped": dropped}


def run(item) -> str:
    key, page = item
    path = OUT / f"{key}.json"
    lock = OUT / f"{key}.lock"
    if path.exists() or lock.exists():
        return f"{key} cached or running elsewhere"
    lock.touch()
    md = shorten_links(page["markdown"])
    text = (f"Product sold on this page: {page['product']}\nPage link: {page['url']}\n"
            f"Shop's own description: {page.get('description') or '(none)'}\n\nPage text:\n\n{md}")
    client = openai.OpenAI()
    t, err = time.time(), None
    for attempt in range(4):
        try:
            raw = client.responses.with_raw_response.parse(
                model=MODEL, instructions=INSTRUCTIONS, input=text, text_format=Answer, max_output_tokens=64_000)
            body = json.loads(raw.text)
            usage = body.get("usage") or {}
            ans = raw.parse().output_parsed
            if ans is None:
                err = f"no answer, status {body.get('status')} {body.get('incomplete_details')}"; continue
            copied = [s for p in ans.passages for s in sentences(markdown_to_text(p))]
            res = snap(copied, markdown_to_text(page["markdown"]))
            json.dump({"key": key, "model": MODEL, "product": ans.product, "passages": ans.passages, **res,
                       "status": body.get("status"), "in": usage.get("input_tokens", 0),
                       "out": usage.get("output_tokens", 0),
                       "reasoning": (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0),
                       "seconds": round(time.time() - t)}, open(path, "w"), indent=1)
            return (f"{key} ok {round(time.time() - t)}s in={usage.get('input_tokens')} out={usage.get('output_tokens')} "
                    f"kept={len(res['kept'])} near={len(res['near'])} dropped={len(res['dropped'])}")
        except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError, openai.APITimeoutError) as e:
            err = f"{type(e).__name__}: {str(e)[:200]}"; time.sleep(15 * (attempt + 1))
        except Exception as e:  # noqa: BLE001
            return f"{key} ERR {type(e).__name__}: {str(e)[:300]}"
    json.dump({"key": key, "model": MODEL, "error": err}, open(OUT / f"{key}.error.json", "w"))
    return f"{key} gave up {err}"


pages = json.load(open(str(Path(__file__).resolve().parents[1] / "inputs" / "text_input.json")))
items = [(k, v) for k, v in sorted(pages.items()) if not ONLY or k in ONLY]
if os.environ.get("REVERSE"):
    items.reverse()
with ThreadPoolExecutor(int(os.environ.get("WORKERS", "10"))) as ex:
    for line in ex.map(run, items):
        print(line, flush=True)
print("COPY DONE", MODEL)
