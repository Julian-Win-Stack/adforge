"""Step 1 + step 2 text test: sections.py, but the product at the top is the Firecrawl `product`
record (title, brand, category, url, variant names, description) instead of the guessed name.
Usage: python sections_rec.py <out.json>   (input: /tmp/st/v2/text_input.json, pages with a record)"""
import json, sys, time
from concurrent.futures import ThreadPoolExecutor
import openai
import sections
from sections import INSTRUCTIONS, Labels, split, render
from photo_rec import load_record, record_text

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/st/v2/sections-rec.json"
MODEL = "gpt-5-mini"


def ask(client, rec, secs):
    text = "Official product record of the product sold on this page:\n" + record_text(rec) + "\n\nSections:\n\n" + "\n\n".join(render(s) for s in secs)
    for attempt in range(4):
        try:
            raw = client.responses.with_raw_response.parse(
                model=MODEL, instructions=INSTRUCTIONS, input=text, text_format=Labels, max_output_tokens=40_000)
            usage = json.loads(raw.text).get("usage") or {}
            out = raw.parse().output_parsed
            got = {l.s: {"label": l.label, "why": l.why} for l in (out.labels if out else [])
                   if l.label in ("this_product", "other_product", "not_product_info")}
            return got, {"in": usage.get("input_tokens", 0), "out": usage.get("output_tokens", 0)}
        except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError):
            time.sleep(5 * (attempt + 1))
        except Exception as e:  # noqa: BLE001
            return {}, {"in": 0, "out": 0, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    return {}, {"in": 0, "out": 0, "error": "gave up"}


def run(item):
    key, page = item
    t0 = time.time()
    client = openai.OpenAI()
    rec = load_record(key)
    secs = split(page["markdown"])
    got, u = ask(client, rec, secs)
    usage = [u]
    left = [s for s in secs if s["s"] not in got]
    if left:
        more, u2 = ask(client, rec, left); got.update(more); usage.append(u2)
    for s in secs:
        s.update(got.get(s["s"], {"label": "UNANSWERED", "why": ""}))
    return key, {"product": rec.get("title"), "sections": secs, "calls": len(usage),
                 "unanswered": sum(s["label"] == "UNANSWERED" for s in secs),
                 "in": sum(x["in"] for x in usage), "out": sum(x["out"] for x in usage),
                 "errors": [x["error"] for x in usage if "error" in x], "secs": round(time.time() - t0, 1)}


if __name__ == "__main__":
    pages = {k: v for k, v in json.load(open("/tmp/st/v2/text_input.json")).items() if load_record(k)}
    try:  # resume: redo only pages with errors or unanswered sections
        results = {k: r for k, r in json.load(open(OUT)).items() if not r["errors"] and not r["unanswered"]}
    except FileNotFoundError:
        results = {}
    t0 = time.time()
    with ThreadPoolExecutor(6) as ex:
        for key, r in ex.map(run, sorted(i for i in pages.items() if i[0] not in results)):
            results[key] = r
            print(key, len(r["sections"]), "sections", r["secs"], "s", r["errors"], flush=True)
            json.dump(results, open(OUT, "w"))
    print(f"SECTIONS DONE {time.time() - t0:.0f}s")
