# Screen test: read the page like a person (screenshot + numbered boxes)

Date: 2026-09-30. Follows `docs/scraping-test/fix-test/results.md`. 15 of the hardest pages from the 100-page test (MOFT, Barbour, Liquid I.V., Buck Mason, Mack Weldon, Glossier, Kosas, Tower 28, Graza, Paula's Choice, beyerdynamic, Nalgene, 3 Amazon). Scripts and outputs: `docs/scraping-test/screen-test/`.

## What was tested

- **Screen (new):** Firecrawl opens the page, scrolls it, and runs `scripts/mark.js`, which draws a blue `I<n>` box on every picture and a red `T<n>` label on every heading, then takes a full-page screenshot. One call to gpt-5.6-sol a page gets the screenshot (cut into parts of 1,200 px, at most 14), the list of numbered pictures (alt text, and where each links to) and the list of numbered sections (heading + text). It is given only the page link and title. It answers which pictures are the product's gallery, which other pictures show the product, and which sections describe it (`scripts/point.py`).
- **Shop (new, no model):** the shop's own list of the product's photos: Shopify's `<product link>.json`, Amazon's gallery data in the page, or the JSON-LD Product images (`scripts/shop_list.py`).
- **Tested:** the fix test's best method (reference photo + link rule), from its saved output.

Each method's first 10 photos per page were shuffled together (copies merged) and judged by 3 blind Claude judges who did not know which method picked what (`JUDGE-BRIEF.md`, `judged/`).

## Photos (top 10 per page, 15 pages)

| Method | Photos | Right product | Other product | No product visible | Pages with a wrong product |
|---|---|---|---|---|---|
| Tested (reference + link rule) | 126 | 86 (68%) | **27** | 10 | **9 of 15** |
| Shop's own list | 110 | 86 (78%) | 1 | 22 | 1 of 15 |
| **Screen** | 110 | **92 (84%)** | **1** | 16 | **1 of 15** |

"Right product" counts other colours and sizes of the same product (the planner already picks one colour from the photos).

- **Screen's one wrong photo:** a Glossier "how to use" graphic showing Boy Brow next to a different Glossier pencil.
- **Screen's "no product" photos (16):** gallery pictures without the product: before/after skin (Paula's Choice 6, Glossier), icon drawings (Fire TV), a dog without the treat. They are harmless but useless for a scene, even though the prompt asked to leave them out.
- **Shop's list:** almost never wrong, but it comes with more pictures of no product (Fire TV 8 of 10), and it has fewer than 3 photos on 3 pages (Liquid I.V., Paula's Choice, Stanley). Shopify's `.json` failed on 2 of 9 Shopify pages (Liquid I.V., Buck Mason).
- **Tested method's wrong photos:** sibling jackets (Barbour 6), other Stanley tumblers (3), other Paula's Choice products (4), MOFT stands (4), Graza Drizzle (3).
- Every page got at least 1 photo from screen; 14 of 15 got 3 or more (Liquid I.V.: 2).

## Text (13 pages that have corrected sentence labels)

Scored only on sentences still on today's page and in the fix test's output, so both methods see the same sentences (`scripts/score_text.py`).

| | Screen | Tested (sections) |
|---|---|---|
| Other-product sentences kept (of 72) | **1** | 10 |
| Real product sentences kept (of 118) | 98 | **112** |
| Noise sentences kept (of 212) | **40** | 97 |
| Mack Weldon "Why We Love 'Em" (Ace Sweatpant) | removed | removed |

- **What screen loses:** Graza's product copy has no heading of its own, so it is joined to a "5 Stars" review section and dropped with the reviews (9 sentences). Paula's Choice's "compare our 4 BHA products" FAQ is dropped (it is also where the tested method leaked other products). A second run with a prompt naming claims, research and awards (`point_v2.py`) kept the awards section but scored the same (98).
- Section splitting by headings is the weak point, not the model: text under a misleading heading goes with that heading.

## Cost and time

- Model: $2.40 for 15 pages, median 16 cents a page (max 27 cents), 21 s median (max 41 s). About 30 K tokens in, mostly the screenshot.
- Firecrawl: 1 credit a page, 32–75 s with the scrolling and the script.
- Tested method for comparison: 3–5 cents and 1–2 minutes a page.

## What this does not settle

- **Only 15 pages, chosen because they were hard.** Easy pages were not tested.
- **One run per page.** A second run (the text prompt fix) picked very different photos on MOFT (158 gallery pictures instead of 8), so how stable the picks are is not known.
- **One judge per photo.** Judges disagreed with each other in earlier tests; close calls are listed in their reports (other colour vs other product for Stanley and Barbour, kits for Glossier).
- The tested method's photos come from the pages as they were on 2026-09-28; screen and shop read them again today.
- Firecrawl lays the page out with a 100,000 px tall window while scripts run and a normal one for the screenshot; `mark.js` re-places its boxes on every layout change to cope. Pages whose layout changes after the screenshot starts could still get misplaced boxes.
