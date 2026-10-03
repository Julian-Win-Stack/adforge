# Second run: 20 test ads, to find what the critic should check

- Date: 2026-09-30
- Commit: `6d5a0df` (branch `gender-and-product-size`). Worker restarted 2026-09-30 23:19 UTC
  to load it. The scraper is the current one; its rebuild is not in the app yet, so page
  problems (missing facts, another product's facts, wrong photos) are expected and tagged
  "page" in the review.
- Why: the fixes from the first run are in, and the critic's checks must come from what goes
  wrong now, not from memory (#83: "add cases from evidence, not imagination"). Quality checks
  are off: none is built (#47).
- Products: #1–20 of [docs/test-products.md](../test-products.md). First messages as in the
  first run: #1–6 `Make a 30-second ad for this product: <link>`; #7 and #10–20
  `Make an ad for this product: <link>`; #8 `15-second`; #9 `6-second`. The producer's
  questions get the first run's answers.
- Sessions are named `R2 NN · …`. Cost: sum of `gateway_modelcall.cost_usd` for the session;
  "without produce" leaves out `purpose='produce'`.
- Review page: [second-run-review/build.py](second-run-review/build.py) writes
  `second-run-review/index.html`, one row per scene plus one per finished ad, graded by eye.

## Summary

| # | Name | Session | Result | Cost | Cost without produce |
|---|------|---------|--------|------|----------------------|
| 1 | R2 01 · Skincare · Naturium Multi-Active Exosome Serum | `1824c9ea` | Finished ad (29.36 s), no questions | $2.345 | $1.937 |
| 2 | R2 02 · Makeup · Merit Flush Balm | `bff84fb1` | Finished ad (26.64 s), no questions | $2.241 | $1.757 |
| 3 | R2 03 · Supplement · Vital Proteins Salted Caramel Collagen Peptides | `e4caaa6b` | Finished ad (24.56 s), after a size question | $2.234 | $1.762 |
| 4 | R2 04 · Clothing · Mack Weldon Vintage French Terry Crew Neck Sweatshirt | `630e6694` | Finished ad (24.5 s), no questions | $1.892 | $1.484 |
| 5 | R2 05 · Well-known brand · Steve Madden Kenzo Bag Gold | `e36e5792` | Finished ad (24.28 s), no questions | $1.959 | $1.504 |
| 6 | R2 06 · Gadget · Anker 313 Power Bank | `52d9e151` | Finished ad (31.04 s), after a USB-C question | $2.341 | $1.736 |
| 7 | R2 07 · Kitchen · Great Jones Dutch Baby | `8296d5ab` | Finished ad (24.06 s), no questions | $2.047 | $1.507 |
| 8 | R2 08 · Cleaning · Molly's Suds Toilet Bowl Cleaner | `67b9c4cc` | Finished ad (15.48 s), after a price question | $1.381 | $0.982 |
| 9 | R2 09 · Food · Momofuku Chili Crunch Sauce | `1644c066` | Finished ad (5.7 s), no questions | $0.748 | $0.470 |
| 10 | R2 10 · Pet · maxbone Enrichment Tether Toy | `9e205668` | Finished ad (20.86 s), after a stock question and 20 failed starting pictures | $2.789 | $1.664 |
| 11 | R2 11 · A smell · Beardbrand Fox Hunt Men's Cologne | `301cb01f` | Finished ad (25.8 s), after a stock question | $2.097 | $1.714 |

## Cost per service, ad #1

| Service | Cost | Note |
|---------|------|------|
| HeyGen | $1.271 | 4 talking clips, 25.4 s at $0.05/s |
| OpenAI | $0.896 | of which the producer's turns $0.408 |
| fal | $0.151 | 1 B-roll clip $0.058, music $0.093 |
| Inworld | $0.025 | |
| ElevenLabs | $0.002 | |

The estimate was $2.10 per ad; this one is 4 talking scenes of 5, so HeyGen is higher than the
first run's mix would suggest.

## Questions the producer asked, and the answers given

- **#3**: "Which version should the ad feature: 10.5 oz for $27.00 or 25.7 oz for $39.99?"
  Answered "The 10.5 oz at $27." New: in the first run the planner picked $27 silently. Both are
  defensible; the difference between runs is the finding.
- **#6**: "Does this model's USB-C port support both input and 15W output, or input only?"
  Answered "Leave it out of the ad.", as in the gender-and-size run. Same question both times:
  the page contradicts itself (page problem; the question is the right behaviour).
- **#8**: "Which offer should the ad feature: the $9.99 one-time price or the 5%-off
  subscription price?" Answered "The $9.99 one-time price." New: the first run picked it
  silently. Same pattern as #3: the planner now asks about a one-time/subscription pair.
- **#10**: "Is the Enrichment Tether Toy currently in stock or out of stock? The page shows
  conflicting availability." Answered "It's in stock." (the page's "out of stock" text is
  hidden; [test-products.md](../test-products.md)). Page problem; asking is the right behaviour.
- **#11**: "Should the ad present Fox Hunt Men's Cologne as sold out or currently in stock? The
  page shows conflicting availability." Answered "It's in stock." Same as the first run (#10,
  #11, #15 asked about availability there too; the page's own product data says available).
- **#12**: "Should the ad present this Gripmunk case as made for iPhone 17e or iPhone 16e?"
  Answered "iPhone 17e." (the product name; the page title says 16e). Page problem; the first
  run asked the same.
