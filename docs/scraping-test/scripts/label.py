"""Job B step 5: gpt-5-mini labels each missing sentence "product info" or "noise".
One call per page, sentences numbered; a script check confirms every number was answered,
and unanswered numbers get one follow-up call, then are marked "unlabeled".

Usage: python label.py <missing.json> <pages-meta.json> <labels.json>
"""
import json, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import openai
from pydantic import BaseModel

MODEL = "gpt-5-mini"
INSTRUCTIONS = """You are given numbered sentences that a web scraper found on a product page but a simpler scraper missed. Label each one.

- "product_info": DESCRIBES this product to a shopper: what it is, ingredients or materials, sizes, dimensions, weight, colours, specs, features, directions or how to use, benefits and claims, clinical results, warnings, what's in the box, compatibility, warranty, certifications, or an FAQ answer about this product.
- "noise": anything else, including: bare prices, discounts, stock or delivery lines ("only 3 left", "free delivery by"), quantity pickers, buy buttons and tab names ("Add to bag", "Ingredients | How to use"), navigation and menus, cookie or privacy banners, cart and checkout UI, promo banners, newsletter sign-ups, customer reviews and Q&A, review controls, anything about OTHER products (recommendations, "customers also bought", other sizes' or products' prices), footer links, legal boilerplate, site help text, template code or broken fragments.

When in doubt between the two, ask: would an ad writer need this sentence to describe the product correctly? If not, it is noise.

Answer for EVERY number given, in order. Do not skip any."""


class Label(BaseModel):
    n: int
    label: str  # product_info | noise


class Labels(BaseModel):
    labels: list[Label]


def ask(client: openai.OpenAI, product: str, items: list[dict]) -> tuple[dict[int, str], dict]:
    text = f"Product page: {product}\n\nSentences:\n" + "\n".join(f"{m['n']}. {m['text'][:400]}" for m in items)
    for attempt in range(4):
        try:
            raw = client.responses.with_raw_response.parse(
                model=MODEL, instructions=INSTRUCTIONS,
                input=text, text_format=Labels, max_output_tokens=32_000,
            )
            usage = json.loads(raw.text).get("usage") or {}
            out = raw.parse().output_parsed
            got = {l.n: l.label for l in (out.labels if out else []) if l.label in ("product_info", "noise")}
            return got, {"in": usage.get("input_tokens", 0), "out": usage.get("output_tokens", 0)}
        except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError):
            time.sleep(5 * (attempt + 1))
        except Exception as e:  # noqa: BLE001
            return {}, {"in": 0, "out": 0, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    return {}, {"in": 0, "out": 0, "error": "gave up after retries"}


def label_page(client: openai.OpenAI, key: str, product: str, missing: list[dict]) -> dict:
    if not missing:
        return {"product": product, "calls": 0, "labels": [], "unlabeled": 0, "product_info": 0, "noise": 0, "input_tokens": 0, "output_tokens": 0, "errors": []}
    got, u1 = ask(client, product, missing)
    calls, usage = 1, [u1]
    left = [m for m in missing if m["n"] not in got]
    if left:
        more, u2 = ask(client, product, left)
        got.update(more); calls += 1; usage.append(u2)
    labels = [{"n": m["n"], "text": m["text"], "label": got.get(m["n"], "unlabeled")} for m in missing]
    return {
        "product": product, "calls": calls, "labels": labels,
        "unlabeled": sum(l["label"] == "unlabeled" for l in labels),
        "product_info": sum(l["label"] == "product_info" for l in labels),
        "noise": sum(l["label"] == "noise" for l in labels),
        "input_tokens": sum(u["in"] for u in usage), "output_tokens": sum(u["out"] for u in usage),
        "errors": [u["error"] for u in usage if "error" in u],
    }


def main() -> None:
    missing = json.loads(Path(sys.argv[1]).read_text())
    meta = json.loads(Path(sys.argv[2]).read_text())
    names = {m["key"]: m["product"] for m in meta} if isinstance(meta, list) else meta
    dst = Path(sys.argv[3])
    done = json.loads(dst.read_text()) if dst.exists() else {}
    client = openai.OpenAI(max_retries=0, timeout=300)
    todo = [(k, v) for k, v in missing.items() if v.get("compared") and k not in done]
    with ThreadPoolExecutor(5) as pool:
        for k, r in zip([k for k, _ in todo], pool.map(lambda kv: label_page(client, kv[0], names.get(kv[0], kv[0]), kv[1]["missing"]), todo)):
            done[k] = r
            dst.write_text(json.dumps(done, indent=1, ensure_ascii=False))
            print(f"{k}: {len(r['labels'])} sentences, {r.get('product_info',0)} product_info, {r.get('noise',0)} noise, {r['unlabeled']} unlabeled, {r['calls']} calls", flush=True)
    bad = {k: r["unlabeled"] for k, r in done.items() if r["unlabeled"]}
    print("COVERAGE:", "every sentence labeled" if not bad else f"unlabeled by page: {bad}")


if __name__ == "__main__":
    main()
