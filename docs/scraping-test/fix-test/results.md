# Fix test: other-product text and other-product photos

Date: 2026-09-30. Follows `docs/scraping-test/results-100.md`. Same 100 pages, same saved data; nothing was re-scraped except one Firecrawl refetch to get each page's raw HTML. Scripts and outputs: `docs/scraping-test/fix-test/`.

## The two problems

1. **Text about other products** reaches the ad writer. gpt-5-mini labelled sentences one at a time; 102 sentences it called "product info" were about a different product (a subagent classified all 216 of its wrong labels: 102 other product, 79 actually this product, 35 site text).
2. **Photos of other products** pass the photo filter. With only the product's name, the filter kept 550 wrong images out of 2,364 (precision 77%).

## Fix 1: label whole sections, not sentences

Firecrawl's markdown keeps the page's headings. The page is cut into sections at each heading; **one gpt-5-mini call per page** gets the product name, the page URL, the shop's own description, and every section numbered, and labels each section `this_product` / `other_product` / `not_product_info`. Only `this_product` sections (plus the JSON-LD product data) go to the ad writer.

Scored on the 70 pages both scrapers read (3,821 sentences with Claude-corrected labels) plus the Mack Weldon page:

| | Old: one sentence at a time | New: whole sections |
|---|---|---|
| Other-product sentences wrongly kept (of 102) | 102 | **11** (run 2: 12) |
| Real product sentences kept (of 426 mapped) | 415 | **385** (run 2: 362) |
| Mack Weldon sweatpant section ("Why We Love 'Em") | kept | **removed**; all 53 sections labelled right |
| Cost | $0.33 | $0.68 for 71 pages (1 cent a page), 1 call a page |

- **What still leaks (11):** FAQ sections that compare this product with its siblings (Paula's Choice "CLEAR vs SKIN PERFECTING", 6), Amazon's "Complete your purchase with" accessory box (2), a research citation, a "Get It In A Spray" cross-link, a label caution. These are single lines inside sections that are mostly about this product; the fact checker is the safety net for them.
- **What is wrongly dropped (41 of 426):** mostly debatable: variant lists under sibling products' headings, image alt-texts, a warranty blurb, Zwilling's Q&A. Fixing the prompt to keep the shop's own Q&A recovered Zwilling but cost more elsewhere; the plain version is the one to ship.
- **Stability:** two identical runs agreed on 92% of 2,909 section labels. Splitting sections finer (bold lines, horizontal rules) or batching 50 sections a call was *worse* (more context lost), so: one call, whole page.
- **Blind check by a different model:** a Claude subagent labelled 60 random sections without seeing the model's answers: agreed on 47. All 13 disagreements were "not_product_info vs this_product" (cart panels, video controls: harmless either way) or "other vs not" (both dropped). **Zero** cases of the model keeping a section Claude called other-product. 3 sections of the MOFT colour picker were dropped as other-product by the model, Claude called them this-product: a real miss.
- Size is not a problem: median page 26 K characters (6.5 K tokens), biggest 323 K (80 K tokens); gpt-5-mini reads 400 K.

## Fix 2: a reference photo, more context, and a link rule

Same 3,261 images that reached the model in the first test (after the cheap rules), on 87 pages with images, scored against the first test's per-tile review. Two new ingredients, tested separately and together:

- **More context for the model:** the shop's description and the page URL as well as the name, and a stricter answer set (`same_product` / `same_product_other_variant` / `other_product` / `no_product_visible` / `unclear`; `unclear` is dropped, the old rule kept it).
- **A reference photo:** one call per page picks the product's own photo from the shop's declared image plus the first 8 page images (it found one on 80 of 87 pages; 7 had none, all Amazon-style pages). Every other image is then judged as "is Image B the same product as Image A?".
- **A link rule, no model:** an image wrapped in a link to a different page is another product's card. Needs the raw HTML (refetched from Firecrawl; 87 of 90 pages).

| Rule for keeping a photo | Kept right | Kept wrong | Dropped wrong | Precision | Recall |
|---|---|---|---|---|---|
| Old: name only, unsure = keep | 1,814 | 550 | 19 | 77% | 99% |
| Name + description + URL, no reference | 1,733 | 333 | 100 | 84% | 95% |
| Reference photo | 1,585 | 197 | 248 | 89% | 86% |
| **Reference photo + link rule (recommended)** | **1,472** | **148** | **361** | **91%** | **80%** |

- **The review it is scored against was lenient on sibling products**, so these numbers are a floor. A subagent re-viewed 80 random disagreements by eye (40 "kept wrong", 40 "dropped wrong") without seeing either verdict: of the 40 "kept wrong", 27 were in fact the product (11 other product, 2 no product); of the 40 "dropped wrong", 22 were the product (7 other, 9 no product, 2 unclear). Scaled up, the recommended rule keeps about 48 wrong images out of 1,620 (**~97% right**) and loses about 200 real ones out of ~1,670 (**~88% kept**).
- **Per page:** the old filter kept at least one wrong photo on 63 of 87 pages; the new rule on 21. Median photos kept per page: 9 (old: 17). One page ends with no photos (Buck Mason: every image is wrapped in a link to a colour variant's page).
- **Where it still fails:** MOFT (a catalogue page with 592 images: 75 wrong keeps, most of the 361 "wrong drops"), Barbour (34 keeps the old review called sibling jackets and the model calls the same Bedale jacket; needs a human look), Liquid I.V. other flavours (8). Pages with no reference (Amazon) rely on the text-only call: 92% precision there, up from 69%.
- **Cost and time:** 0.1 cents an image (1,530 tokens in, 330 out), $2.88 for the 2,750 reference calls; 15–30 s a call, so 10 in flight gives 1–2 minutes for a typical page.


## How to implement

Three tickets, in this order. Each one is scored against the saved test data before it ships (the scorers are in `fix-test/scripts/`), so a regression shows up as a number, not as a bad ad.

### Ticket 1: read the page through Firecrawl (`backend/jobs/page.py`)

- `download()` gets a Firecrawl path: `POST /v2/scrape` with `formats: ["markdown", "rawHtml", "links"]`, timeout 5 minutes, 3 tries on 429. Keep the plain HTTP download as the fallback when Firecrawl times out or errors (3 of 100 pages), and keep `_check_where_it_points` (the private-address check) on the URL either way.
- `Download` grows two fields: `markdown` (Firecrawl's) and `html` (Firecrawl's rawHtml, or our own HTML on the fallback path). `parse()` keeps working from `html`, so JSON-LD, og:image and the full visible text still come out as today.
- The page's text for the models becomes the markdown when we have it (headings survive), else today's visible text.
- Store `FIRECRAWL_API_KEY` in settings like `OPENAI_API_KEY`; a missing key means "fallback path only".

### Ticket 2: keep only this product's text (`backend/jobs/page.py` + `work.py`)

- `sections(markdown)`: cut at every markdown heading (`#`–`######`); text before the first heading is "(no heading)"; a section over 3,000 characters is split at paragraph breaks. No finer splitting (it tested worse).
- One model call per page, gpt-5-mini, whole page in one go (batching tested worse): input is product name (from JSON-LD `name` or the page title), page URL, JSON-LD/og description, and every section as `[n] [heading]\ntext` (long sections shown as first 1,800 + last 700 characters). Output is a Pydantic list of `{s, label, why}`; a validator fails the answer if any section number is missing, and the call is retried for the missing ones. The prompt is in `fix-test/scripts/sections.py` (`INSTRUCTIONS`).
- `Job` gets `page_text_full` (everything, for debugging and the "what the page says" reply) next to `page_text`, which becomes the cleaned text: the `this_product` sections in page order, then the JSON-LD product data under the existing `DECLARED_DATA_HEADING`. Nothing downstream changes: planner, writer, fact checker and shortener already read `job.page_text`.
- Guard: if fewer than 200 characters survive, fall back to the full text and log it, so a mislabelled page is never an empty page.
- Test against `fix-test/outputs/text_truth.json` and `old-wrong-216-classified.json`: other-product sentences kept must stay ≤ 15 of 102; real product sentences kept ≥ 370 of 426.

### Ticket 3: pick this product's photos (`backend/jobs/page.py` + `work.py`)

1. **Candidates** from the HTML: every `<img>`/`<source>` (`src`, `srcset`, `data-src`), plus JSON-LD `Product.image` and `og:image` as today. For each candidate record its alt text, the nearest enclosing `<a href>`, and its order in the page.
2. **Cheap rules, no model** (tested, 95% of drops right): data URIs, SVGs, icon/logo/badge/payment names, declared width under 100 px, upgrade Shopify/Amazon size suffixes to the full-size URL, then after download: short side under 200 px or long side under 300 px, aspect over 5:1, smaller copies of the same picture. The copy rule must use the whole path minus the size suffix, not the name up to the first dot (the bug that lost 167 photos).
3. **Link rule, no model:** drop any candidate whose enclosing link leads to a different page (not an image file, not `#`, not this page's path or product id, e.g. Amazon's `/dp/<asin>`). Tested: it removed 462 of the review's wrong keeps and 131 of its "right" ones, but a subagent looking at 40 of those 131 found 29 were other products too (Nalgene 16 oz on the 32 oz page, other Stanley tumblers).
4. **Reference photo, one model call per page:** show gpt-5-mini the JSON-LD/og photo (if it survived the rules) plus the first 8 surviving images in page order, with the product name and description; it picks the one that best shows the product, or none. Tested: picked a real photo of the product on 80 of 87 pages; the shop's declared photo was the pick on 31.
5. **One model call per remaining image**, gpt-5-mini, `detail: low`, image resized to 768 px: product name, variant if the URL or JSON-LD gives one, description, page URL, the reference as "Image A" and the candidate as "Image B"; answer `same_product` / `same_product_other_variant` / `other_product` / `no_product_visible` / `unclear` plus one sentence. Keep `same_product`, and `same_product_other_variant` only when the page does not pin a variant. Drop `unclear` (the old "unsure = keep" rule let 231 no-product shots through). No reference on the page: same call without Image A (tested: 92% precision on those pages, up from 69%). Prompt in `fix-test/scripts/photo_ai.py`.
6. Order: reference first, then page order; cap stays at `MAX_PHOTOS` until the user decides otherwise. Up to 10 calls in flight per page; about 1–2 minutes and 3–5 cents for a typical page (36 images after the rules), 0.1 cents per image.
7. Store each candidate's decision and reason with the job (a small `PhotoDecision` table or a JSON field), so the chat's "Skipped N photos" line and any later check can show why.
8. Test against `fix-test/outputs/photo_truth.json` with `score_photo.py`: wrong keeps ≤ 150 of the 3,261 AI-stage images, real photos kept ≥ 1,450.

Not fixed by any of this: sibling products that look identical (Barbour Bedale vs Ashby), catalogue-like pages (MOFT: 592 images, 75 wrong keeps left), and Amazon gallery images that live in a script rather than `<img>` tags (take Firecrawl's `images` list as extra candidates there; they cannot use the link rule).


## How the checks were done

- Text ground truth: the 3,821 sentence labels from the first test, each already checked by Claude; plus a subagent's 3-way classification of the 216 wrong labels; plus a blind 60-section relabel by a subagent.
- Photo ground truth: the 6,281 tile verdicts from the first test (Claude-reviewed). That review was lenient on sibling products (it called a 16 oz bottle "product" on the 32 oz page), so a subagent re-viewed 40 disputed images by eye; of 40 images the filter kept against the review, 27 were the product; of 40 it dropped against the review, 22 were. So the two reviewers disagree often, and the user's own spot-check (`fix-test/user-check-photos.md`, 20 images, blind) is what settles which one to trust.
- Costs: OpenAI about $8 (photos $5.70 for two versions on 3,261 images; text $2.10 for three runs); Firecrawl 110 credits for the raw HTML.
- Everything the model kept or dropped is in the JSON outputs, so a person can check any of it. The user's blind spot-check files are `fix-test/user-check-photos.md` and `fix-test/user-check-text.md`.
