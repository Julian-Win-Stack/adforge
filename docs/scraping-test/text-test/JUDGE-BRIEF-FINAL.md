# Blind judging brief (text test, third round)

Folder: `/Users/phyonyanwinn/Project/ProJect/adforge/docs/scraping-test/text-test/`

Same task and rules as `JUDGE-BRIEF.md` (read it first), with these differences:

- Sentences are in `text-final/<key>.json` (ids N1, N2, ...). Judge only text; there are no photos this round.
- Write `judged-final/<key>.json`: `[{"id": "N1", "verdict": "this_product", "why": "one short phrase"}]`, one entry per sentence, in id order.
- Don't open `outputs/`, `scripts/`, `judged/`, `judged-open/`, `text/` or `text-open/`.

Reply with one line: key and the count of each verdict.
