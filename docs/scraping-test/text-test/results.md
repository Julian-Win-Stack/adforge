# Text test: pick the product's text from pieces, or copy it off the screenshot

Date: 2026-09-30. Follows `docs/scraping-test/screen-test/results.md`. 35 pages: the screen test's 15 hard pages and 20 ordinary pages from the 100-page test. Scripts and outputs: `docs/scraping-test/text-test/`.

## What was tested

Both versions use the screen test's setup: Firecrawl opens the page, scrolls it, runs `scripts/open.js` (new: opens closed accordions and tabs, skipping menus, carts and pop-ups), then `scripts/mark.js`, then takes a full-page screenshot. One gpt-5.6-sol call a page per version, each run twice.

- **Pieces** (`scripts/point_pieces.py`): the screen test's call, with the page's text cut into smaller numbered pieces. A new piece starts at each heading, at each short bold or large line on its own (a "fake heading"), and after about 1,200 characters. The model answers with piece numbers; the same call picks the photos.
- **Copy** (`scripts/point_copy.py`): the model gets only the screenshot and copies out, word for word, the passages about the product. A copied sentence is kept only if it is found in the page's own text, so a misread or made-up sentence can't get in (it dropped 0–14 sentences a page).

Every kept sentence from both versions and both runs was merged, shuffled and judged blind by one Claude judge per page (`JUDGE-BRIEF.md`, `judged/`): `this_product`, `other_product`, `not_product_info` or `unclear`. Photos (from the pieces call) were judged on the ordinary pages as in the screen test. The Chewy page sent Firecrawl to a sign-in page; both versions correctly kept nothing, so 34 pages were judged.

## Text: other-product sentences kept (34 pages)

| | Pieces run 1 | Pieces run 2 | Copy run 1 | Copy run 2 |
|---|---|---|---|---|
| Other-product sentences, hard pages (15) | 49 | 48 | **4** | **2** |
| Other-product sentences, ordinary pages (19) | 39 | 28 | **2** | **3** |
| Pages with any other-product sentence (of 34) | 24 | 24 | **5** | **5** |
| Product sentences kept | 1,061 | 1,053 | 843 | 827 |
| Noise sentences kept | 721 | 699 | 232 | 223 |

- **What pieces lets through:** FAQs comparing other models (beyerdynamic DT 700 PRO X, 13 sentences), "pair it with" and "set it with" advice naming other products (Kosas, KONG), other pack sizes (Greenies), "you may also like" and add-on rows (Brooklinen). The model keeps a whole piece when most of it is about the product, and the other product rides along.
- **What copy lets through (5–6 sentences):** a FAQ question naming Revealer Foundation (Kosas), "100% Eye Cream" (Kosas), a collar tip (Wild One), "Pair w/ food & treats" (KONG), one sentence about the older DT 770 PRO (beyerdynamic).
- The judges' strict rule counts a sentence naming another product at all as `other_product`, so some are mild (a FAQ question mentioning the foundation). Spot checks of pieces' flagged sentences found them real: comparisons, pairings and add-ons.

## Text: what copy misses

- On the hard pages, scored against the fix test's corrected sentence labels (`outputs/score-hard-text.txt`): pieces kept 102 of 117 product sentences and copy 31; neither kept any of the 71 labelled other-product sentences. Almost all of copy's loss is Paula's Choice (24 of 95 kept against 94).
- **Why:** copy only sees what is in the screenshot. Text inside tabs that stay closed (Amazon's "Item details", Paula's Choice "How to apply" and "All ingredients"), long FAQs and research lists aren't copied. `open.js` opened 0–26 elements a page but not every site's tabs.
- Judged sentences about this product kept by only one version: 599 pieces only, 403 copy only, 473 both. The numbers are rough: the two versions cut sentences differently, so the same text can count twice.

## Photos (pieces call, 19 ordinary pages)

| | Run 1 | Run 2 |
|---|---|---|
| Photos | 206 | 204 |
| Right product (incl. other colours) | 180 (87%) | 179 (88%) |
| Other product | **12** | **10** |
| No product visible | 12 | 13 |
| Pages with a wrong photo | 6 of 19 | 6 of 19 |

- Wrong photos: Sephora (4 and 2: other Rare Beauty products), Peak Design (3: a different zip-top bag, judge unsure), OUAI (2: a model holding a pale bottle), Zwilling, Macy's ("pair it with" jeans tile, judge unsure), Fellow (a cream kettle of another model).
- This is worse than the 15 hard pages in the screen test (1 wrong of 110).

## Stability (run 1 against run 2)

- Photos: 180 picked by both runs, 32 by one run only.
- Text: pieces 1,783 sentences kept by both runs, 148 by one only; copy 1,003 by both, 136 by one only.
- No run came close to the screen test's MOFT flip (158 photos against 8).

## Cost and time

- Pieces: $11.00 for 70 calls, median 14.5 cents a call (max 34), median 25 s (max 64).
- Copy: $9.03 for 70 calls, median 13.6 cents (max 23), median 26 s (max 49).
- Using both (pieces for photos, copy for text) is about 28 cents and 25–30 s a page (run in parallel), plus Firecrawl.
- Firecrawl: this plan runs only a few browsers at once. Sending 35 pages together made 17 fail with "too many at once"; 4 at a time worked, at 32–125 s a page.

## What this does not settle

- One judge per page; judges called close cases differently (other pack sizes and flavours on a page that sells them as options: Greenies counted them as other products, LMNT as the same product).
- Only the photo side of ordinary pages was judged for photos; hard-page photos come from the screen test.
- Copy's missing text inside closed tabs could be fixed by opening more tab types or by adding the hidden text some other way; not tested.

## Round 2: a better opener for closed tabs (copy version only)

The opener was rewritten (`scripts/open_round2.js`) so the copy version sees text in closed tabs. Final run: all 35 pages again (`outputs/final/`, Chewy failed to load), copy run twice; Best Buy was re-run once more after the last fix. Sentences not judged in round 1 were judged blind the same way (`JUDGE-BRIEF-OPEN.md`, `JUDGE-BRIEF-FINAL.md`, `judged-open/`, `judged-final/`; two Best Buy sentences, a shipping line and a returns link, were marked by hand). Scores: `scripts/score_open.py`, `scripts/summary_open.py`.

What the opener now does that it didn't:
- Sites that keep one section open at a time (Paula's Choice): after opening each section it leaves a static copy of its text in the page, so the next click can't take it away.
- Amazon's tabs ("Item details", "Measurements") are clicked: they are links to `javascript:void(0)`, which the old script treated as real links.
- Tab widgets (`role="tab"`), "Read more" / "Show more" buttons and plain accordions without ARIA are opened too.
- Pop-ups open at the start (Sephora's sign-in) are closed or hidden first: Sephora ignored every click while it was open. Full-screen layers a click opens (Fellow's "Zoom", Best Buy's customer photos) are hidden; a drawer with text (a full description) is copied into the page first.
- `<details>` boxes are opened directly and their header is never clicked (a click closed them again on Kosas).
- Never clicked: carousels, galleries, reviews, buy options, bundle offers ("SAVE 38%"), comparisons, Amazon's review chips, shipping and returns.

The copy call now reads up to 24 screenshot parts instead of 14 (Amazon pages are 17,000–25,000 px tall); the most used was 18.

| | Copy run 1 before | Copy run 2 before | Run 1 after | Run 2 after |
|---|---|---|---|---|
| Product sentences kept (34 pages) | 881 | 857 | **1,100** | **1,098** |
| Other-product sentences | 6 | 5 | 10 | 10 |
| Pages with any other-product sentence | 5 | 5 | 8 | 7 |
| Noise sentences | 234 | 225 | 293 | 282 |
| Hard pages: labelled product sentences kept (of 117) | 31 | – | **90** | **88** |
| Hard pages: labelled other-product sentences kept (of 71) | 0 | – | 0 | 0 |

(Round-1 counts differ slightly from the table above because more of the old sentences now have verdicts from later rounds.)

- Paula's Choice: 24 → 84 of 95 labelled product sentences.
- New other-product sentences come from newly visible text: Amazon Fire TV's "About this item" (the eero router, "we recommend Fire TV Stick 4K Max", a compatibility list), Greenies' size chart (other pack sizes), Lodge's "Miniature Skillet", Paula's Choice "10-Count Pads" and an FAQ line about AHA exfoliants. Several are close calls under the strict rule (size options, compatibility lists).
- Cost of the copy call didn't move: $9.52 for 68 calls, median 13.2 cents (max 27). The opener adds a median 2.4 s a page (max 17 s).
- Still missed: Graza (its product text is visible but the model kept 3–4 of 17 labelled sentences), Best Buy's "From the Manufacturer" (inside a frame the script can't reach), Glossier's per-ingredient "+" rows (no ARIA, class names the script doesn't recognise).

## Scripts

Round 2's opener is kept as `scripts/open_round2.js`; `scripts/open.js` is round 1's simple opener (only `<details>` boxes and `aria-expanded` buttons).
