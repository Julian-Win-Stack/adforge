# Copy test: copy the product's sentences word for word from the big Firecrawl text

Date: 2026-10-02. Follows `docs/scraping-test/fix-test/results.md`. Same 70 pages, same saved Firecrawl markdown (`/tmp/st/v2/text_input.json`, scraped 2026-09-28), same answer key (`fix-test/outputs/text_truth.json`: 3,821 sentences, 437 real product info, 102 about other products). No new Firecrawl calls. Scripts: `scripts/copy_text.py`, `scripts/score_copy.py`. Outputs: `outputs/<model>/<key>.json`, scores in `outputs/score.txt`.

## What was tested

- **Copy:** one model call a page. Input: product name, page link, the shop's own description, and the whole page text (images removed, links shortened to their path). The model copies, word for word, every passage about this product and nothing about other products. Prompt: `INSTRUCTIONS` in `scripts/copy_text.py`.
- **Matching to the page:** each copied sentence is looked up in the page text. Found as is: kept. Not found, but the closest page sentence is at least 85% the same: the page's own sentence is kept instead (fixes small spelling or grammar changes). Otherwise dropped.
- **Compared with:** the fix test's section filter (gpt-5-mini labels whole sections), both saved runs, re-scored with the same ruler.
- Three models for copy: gpt-5-mini, gpt-6-luna, gpt-5.6-sol.

## Results (70 pages)

| Method | Real product sentences kept (of 437) | Other-product sentences leaked (of 102) | Other noise kept (of 3,282) |
|---|---|---|---|
| Section filter, gpt-5-mini, run 1 | **395 (90%)** | 12 | 1,098 |
| Section filter, gpt-5-mini, run 2 | 371 (85%) | 13 | 1,009 |
| Copy, gpt-5-mini | 338 (77%) | 2 | 147 |
| Copy, gpt-6-luna | 324 (74%) | **0** | 86 |
| Copy, gpt-5.6-sol | 311 (71%) | **0** | 87 |

- **Leaks almost gone.** Copy with luna or sol leaked nothing; gpt-5-mini leaked 2 (a research citation on Cymbiotika, "Get It In A Spray" on Graza). The section filter's leaks (Paula's Choice CLEAR comparison FAQ, Amazon "complete your purchase" box, other Stanley tumbler) did not get through any copy run.
- **But copy keeps less real text: 71–77% against 90%.** Where it lost most (sol):
  - Life Extension (25): the brand's own answers in the shoppers' Q&A. The prompt said to leave out shoppers' Q&A; the answer key counts the brand's answers as product info. A prompt choice, fixable.
  - Tower 28 (13): image alt texts ("Before and after comparison of skin…"). Images were removed from the input, so the model never saw them. An input choice, fixable.
  - Brooklinen (17): the product's FAQ sits under a "Shop the Collection" heading; the model skipped it. A real miss.
  - Cymbiotika (13): research claims under "Got questions? We have answers!". A real miss.
  - The two fixable causes are 38 of sol's 126 misses; with them, sol would be at about 80%.
- **The page-matching step barely mattered.** Near-match replacements: gpt-5-mini 18, luna 2, sol 0. Dropped copies: 4–6 per model. About half are formatting bits (`\n\nLemon Lime`, PDF file names); the rest are real sentences not written that way in the page text, most likely taken from the shop's description given in the prompt (Luxe Sateen's award line, Carhartt's meta description) or written with HTML codes (`&rsquo;`). The 85% match threshold may be too strict for sentences like these; not tuned.
- **Smarter model did not keep more.** sol kept the least real text; the cheaper models kept slightly more and leaked slightly more.

## Cost and time (70 pages)

| Model | Tokens in / out | Cost | Seconds per page (median / max) |
|---|---|---|---|
| gpt-5-mini | 695 K / 471 K | $1.12 | 48 / 101 |
| gpt-6-luna | 695 K / 163 K | price not in `backend/gateway/catalog.py` | 23 / 120 |
| gpt-5.6-sol | 695 K / 157 K | $5.93 (8.5 cents a page) | 25 / 60 |

## FAQ answers

On Liquid I.V., the Firecrawl markdown has the FAQ questions but not the answers: the answer boxes are closed and only open on click. The answers are in the page's raw HTML, inside its embedded page data. Opening the boxes before the scrape (as `text-test/scripts/open.js` does) would put them in the text. Not counted across the 70 pages. The user's call (2026-10-02): FAQ questions don't matter; answers are a bonus, not required.

## Round 2 (same day): fixed copy prompt, and a "remove" version

- **"Useful for an ad" labels:** a Claude subagent, blind to every method's output, relabelled the 437 product-info sentences: 354 useful, 70 not useful (image alt texts, bare colour/pattern lists, "Was this answer helpful", headings with no content), 13 unclear (`outputs/useful_437.json`, `scripts/label_useful_437.py`). Only "useful" is counted in the first column below.
- **Copy v2** (`PROMPT=2 python copy_text.py <model>`): the prompt now keeps the shop's or brand's answers in Q&A, and says headings can mislead.
- **Remove** (`scripts/remove_text.py`): starts from the section filter's kept text (gpt-5-mini run 1), numbers every sentence under its heading, and asks the model which sentences are about other products. Everything not named is kept.

| Method | Useful product sentences kept (of 354) | All real product sentences kept (of 437) | Other-product sentences leaked (of 102) | Other noise kept (of 3,282) |
|---|---|---|---|---|
| Section filter, gpt-5-mini, run 1 | **332 (94%)** | 395 (90%) | 12 | 1,098 |
| Section filter, gpt-5-mini, run 2 | 311 (88%) | 371 (85%) | 13 | 1,009 |
| Copy, gpt-5-mini | 298 (84%) | 338 (77%) | 2 | 147 |
| Copy, gpt-6-luna | 289 (82%) | 324 (74%) | 0 | 86 |
| Copy, gpt-5.6-sol | 288 (81%) | 311 (71%) | 0 | 87 |
| Copy v2, gpt-6-luna | 290 (82%) | 322 (74%) | 0 | 81 |
| Copy v2, gpt-5.6-sol | 304 (86%) | 339 (78%) | 0 | 98 |
| Section filter + remove, gpt-6-luna | 317 (90%) | 363 (83%) | 2 | 1,008 |
| **Section filter + remove, gpt-5.6-sol** | **319 (90%)** | 374 (86%) | **1** | 998 |

- **Remove with sol: 90% of useful text, 1 leak** (a research citation on Cymbiotika). It took out every Paula's Choice CLEAR/RESIST comparison line, Amazon's "complete your purchase" items and the other Stanley tumbler.
- **What remove took that the section filter kept (13 useful sentences, sol):** all mention another product: "a bit warmer than Classic" (Brooklinen), "wash with our Essential Detergent", Our Place bundles including the Perfect Pot, Life Extension answers about taking it with Neuro-Mag or vitamin K2, Cotopaxi's waistbelt extenders. Most are borderline: about this product, but naming another.
- **Copy v2 helped sol (81% → 86%) but not luna.** Copy still leaks nothing, but misses more useful text than remove.
- **Noise:** remove keeps the section filter's navigation and cart text (about 1,000 sentences); copy keeps about 90. Not about other products, so harmless to the ad's facts, but it is more for the writer to read.
- **Cost (70 pages):** remove with sol $2.12 (3 cents a page, median 10 s) on top of the section filter's 1 cent a page: 2 calls a page. Copy v2 with sol $5.95 (8.5 cents a page). gpt-6-luna tokens: remove 218 K in / 80 K out; its price isn't in `catalog.py`, and it hit the 200 K tokens-a-minute limit at 30 pages in parallel.

## What this does not settle

- One run per model. The section filter's two runs differ by 24 sentences, so differences of that size between copy models may be noise.
- The answer key only covers sentences Firecrawl had that the old scraper lacked (the 2026-09-28 test). Text missing from Firecrawl's markdown (closed FAQ answers) is not in it.
- The remove step was tested only on top of section filter run 1. Run 2 kept 21 fewer useful sentences, so remove's result depends on that first step's run-to-run swing.
- The useful/not-useful labels are one judge's; the subagent listed its close calls (FAQ question headings, size options, Cymbiotika's ingredient background text).

## Why copy v2 (gpt-5.6-sol) missed 50 useful sentences

Checked where each missed sentence sits in the page text and what the model copied around it (`missed-useful-copy-sol-v2.md`).

| Group | Sentences | Why |
|---|---|---|
| Answer key wrong: not this product's text | 14 | Other products' cards (Nécessaire sizes, MOFT Trackable Wallet Stand table, Our Place bundles and Dual Handle pan), review title tags and review chips (No Pong, Our Place), a menu banner (Vuori), the Pro Max case (Rokform) |
| FAQ / Q&A answers | 16 | Life Extension's brand answers sit inside the reviews block ("Asked by … Verified Reply"); copied 0 of them even with the v2 prompt. Zwilling's Q&A also skipped |
| Clinician write-ups (Cymbiotika) | 13 | Shown as "Dr. …, MD · Verified clinician"; read as testimonials |
| Other | 7 | Brooklinen fitted-sheet fact kept in another sentence (2); care tip naming the detergent (1); product name lines (Ulta, Zwilling: 2); Satechi's site-wide "2 Year Warranty" banner (1); Momentous "Creatine – 90 Servings" card (1, unsure) |

The user's call (2026-10-02): FAQ doesn't matter, and the remaining losses (mainly Cymbiotika's clinician write-ups) are ignored for now.

## Round 3: cheaper models on copy v2 (same 70 pages)

Prices from OpenAI's pricing page (2026-10-02), per million tokens in / out: gpt-5.4-mini $0.75 / $4.50, gpt-5.6-terra $2 / $12, gpt-6-sol $2 / $10, gpt-6-luna $0.10 / $0.50. Scores: `outputs/score-v2-models.txt`. All runs sent every page at once (`WORKERS=71`).

| Model (copy v2) | Useful kept (of 354) | Other-product leaked (of 102) | Cost (70 pages) | Cents a page | Seconds a page (median / max) |
|---|---|---|---|---|---|
| gpt-5.6-sol | **304 (86%)** | 0 | $5.95 | 8.5 | 26 / 103 |
| gpt-5.6-terra | 294 (83%) | 0 | $2.81 | 4.0 | 15 / 44 |
| gpt-6-sol | 290 (82%) | 0 | $2.60 | 3.7 | 14 / 49 |
| gpt-6-luna | 290 (82%) | 0 | **$0.16** | **0.2** | 27 / 240 |
| gpt-5.4-mini | 281 (79%) | 7 | $0.95 | 1.4 | 12.5 / 174 |

- gpt-5.4-mini's leaks: 6 lines of Paula's Choice's CLEAR comparison, 1 research citation on Cymbiotika.
- Every other model leaked nothing. The spread between them (290–304) is within the section filter's own run-to-run swing (21 useful sentences), so with one run each, none is proven better than the others.
- gpt-6-luna costs about 40 times less than gpt-5.6-sol. Its slowest page (240 s) came from its 200 K tokens-a-minute limit with 70 pages sent at once.

## Round 4: gpt-6-luna with one more prompt line (copy v3), run twice

`PROMPT=3` adds to v2: "The shop's or brand's own text can also sit after or between customer reviews: a brand story, a "why it's better" block, the shop's answers to questions. Judge each passage by who wrote it: copy what the shop or brand wrote about this product, leave out what shoppers wrote." Two runs (`outputs/gpt-6-luna-v3`, `outputs/gpt-6-luna-v3-b`), 8 pages at a time each; 5 big Amazon pages hit the 200 K tokens-a-minute limit and were re-run one at a time. Scores: `outputs/score-luna-v3.txt`.

| Method | Useful kept (of 354) | Other-product leaked (of 102) | Cost (70 pages) |
|---|---|---|---|
| gpt-5.6-sol, v2 | 304 (86%) | 0 | $5.95 |
| gpt-6-luna, v2 | 290 (82%) | 0 | $0.16 |
| **gpt-6-luna, v3, run 1** | **307 (87%)** | **0** | $0.16 |
| **gpt-6-luna, v3, run 2** | **309 (87%)** | **0** | $0.16 |

- Graza's brand story after the reviews: luna v2 kept 7 of 16 useful sentences, both v3 runs 16 of 16.
- Life Extension (brand answers in the Q&A): 11 → 16 of 25 in both runs. Cymbiotika unchanged (23 of 36; the clinician write-ups are still left out).
- The two runs differ by 2 useful sentences and both leaked nothing.
