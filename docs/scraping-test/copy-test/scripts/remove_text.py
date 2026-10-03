"""Remove test: start from the section filter's kept text (fix test, gpt-5-mini run 1: the sections
labelled this_product), number every sentence, and ask the model which sentences are about OTHER
products. Everything not named is kept, so the default is "keep".
Writes copy-test/outputs/remove-<model>/<key>.json. Usage: python remove_text.py <model> [keys...]"""
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import openai
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "text-test" / "scripts"))
from missing import markdown_to_text  # noqa: E402
from textnorm import sentences  # noqa: E402

MODEL = sys.argv[1]
ONLY = set(sys.argv[2:])
OUT = Path(__file__).resolve().parents[1] / "outputs" / f"remove-{MODEL}"
OUT.mkdir(parents=True, exist_ok=True)

INSTRUCTIONS = """You clean the text of one shop's product page before it is given to an advert writer. The advert is about ONE product: the one this page sells (its name, link and the shop's own description are given). The text below has already been cut down to the parts about this product, but some sentences about OTHER products are still mixed in. Find them.

Every sentence is numbered and shown under the heading it appeared beneath. Name the number of every sentence that is about a different product: another product from the same brand or collection, other models, versions, flavours, scents or sizes sold on their own pages, accessories or add-ons sold separately, "complete your purchase" or "pairs well with" items, bundles' other parts, recipes or tips built around another product, and comparisons with the brand's other products (both the other product's lines and the comparison itself).

Keep everything else: anything about this product, its own colours, shades, sizes and variants, how to use it, its claims and research, its FAQ and the shop's answers. Do not name a sentence just because it is unhelpful or off-topic; only remove sentences about other products. If no sentence is about another product, answer with an empty list."""


class Removal(BaseModel):
    n: int
    why: str


class Answer(BaseModel):
    remove: list[Removal]


def run(item) -> str:
    key, page = item
    path = OUT / f"{key}.json"
    if path.exists():
        return f"{key} cached"
    numbered, lines, n = [], [], 0
    for s in page["sections"]:
        if s.get("label") != "this_product":
            continue
        sents = sentences(markdown_to_text(s["text"]))
        if not sents:
            continue
        lines.append(f"\n[{s['heading']}]")
        for t in sents:
            n += 1; numbered.append(t); lines.append(f"{n}. {t}")
    meta = PAGES[key]
    text = (f"Product sold on this page: {meta['product']}\nPage link: {meta['url']}\n"
            f"Shop's own description: {meta.get('description') or '(none)'}\n\nSentences:\n" + "\n".join(lines))
    client = openai.OpenAI()
    t, err = time.time(), None
    for attempt in range(4):
        try:
            raw = client.responses.with_raw_response.parse(
                model=MODEL, instructions=INSTRUCTIONS, input=text, text_format=Answer, max_output_tokens=32_000)
            body = json.loads(raw.text); usage = body.get("usage") or {}
            ans = raw.parse().output_parsed
            if ans is None:
                err = f"no answer, status {body.get('status')}"; continue
            gone = {r.n for r in ans.remove if 1 <= r.n <= len(numbered)}
            json.dump({"key": key, "model": MODEL, "sentences": len(numbered),
                       "removed": [{"n": r.n, "text": numbered[r.n - 1], "why": r.why} for r in ans.remove if r.n in gone],
                       "kept": [x for i, x in enumerate(numbered, 1) if i not in gone],
                       "status": body.get("status"), "in": usage.get("input_tokens", 0), "out": usage.get("output_tokens", 0),
                       "seconds": round(time.time() - t)}, open(path, "w"), indent=1)
            return f"{key} ok {round(time.time() - t)}s sentences={len(numbered)} removed={len(gone)}"
        except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError, openai.APITimeoutError) as e:
            err = f"{type(e).__name__}: {str(e)[:200]}"; time.sleep(15 * (attempt + 1))
        except Exception as e:  # noqa: BLE001
            return f"{key} ERR {type(e).__name__}: {str(e)[:300]}"
    return f"{key} gave up {err}"


PAGES = json.load(open(str(Path(__file__).resolve().parents[1] / "inputs" / "text_input.json")))
sec = json.load(open(str(Path(__file__).resolve().parents[1] / "inputs" / "sections-mini.json")))
items = [(k, v) for k, v in sorted(sec.items()) if (not ONLY or k in ONLY) and k in PAGES]
with ThreadPoolExecutor(int(os.environ.get("WORKERS", "30"))) as ex:
    for line in ex.map(run, items):
        print(line, flush=True)
print("REMOVE DONE", MODEL)
