"""Text test: split each page's Firecrawl markdown into sections under its headings, then one
gpt-5-mini call per page labels every section this_product / other_product / not_product_info.
Usage: python sections.py <model> <out.json>   (input: /tmp/st/v2/text_input.json)"""
import json, re, sys, time
from concurrent.futures import ThreadPoolExecutor
try:
    import openai
    from pydantic import BaseModel
except ImportError:  # host python, only split() is used
    openai = None
    class BaseModel:  # type: ignore
        pass

MODEL = sys.argv[1] if len(sys.argv) > 1 else "gpt-5-mini"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/st/v2/sections-out.json"
SECTION_MAX = 3000  # a section longer than this is split into chunks at paragraph breaks

INSTRUCTIONS = """You clean the text of one product page before it is given to an advert writer. The page's text is split into numbered sections, each under the heading it appeared beneath (the heading is shown in [brackets]; "(no heading)" means text before the first heading). Product pages also carry text about OTHER products: "you may also like", "shop the look", "complete the set", other products' details, the rest of a collection, related items, bundles' other parts, and the brand's other lines. Label every section:

- "this_product": the section is about the product being sold on this page: what it is, materials or ingredients, sizes, fit, dimensions, how to use, benefits, specs, what's in the box, warranty, shipping and returns for this item, its FAQ.
- "other_product": the section is mainly about one or more different products (a different item, even from the same brand or collection; other colours of a different item; accessories sold separately; "also bought"). If it mixes this product with others, choose "other_product" only if most of it is about the others.
- "not_product_info": neither: navigation, menus, cookie and login text, cart, promo banners, newsletter, customer reviews, recently viewed, footer, legal boilerplate, empty or broken fragments. (A FAQ or Q&A answered by the shop about this product is "this_product".)

Clues: a section that ends with a link or button to another product ("Shop X") is about X. A section whose links go to other product pages is about those products. Read the whole section before deciding; a sentence like "So they never sag" belongs to whatever product its section is about.
Answer for every section number."""


class Label(BaseModel):
    s: int
    label: str
    why: str


class Labels(BaseModel):
    labels: list[Label]


def split(markdown: str) -> list[dict]:
    """Sections: heading + the text under it, long ones chunked."""
    sections, heading, buf = [], "(no heading)", []
    def flush():
        text = "\n".join(buf).strip()
        if not text:
            return
        paras = re.split(r"\n\s*\n", text)
        chunk = ""
        for p in paras:
            if chunk and len(chunk) + len(p) > SECTION_MAX:
                sections.append({"heading": heading, "text": chunk.strip()})
                chunk = ""
            chunk += p + "\n\n"
        if chunk.strip():
            sections.append({"heading": heading, "text": chunk.strip()})
    for line in markdown.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        bold = re.match(r"^\*\*([^*]{3,80})\*\*:?\s*$", line.strip())  # a bold line on its own works as a heading
        if m or bold:
            flush(); buf = []
            heading = (m or bold).group(2 if m else 1).strip()
        elif re.match(r"^\s*([-*_]\s*){3,}\s*$", line):  # horizontal rule: a new block under the same heading
            flush(); buf = []
        else:
            buf.append(line)
    flush()
    for i, s in enumerate(sections, 1):
        s["s"] = i
    return sections


def render(s: dict) -> str:
    t = s["text"]
    if len(t) > 2600:
        t = t[:1800] + "\n[...]\n" + t[-700:]
    return f"[{s['s']}] [{s['heading']}]\n{t}"


def ask(client, page: dict, sections: list[dict]) -> tuple[dict, dict]:
    head = (f"Product sold on this page: {page['product']}\nPage URL: {page['url']}\n"
            f"Shop's own description: {page['description'] or '(none)'}\n\nSections:\n\n")
    text = head + "\n\n".join(render(s) for s in sections)
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
    client = openai.OpenAI()
    sections = split(page["markdown"])
    got, u = ask(client, page, sections)
    calls, usage = 1, [u]
    left = [s for s in sections if s["s"] not in got]
    if left:
        more, u2 = ask(client, page, left); got.update(more); calls += 1; usage.append(u2)
    for s in sections:
        s.update(got.get(s["s"], {"label": "UNANSWERED", "why": ""}))
    return key, {"product": page["product"], "sections": sections, "calls": calls,
                 "unanswered": sum(s["label"] == "UNANSWERED" for s in sections),
                 "in": sum(x["in"] for x in usage), "out": sum(x["out"] for x in usage),
                 "errors": [x["error"] for x in usage if "error" in x]}


if __name__ == "__main__":
    pages = json.load(open("/tmp/st/v2/text_input.json"))
    results = {}
    with ThreadPoolExecutor(6) as ex:
        for key, r in ex.map(run, sorted(pages.items())):
            results[key] = r
            print(key, len(r["sections"]), "sections", {l: sum(s["label"] == l for s in r["sections"]) for l in ("this_product", "other_product", "not_product_info", "UNANSWERED")}, r["errors"], flush=True)
            json.dump(results, open(OUT, "w"))
    print("SECTIONS DONE")
