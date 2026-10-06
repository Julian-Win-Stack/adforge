# Colour-list test: how many good photos does the colour list throw away? (#110)

Run 2026-10-05 by Claude. 37 product pages were read and planned the way the app does it (`read_page`, then `plan_ad`, through the producer's own tools), and the run stopped there: no person, pictures, voices or clips. Claude then looked at every kept photo (contact sheets in `sheets/`, green frame = on the list, red = dropped) and marked why each dropped photo was dropped, and whether a scene might need it (`judged.json`).

**Still to do:** the user judges, on all 35 pages, which dropped photos a scene really needs and which listed photos shouldn't be on the list (`review/index.html`). Until then, number 3 below is Claude's guess.

## The pages

`products.json`: the 17 test products from `docs/test-products.md` with more than one photo that make a full ad (2–17 and 20), and 20 pages from the 100-page scraping test (`docs/scraping-test/job-b/pages100.json`) chosen because they come in several colours, shades or sizes. **35 were planned.** Steve Madden and Anker weren't: their page text copy failed 3 times out of 3 (below).

## The 5 numbers (35 pages)

Per page: `counts.md`.

| | Total | |
|---|---|---|
| 1. Photos lost | **153 of 315 dropped (49%)** | 162 on the list |
| 2. Why each was dropped | **85 another colour**, 1 another size, 17 with other products, **23 product not clearly seen**, 6 no product in it, **8 right colour and clearly seen**, 13 can't tell | |
| 3. Good ones lost (Claude's guess) | **38 photos on 16 of 35 pages** | the user checks all 35 pages |
| 4. Lists with 0 or 1 photo | **6 pages**; 5 of them had more photos kept | Glossier 1/7, Merit 1/14, Wild One 1/10, Buck Mason 1/6, Molly's 1/2; Rare Beauty kept only 1 photo |
| 5. Wrong marks | **4 photos on 2 pages** | Moft: a pink wallet on a "pale lavender" list, and one seen edge-on. Ilia: two faces with no bottle |

What the numbers say:

- **Most drops are right.** The 85 "another colour" drops are the list doing its job: Kosas' 33 other shades, Peak Design's 12 other colours.
- **Wrong marks are rare: 4 photos.** Putting the wrong photo on the list is not the problem.
- **Good photos are lost, and they are the ones B-roll asks for.** The plans' own B-roll scenes describe what the dropped photos show:
  - Nomad: scene 4 is "a MagSafe charger attaches to the back of the case"; photos 3 and 8 show exactly that, and were dropped. 5 photos of the black case on a phone, clearly seen, were dropped.
  - Great Jones: scene 4 is "the Dutch Baby nested inside The Dutchess"; photo 9 shows that.
  - Molly's Suds: scene 5 is "the bottle beside the toilet"; photo 2 shows that.
  - Supergoop: scene 4 is "hands apply the clear gel across the face"; photo 2 is the gel on a cheek (the first run's sunscreen problem, again).
  - Wild One: scene 2 is "a dog stands wearing the harness"; photo 5 shows that, so the list kept only 1 photo of 10.
  - Glossier: scenes 3 and 5 brush brows; photo 5 is the wand on a brow.
  - Rokform: scene 5 twists the case into a mount; photos 4 and 8 show mounts.
- **"Clearly seen" is applied unevenly.** Ilia's faces with no bottle are on the list; Merit's faces with the blush on them aren't. And 8 photos were dropped though the product is the right colour and clearly seen, mostly because something else is in the picture: the phone around Nomad's case, the ingredients around Momofuku's bottle.

So the worst number is **3, good photos lost**, with 4 (lists left with 0 or 1 photo) as its result. That points to **fix a)** of #110: two notes per photo, its colour and whether the product is clearly seen. Starting pictures need both; a B-roll scene showing the product in use needs only the right colour. Fix a) covers the "not clearly seen" drops (23) and most "can't tell" ones (13). The 8 "right colour and clearly seen" drops are a different mistake (too strict about what else is in the picture), which a clearer description of "clearly seen" would cover in the same change. To be decided with the user once they've checked the sample.

## Found on the way (not this ticket)

- **The page text copy drops the price.** On 11 of 35 pages the planner asked the shop owner for the price because the copied text (`Job.page_text`) has none, though the page's full text does (Kosas: $32, Allbirds: $100, Nalgene: $20.00); on 2 more (Fellow, Brickell) it asked which of two prices. Those 13 pages were answered with the price their own page shows (`answers.json`). Being fixed in a separate session.
- **The text copy sometimes gives back cut-off JSON** (`copy_page_text`, "EOF while parsing a string"): Steve Madden and Anker, 3 tries each. The page then can't be read at all.
- **Firecrawl's rate limit** (about 33 requests a minute) was hit with 6 pages read at once, and the app fell back to the plain download without waiting and trying again. Those pages were read again, 2 at a time, and only those reads are counted.
- **The planner always needs a price.** Told "don't state a price", it asked again: every plan must say one.
- Mushie's list has photos of a baby, though the ad must never show children. The list doesn't check for that.

## How it was run

Scripts in `scripts/`, run from `backend/scratch/colour-list-test/` (git-ignored, mounted in the container):

```
docker compose up -d db redis backend
docker compose exec backend python scratch/colour-list-test/run.py      # pays Firecrawl and OpenAI
docker compose exec backend python scratch/colour-list-test/sheets.py
python3 backend/scratch/colour-list-test/count.py > counts.md
docker compose exec backend python scratch/colour-list-test/review.py
```

- `run.py` answers any question the planner asks with "use the first option the page shows, bought one-time; leave out anything the page contradicts itself on", plus the page's price where the planner asked for one, then plans again, at most twice.
- Cost: **$18.45 of model calls** (page check, text copy, photo pick, plan; including the first, rate-limited tries and re-plans) and about 160 Firecrawl credits. The 35 counted results alone cost $16.48 and 111 credits. Kosas, with 42 photos, cost $2.94 by itself: every photo is shown to the planner each time it plans.
- Not measured: how many B-roll scenes were made from words because their photo was dropped. The photo for each scene is picked after the planner (`jobs/scenes.py`), so a run that stops at the plan can't show it.
