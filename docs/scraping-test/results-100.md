# Scraper test on 100 product pages: current scraper vs Firecrawl

Date: 2026-09-28. All data and scripts are in `docs/scraping-test/` (uncommitted; this summary is the only committed file).

## What was tested

- **100 live product pages**: 40 small brand stores (mostly Shopify), 30 big retailers (Target, Walmart, Sephora, Best Buy, Home Depot, Chewy, and others), 10 Amazon, 20 others (WooCommerce, BigCommerce, custom). Categories: beauty, supplements, electronics, home, pets, clothing. Every URL was confirmed live before the test.
- **Current scraper** (`backend/jobs/page.py`): a plain HTTP download, no JavaScript. Text is the page's visible text plus its JSON-LD product data. Photos are only the JSON-LD product images and the og:image, max 10.
- **Firecrawl** (`/v2/scrape`, formats markdown + images + rawHtml, 1 credit a page): a hosted headless browser.

## 1. Did each scraper get the product page at all?

| | Current scraper | Firecrawl |
|---|---|---|
| Got the product page | **73 / 100** | **94 / 100** |
| Blocked (HTTP 403 or 429) | 15 | 0 |
| No HTTP answer (connection hung; bot protection) | 4 | 0 |
| Empty or thin page (JavaScript-only shell, under 2,500 chars) | 8 | 3 |
| Timed out after 5 minutes | 0 | 3 |

- **Big retailers are where the current scraper fails: 8 of 30 pages.** Sephora, Home Depot, Petco, Macy's, CVS, iHerb answered 403; Chewy, Marine Layer, Everlane answered 429; Best Buy and Walgreens never answered; Nordstrom returned an empty page; Target returned a 1–2 KB JavaScript shell with no product text. Small brands: 36 of 40. Amazon: 10 of 10. Others: 19 of 20.
- **Firecrawl got 26 of 30 big retailers.** Its six failures: three timeouts (Sundays for Dogs, Wild Earth, Walmart onn), Target's Goodfellow tee (JS shell for both), Nordstrom's Zella leggings (Firecrawl returned the "Moved Permanently" redirect page instead of following it), Macy's Levi's 501 (a 1 KB shell).
- **No URL was dead.** Every page that failed for either scraper was opened in a real browser and showed a full product page (`job-b/browser-checks.md`).

## 2. Product text the current scraper missed

Compared on the 70 pages both scrapers got. A "missing sentence" is a sentence in Firecrawl's markdown that is not in the current scraper's text (after normalising quotes, spaces and case; a sentence counts as present if 90% of its word triples appear).

- Firecrawl had **3,821 sentences** the current scraper lacked. gpt-5-mini labelled each one product info or noise (one call per page, every sentence numbered, a script confirmed every number was answered). Claude then checked every label.
- After Claude's corrections, **437 sentences on 39 of the 70 pages are real product information** the current scraper never saw. 16 pages missed 5 or more; 6 pages missed 20 or more.
- The big misses are pages that load their description with JavaScript: Paula's Choice 2% BHA (120 sentences; the current scraper got a 5 KB shell with only the title and JSON-LD), Brooklinen Luxe sheets (40; "Includes 1 fitted sheet, 1 flat sheet, and 2 pillowcases", thread count, care), Cymbiotika Vitamin C (36), Cotopaxi Allpa 35L (35), Life Extension Two-Per-Day (33), Thorne Basic Nutrients (27; "Keep your nutrition up… a complete multi…"). Amazon: only 2 of 10 pages missed anything.
- **The labeler over-counts.** Of the 642 sentences gpt-5-mini called product info, 216 (34%) were wrong, and almost all of those were text about a *different* product on the same page: add-on accessories on the Fire TV page, other flavours' recipes on Liquid I.V., sibling headphones on beyerdynamic, "you may also like" rails. Its noise labels were right 99.7% of the time.
- **Firecrawl does not remove cross-sell text either.** The Mack Weldon sweatshirt page (the ad #04 problem in `docs/runs/first-run.md`) was run through Firecrawl: its markdown contains the whole Ace Sweatpant section ("Micro-Brushed French terry… Back-Ribbed Ankle Cuffs… Shop Ace Sweatpant"), plus "Shop the look", the whole collection, and a login form. Markdown keeps the headings, so section boundaries survive, but nothing says which product a section is about. That fix has to be ours, whichever scraper we use.

## 3. Photo filter (Job A)

Input: Firecrawl's full image list per page (16 to 640 images, mostly junk). Rules first: drop SVGs, data URIs, icons and logos by file name, images listed under 100 px, images under 200 px short side or 300 px long side, banner shapes, and smaller copies of the same picture. Then **one gpt-5-mini call per remaining image** with the product name: product / not_product / unsure, where unsure is kept.

- **Pilot (8 pages, hand-made answer key of 57 gallery photos): 55 of 57 gallery photos kept**, 103 extra images kept. Claude's review of all 510 tiles: 460 correct, 50 wrongly kept, 0 wrongly dropped.
- **100-page run (90 pages had images): 7,797 images in, 2,364 kept, 3,261 AI calls, $2.49.** Claude reviewed every one of the 6,281 tiles on the 296 contact sheets (1,516 drops with no downloadable file, such as SVGs, were counted, not shown).

| | Count | Right | Wrong |
|---|---|---|---|
| Kept | 2,364 | 1,814 (77%) | 550 wrongly kept |
| Dropped and shown | 3,917 | 3,730 (95%) | 187 wrongly dropped |

- **Wrongly kept (550):** almost all are *other products from the same store*: the MOFT page kept 161 tripods, cases and lanyards; Barbour kept 38 sibling jackets; Glossier kept other products' models; Tower 28 kept the purple SunnyDays bottle as the orange SOS spray. 231 of the 550 were "unsure" verdicts (lifestyle shots with no product) that the keep-unsure rule let through; the AI's "product" verdicts alone were right 84% of the time.
- **Wrongly dropped (187):** 167 come from one bug in the duplicate rule, not from the AI. `photo_key` cuts the file name at the first dot and ignores the folder, so Buck Mason's `BM11030.102_OLIVE.jpg` and `BM11030.102_AMBERWOOD.jpg` count as one picture, and review photos all named `original.jpg` collapse into one. Kosas lost 93 shade pictures this way, Samsung 24. Only 19 drops were AI mistakes.
- **Firecrawl's image lists have broken URLs:** 724 of 7,797 could not be downloaded (Amazon `/dp/…_QL54_.jpg` fragments, iHerb page URLs). Two stores block image downloads from a script (Oak Furniture King 503, VetriScience 403), so no photos were kept there.

## 4. How the checks were done

- Claude subagents, about 10 pages each, looked at every contact sheet tile and every sentence label. A script confirmed every tile and every sentence got a verdict.
- The user checked 20 random photo disagreements (`job-a/photo-disagreements.md`) and 20 random labels (`job-b/label-sample.md`): **[USER MARKS: to be filled in]**.
- Costs: Firecrawl about 140 credits (100 pages plus retries and a lost first run); OpenAI $2.49 filter + $0.33 labels + $0.23 pilot.

## 5. Why not run our own headless browser instead of Firecrawl?

A headless Chrome of our own (Playwright) would fix the JavaScript-only pages but not the blocking: Sephora, Home Depot, Chewy, Best Buy and the rest block by fingerprint, IP reputation and challenge pages, not by "no JavaScript". Getting past that means residential proxies, browser fingerprint patches, and a maintenance chore every time a retailer changes its bot protection. Firecrawl already does that work (26 of 30 big retailers here) and costs 1 credit a page. It is slower (1 to 4 minutes on some pages, three timeouts) and its markdown still needs our own cleaning. Build only makes sense if the per-page cost or the timeouts become a problem.

## What this does not settle

- Photos: all pilot gallery photos were in the raw HTML our scraper already downloads. The photo win is fixable on our side; Firecrawl's image list is only a starting point and needs the filter above plus the dedupe bug fix.
- Cross-sell text (the ad #04 problem) is not fixed by either scraper.
- Text the current scraper had and Firecrawl lacked was not measured.
