# Reference test: give the photo-picking call the product record while it picks

Date: 2026-10-02. Follows `text-test/results.md` and `photo-merge-test/results.md`. The user's idea: the screenshot call that picks the product's photos also gets what Firecrawl's product call returns (official photos, name, description, colour/size names) as a guide, so it can tell this product from look-alikes.

## What was tested

- Pages: the text test's 35 pages, screenshotted again (`/tmp/tt`, 35 Firecrawl credits). Both runs read the **same** screenshots, so the only difference is the reference.
- **ref** (`REF=1 python scripts/point_ref.py ref`): gpt-5.6-sol gets the shop's record (title, brand, description, variant names) and up to 6 official photos (one per colour, labelled R1…), then the screenshot pieces as before. The prompt adds `REF_NOTE` in `scripts/point_ref.py`: a photo is this product only if it matches an official photo; answer with page photo numbers only.
- **noref** (`python scripts/point_ref.py noref`): the same call without the record.
- Records and official photos come from the fix test (`photo_rec.py`). LMNT's record has no photos, Mack Weldon has no record, Bose and Best Buy's records are empty: on those pages ref is the same as noref apart from the prompt note.
- Every photo picked by either run (gallery + more, no cap) was downloaded, copies merged, shuffled and judged blind by 5 Claude subagents (`JUDGE-BRIEF.md`, `judged/`, `scripts/build_judging.py`, `scripts/score.py`). Chewy showed a sign-in page; neither run picked anything, so 34 pages.

## Results (34 pages)

| | With reference | Without |
|---|---|---|
| Photos picked | 349 | 332 |
| Right product (incl. other colours/sizes) | 322 (92%) | 303 (91%) |
| Other product | 9 | 7 |
| No product visible | 17 | 21 |
| Unclear | 1 | 1 |
| Pages with an other-product photo | 4 | 3 |

- **Other-product photos picked by both runs (7):** Paula's Choice (5: line-ups of the 1%/2%/4% BHA and BHA next to AHA, a group routine shot, a woman holding a pale blue tube), Zwilling ("The Essentials", three different knives), Kosas (a group with a colour-corrector tube). The reference did not stop any of them.
- **Extra wrong photos with the reference (2):** another Paula's Choice group shot, and an LMNT firefighter drinking from an LMNT bottle (the drink-mix pack is not shown). LMNT had no official photos, so this is not the reference misleading it.
- **Extra right photos with the reference (27):** mostly other colours or flavours: 12 LMNT flavour packs (noref picked 3 LMNT photos, ref 16), 4 Sony XM6 colours plus 3 more black XM6 shots on Best Buy, 4 Lodge, 2 Fire TV.
- **Photos only noref picked (12):** 6 right, 2 other colours, 4 no-product (Sephora face and arm swatches, a Fire TV screen).

## Cost and time (35 pages)

| | With reference | Without |
|---|---|---|
| Cost (gpt-5.6-sol) | $5.79 (median 14.8 cents a page) | $5.61 (14.5 cents) |
| Median seconds | 32 | 34 |

The official photos are sent small (detail low), so they add almost nothing to the cost.

## What this shows

- The reference **did not cut wrong photos**: 9 against 7, the same pages. The wrong photos that both runs pick are group and line-up shots in which this product appears next to others; matching against an official photo doesn't rule those out, because the product *is* in them.
- It made the call pick **more photos of other colours and flavours** (LMNT, Sony), which the judges count as right.
- Both runs are already at 91–92% right on these pages; the text test's run of the same call on 19 of them was 87–88%.

## What this does not settle

- One run each. The text test's two runs of the same call differed by 32 photos, so 17 more photos and 2 more wrong ones are within run-to-run noise.
- Five judges, one per group of pages; close calls (which colour counts as "the" product, kit pieces alone, Paula's Choice size line-ups) may be called differently by another judge.
- On 4 pages the record was empty or had no photos, so the reference could not help there.
