# Photo merge test: add the product call's photos to the screenshot picks

Date: 2026-10-02. Follows `docs/scraping-test/screen-test/results.md` and `docs/scraping-test/text-test/results.md`. No new OpenAI or Firecrawl calls: it reuses saved outputs. Scripts and outputs: `docs/scraping-test/photo-merge-test/`.

## Question

If we add the photos from Firecrawl's `product` call to the photos the screenshot method picked, do we get more good photos per page, without adding photos of other products?

## What was tested

- **Screenshot picks, as they were scored before.** 15 hard pages: the screen test's "screen" method, first 10 photos a page. 19 ordinary pages: the text test's "pieces" call, run 1, every photo. Their verdicts come from the earlier blind judges.
- **Product photos.** Every image in the saved product record (`/tmp/st/v2/product/<key>.json`, `data.product.variants[].images[].url`), one per file name, the page's variant first. All were downloaded straight from the shops' image servers.
- **Rules (`scripts/build.py`):**
  - Drop small photos: short side under 200 px or long side under 300 px.
  - Drop copies of an earlier product photo: same file name, or the same picture hash as the earlier tests used (12 of 256 bits).
  - (a) **All** product photos left. (b) **Capped:** the first 10 product photos left, page's variant first, then record order.
  - Then drop product photos that copy a screenshot pick, by file name (Shopify `_800x`, `?width=`, Amazon `._AC_SL1500_.` and the extension removed) or by picture hash. What is left is "added".
- **Judging.** All 300 added photos were judged blind by one Claude judge (me), looking at each photo on numbered contact sheets (`outputs/sheets/`), with the product name, page link and the top of the page, using the screen test's brief (`../screen-test/JUDGE-BRIEF.md`). Verdicts: `judged/<key>.json`. `scripts/score.py` stops if any added photo has no verdict or two.
- **Near copies (after judging).** The hash misses some copies (other crop, other background, a text panel on top). After judging, each added photo was put next to the closest screenshot pick and the other added photos (`scripts/near_copies.py`, `outputs/near/`). 40 were the same shot as a screenshot pick or as another added photo (`outputs/near-copies.json`). They are counted in the main table and removed in the second one.

## The earlier numbers reproduce

| | Photos | Right | Other product | No product | Unclear |
|---|---|---|---|---|---|
| Screen, 15 hard pages (published: 110 / 92 / 1 / 16) | 110 | 92 | 1 | 16 | 1 |
| Pieces run 1, 19 ordinary pages (published: 206 / 180 / 12 / 12) | 206 | 180 | 12 | 12 | 2 |

Both match. The published tables leave out the 3 "unclear" photos; they count in "photos" below too.

## Where the product photos went (34 pages)

- The records hold 2,672 image links; 766 different file names.
- 347 were copies of another product photo (mostly Amazon, where every size or colour carries the same gallery).
- 35 were too small: all 23 of Sephora's (250 × 250), 9 of Lodge's, 3 others.
- 84 copied a screenshot pick (76 by file name, 8 by picture hash).
- **300 were added** with rule (a), **79** with rule (b).

## Results

"Right product" counts other colours and sizes of the same product, as before.

| Pages | Set | Photos | Right product | Other product | No product visible | Pages with a wrong-product photo | Pages with fewer than 3 right photos |
|---|---|---|---|---|---|---|---|
| **Hard (15)** | Screenshot alone | 110 | 92 (84%) | 1 | 16 | 1 | 1 |
| | + added (a) all | 210 | 151 (72%) | 6 | 52 | 3 | 1 |
| | + added (b) capped | 152 | 119 (78%) | 2 | 30 | 1 | 1 |
| **Ordinary (19)** | Screenshot alone | 206 | 180 (87%) | 12 | 12 | 6 | 1 |
| | + added (a) all | 406 | 331 (82%) | 25 | 48 | 8 | 0 |
| | + added (b) capped | 243 | 208 (86%) | 12 | 21 | 6 | 0 |
| **All (34)** | Screenshot alone | 316 | 272 (86%) | 13 | 28 | 7 | 2 |
| | + added (a) all | 616 | 482 (78%) | 31 | 100 | 11 | 1 |
| | + added (b) capped | 395 | 327 (83%) | 14 | 51 | 7 | 1 |

The same, all 34 pages, with the 40 near copies taken out:

| | Photos | Right product | of which other colour or size | Other product | No product visible | Pages with a wrong-product photo |
|---|---|---|---|---|---|---|
| Screenshot alone | 316 | 272 | 60 | 13 | 28 | 7 |
| + added (a) | 576 | 449 (78%) | 171 | 29 | 95 | 11 |
| + added (b) | 389 | 322 (83%) | 92 | 13 | 51 | 7 |
| + added (b2): copies of picks dropped first, then 10 | 413 | 340 (82%) | 101 | 14 | 56 | 7 |

- **Capped (b) adds about 50 right photos on 34 pages (1.5 a page) and no new wrong product.** Its one other-product photo (Glossier's "how to use" graphic with the Boy Brow Arch pencil) is a bigger copy of the screenshot method's own wrong photo. Pages with a wrong photo stay at 7.
- **Most of the gain is other colours.** Of the 50, 32 are other colours or sizes: MOFT (9 colours), Brooklinen (9), Vuori (9), Kosas (4 shades). Only 18 show the page's own colour (or a colour that can't be told). The planner keeps one colour, so for it the real gain is smaller.
- **The gain sits on few pages.** 14 of 34 pages get at least one right photo; 3 pages get 9; 20 pages get nothing.
- **Pages with fewer than 3 right photos:** 2 → 1. Levi's (Macy's) gets a third photo. Liquid I.V. stays at 2: its record has one photo, the one already picked.
- **All (a) brings in other products:** 18 other-product photos on 5 pages (Lodge 9, Maybelline 4, MOFT 3, Greenies 1, Glossier 1), and 72 photos with no product (Amazon infographics, before/after eyes, swatches). Pages with a wrong photo go from 7 to 11 (Glossier already had one).
- (b) adds 23 no-product photos, 8 of them Fire TV's TV-screen graphics (all 10 of its added photos show no stick or remote).

## Other-product photos the product call added (rule a)

| Page | Label | In (b)? | What it shows |
|---|---|---|---|
| Maybelline Sky High | A2 | no | Sky High Tinted Primer tubes (white): a different product sold as a "colour" option |
| Maybelline Sky High | A16 | no | Tinted Primer tube with the Allure badge |
| Maybelline Sky High | A23 | no | "Prime first" graphic: Tinted Primer next to the mascara |
| Maybelline Sky High | A25 | no | Sky High Curves waterproof mascara in a blister pack |
| Lodge skillet | A25, A27, A30, A38 | no | Skillet sold with a red silicone handle holder (a bundle option); A38 copies A27 |
| Lodge skillet | A35, A51 | no | Set of three skillets of different sizes |
| Lodge skillet | A64 | no | Stove scene with a Dutch oven and a griddle |
| Lodge skillet | A81 | no | Outdoor grill with many Lodge pans and a griddle |
| Lodge skillet | A82 | no | Campfire: skillet with oysters and a lidded Dutch oven |
| Greenies | A30 | no | "Find the right size" chart: Teenie, Petite, Large next to Regular |
| Glossier Boy Brow | A11 | **yes** | "How to use": Boy Brow Arch pencil next to Boy Brow (same graphic as screenshot pick S5) |
| MOFT stand wallet | A6, A9, A13 | no | Trackable (Find My) 2-card wallet next to a phone: another model, sold on the page as a "version" |

Close calls: MOFT's Trackable wallet is offered on the page as a version, so a gentler judge might call it another variant. Lodge's handle-holder photos and the three scenes are the product with something else in the frame; the strict brief calls them other products. The Tinted Primer is sold as a shade on Amazon but is a different product in a white tube.

## Pages where the product call added no new right photo (20 of 34)

- **No usable record (4):** Mack Weldon (no record file), Bose and Best Buy Sony XM6 (the call returned no product), LMNT (record has no photos).
- **All photos too small (1):** Sephora Rare Beauty (23 photos, all 250 × 250).
- **Every photo was already picked (12):** Nalgene, KONG, Zwilling, Paula's Choice, Nécessaire, Liquid I.V., Peak Design, Satechi, Graza, Wild One, Allbirds, Buck Mason. On these Shopify-style sites the record holds one or a few photos, and the screenshot method had them all.
- **Added only photos without a right product (2):** Fire TV (10 TV-screen graphics), Barbour (1 grey placeholder tile).
- **Added only a copy (1):** Stanley's one added photo is the same render as screenshot pick S3.

(Barbour's record has 9 file names; 8 were the screenshot picks.)

## Things that are odd in the records

- **Fire TV:** the page is ASIN B0CJM1GNFQ; the record is ASIN B0F7Z4QZTT. The product call may have landed on a newer model. Its added photos were all no-product, so this did not change a verdict.
- **Maybelline:** the page's own ASIN is labelled "TRUE BROWN" in the record while the page title says True Black.
- **"Page's variant first" never changed the order:** on every page the variant matching the link was already the record's first variant (or no variant had an id to match). On Shopify records each variant usually has one photo, so the cap of 10 fills with other colours (Brooklinen, Vuori, MOFT).

## What this does not settle

- **One judge, and a different one.** The screenshot verdicts come from the earlier judges; the added photos were judged by me. I judged blind (every added photo comes from the product call anyway), but my calls on close cases may differ from theirs. The close calls above move "other product" by up to 13 photos for rule (a) and by 0 for rule (b).
- **I knew the test's question while judging.** I did not know which photos went into rule (b) or which were copies.
- **Near copies were found by eye, after judging.** The rule was "the same photograph": other crop, background or a text panel on top counts as the same; another colour does not. Lodge's studio shots of other sizes look identical and were counted as copies of the screenshot picks (13 of the 40). Someone else would draw the line a little differently.
- **The picture hash is a weak copy finder.** It merges different colours of the same shot (Allbirds, MOFT, Our Place) and misses the same picture in another crop (Greenies, Lodge). The (a) numbers before near-copy removal count some photos twice.
- **Screenshot picks are capped at 10 on hard pages but not on ordinary pages,** as they were scored before. The ordinary pages already had more photos (11 a page against 7), so the product call has less to add there.
- **The pages and records were saved on different days** (product records from the 100-page test, screenshot picks from 2026-09-30). A page that changed in between can give a "new" photo that the page no longer shows.
- **Only one size rule was tried.** A stricter one would drop more of Amazon's graphics; a looser one would keep Sephora's 250 px photos.
