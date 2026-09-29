"""Job B step 4: sentences in Firecrawl's markdown that the current scraper's text lacks.

Usage: python missing.py <firecrawl.json> <current.json> <missing.json>
Only pages where BOTH scrapers got the page (status ok) are compared.
"""
import json, re, sys
from pathlib import Path


def normal(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-").replace("\u00a0", " ")
    return " ".join(text.split()).casefold()


def markdown_to_text(md: str) -> str:
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", md)            # images
    md = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)          # links -> text
    md = re.sub(r"<[^>]+>", " ", md)                          # html tags
    md = re.sub(r"^\s{0,3}#{1,6}\s*", "", md, flags=re.M)     # headings
    md = re.sub(r"^\s*[-*+]\s+|^\s*\d+\.\s+", "", md, flags=re.M)  # bullets
    md = re.sub(r"^\s*\|?[\s:|-]+\|?\s*$", "", md, flags=re.M)  # table rules
    md = md.replace("|", "\n")
    md = re.sub(r"[*_`~]{1,3}", "", md)                       # emphasis
    md = re.sub(r"\\([^\w\s])", r"\1", md)                    # escapes like \, \* \-
    md = re.sub(r"\\\s*$", "", md, flags=re.M)                # trailing hard-break backslash
    return md


def sentences(text: str) -> list[str]:
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", line):
            s = s.strip(" \t-•·")
            if len(s.split()) >= 4 and re.search(r"[a-zA-Z]{3}", s):
                out.append(s)
    seen, uniq = set(), []
    for s in out:
        k = normal(s)
        if k not in seen:
            seen.add(k); uniq.append(s)
    return uniq


def main() -> None:
    fc = json.loads(Path(sys.argv[1]).read_text())
    cur = json.loads(Path(sys.argv[2]).read_text())
    result = {}
    for key, f in fc.items():
        c = cur.get(key)
        if not c or f.get("status") != "ok" or c.get("status") != "ok":
            result[key] = {"compared": False, "why": f"firecrawl={f.get('status')}, current={c.get('status') if c else None}"}
            continue
        sents = sentences(markdown_to_text(f["markdown"]))
        have = normal(c["text"])
        have_bare = re.sub(r"[^\w\s]", "", have)
        have_words = have_bare.split()
        have_grams = {tuple(have_words[i:i + 3]) for i in range(len(have_words) - 2)}
        missing = []
        for s in sents:
            n = normal(s)
            bare = re.sub(r"[^\w\s]", "", n)
            if n in have or bare in have_bare:
                continue
            words = bare.split()
            grams = [tuple(words[i:i + 3]) for i in range(len(words) - 2)]
            # Nearly all of its word triples are in the current text: it's there, just joined
            # differently.
            if grams and sum(g in have_grams for g in grams) >= 0.9 * len(grams):
                continue
            missing.append(s)
        result[key] = {"compared": True, "firecrawl_sentences": len(sents), "missing": [{"n": i + 1, "text": s} for i, s in enumerate(missing)]}
    Path(sys.argv[3]).write_text(json.dumps(result, indent=1, ensure_ascii=False))
    for k, r in result.items():
        print(k, "-", (f"{len(r['missing'])}/{r['firecrawl_sentences']} missing" if r["compared"] else "skipped: " + r["why"]))


if __name__ == "__main__":
    main()
