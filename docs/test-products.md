# The test products for the first error-analysis run

The 24 product pages the first run of test ads is made from, with quality checks switched off. The run shows what goes wrong in real ads, which decides which quality checks the critic runs and what the evals measure (spec #1, Evaluation). The ads made from these pages are kept and reused: the critic and the evals are tested on them rather than on new ads.

Every page was checked on 2026-09-27 with the app's own page reader (`jobs/page.py`): one plain HTTP request, the visible text, the product data the page declares for search engines, and the photos the app would keep, downloaded as the app downloads them. "Distinct photos" counts different pictures: many shops list the same picture twice, at two sizes. Every product page below was in stock, showed its price, and declared its product data. Prices and sales change, so check a page again just before its run, especially 12 and 19.

## How the set was chosen

Each product is here because it answers a question about the app. Two kinds of run:

- **Normal products (1–10)** are what a real shop would send. They make full ads, and most of what the critic will check shows up in them. They lean towards the categories this kind of ad is most made for: beauty, supplements, clothing, gadgets and home.
- **Hard cases (11–24)** each test one thing. 11–20 still make a full ad. 21–24 should stop before anything is made, so they cost almost nothing.

Only physical products, from US shops, priced in US dollars, in English. Mostly small and mid-size online brands, the shops Creatify sells to, with a few well-known brands. No alcohol, CBD or weight-loss products.

## Normal products

| # | Slot | Product | Price | Distinct photos | What to watch |
|---|---|---|---|---|---|
| 1 | Skincare | [Naturium Multi-Active Exosome Serum](https://naturium.com/products/multi-active-exosome-serum) | $27 | 1 | One photo on purpose: most beauty shops give only one (see below) |
| 2 | Makeup | [Merit Flush Balm](https://www.meritbeauty.com/products/flush-balm) | $30 | 3 | 13 shades: the planner picks one colour and never says it |
| 3 | Supplement | [Vital Proteins Salted Caramel Collagen Peptides](https://www.vitalproteins.com/products/salted-caramel-collagen-peptides) | $27 | 8 | The page talks about hair, skin and nails: never a body or health result |
| 4 | Clothing | [Mack Weldon Vintage French Terry Crew Neck Sweatshirt](https://mackweldon.com/products/vintage-french-terry-crew-neck-sweatshirt) | $108 | 5 | 4 colours at one price. Is it held up or worn? |
| 5 | Well-known brand | [Steve Madden Kenzo Bag Gold](https://www.stevemadden.com/products/bkenzo-gold) | $88 | 4 | A bigger, busier brand page |
| 6 | Gadget | [Anker 313 Power Bank](https://www.anker.com/products/a1229) | $25.99 | 9 | The declared name has HTML codes in it (`Anker &lt;b&gt;313&lt;/b&gt; Power Bank`). Small label print. Never invented screens |
| 7 | Kitchen | [Great Jones Dutch Baby](https://greatjonesgoods.com/products/dutch-baby) | $165 | 3 | 28 other products' prices on the page. 6 colours |
| 8 | Cleaning | [Molly's Suds Toilet Bowl Cleaner](https://mollyssuds.com/products/toilet-bowl-cleaner) | $9.99 | 2 | The cleanest page: one price and nothing else |
| 9 | Food | [Momofuku Chili Crunch Sauce](https://shop.momofuku.com/products/chili-crunch-sauce) | $10 | 3 | B-roll of it spooned over food, and only foods the page names |
| 10 | Pet | [maxbone Enrichment Tether Toy](https://www.maxbone.com/products/maxbone-tether-toy) | $20 | 3 | Hidden "out of stock" text, though it is in stock |

## Hard cases that make a full ad

| # | What it tests | Product | Price | Distinct photos | What should happen |
|---|---|---|---|---|---|
| 11 | A benefit that can't be shown (a smell), a glass bottle | [Beardbrand Fox Hunt Men's Cologne](https://www.beardbrand.com/products/fox-hunt-mens-cologne) | $50 | 3 | Scenes show only what the page states, never a made-up effect |
| 12 | A see-through product | [Smartish Gripmunk Slim Case for iPhone 17e](https://smartish.com/products/gripmunk-clear-slim-case-for-iphone-17e) | $14.99, was $19.99 | 9 | The picture copies the clear case. Also on sale: says $14.99. The page title says "16e", the product name "17e" |
| 13 | A tiny product | [Gorjana Melrose Diagonal Studs](https://www.gorjana.com/products/melrose-diagonal-studs) | $60 | 3 | The studs are visible in the picture |
| 14 | Too big to hold at chest height | [Branch Swivel Chair](https://www.branchfurniture.com/products/swivel-chair) | $199 | 2, one only 240 px | The picture and clip make sense for a chair |
| 15 | A kit of several parts | [Nécessaire The Body Ritual Kit](https://necessaire.com/products/the-body-ritual-kit) | $50 | 2 | Which of the 3 bottles is shown, and whether it matches the line |
| 16 | Baby: never show children | [Mushie Space Teething Ring](https://mushie.com/products/space-teething-ring) | $14.99 | 3 | No child in any scene |
| 17 | The person suits the buyer | [Brickell Beard Oil](https://brickellmensproducts.com/products/beard-oil) | $25 | 3 | A presenter with a beard |
| 18 | Reviews claim more than the brand | [Starface Hydro-Stars + Big Yellow](https://starface.world/products/hydro-stars-big-yellow) | $14.99 | 1 | The brand says it shrinks pimples; reviews say it cleared their skin. The ad says only what the brand says |
| 19 | On sale | [Titan Foam Fitness Mat](https://titan.fitness/products/foam-fitness-mat) | $80.97, was $89.99 | 1 | Says $80.97 |
| 20 | Several prices | [Supergoop Unseen Sunscreen SPF 50](https://supergoop.com/products/unseen-sunscreen-spf-50) | $19, $38, $48 or $76 by size | 5 | Asks which price. The declared data says $38, the headline $19 |

## Hard cases that should stop early

| # | What it tests | Page | What the app sees | What should happen |
|---|---|---|---|---|
| 21 | A page that needs JavaScript | [SNOO Smart Sleeper](https://www.happiestbaby.com/products/snoo-smart-sleeper-bassinet) | 1,783 characters of menu text, no declared data, and its one "photo" is a web page, not a picture | Judged unreadable; asks for another link |
| 22 | A category page | [Nécessaire body collection](https://necessaire.com/collections/body) | 61 prices for many products | Judged unreadable: it lists many products |
| 23 | Amazon | [Stanley Quencher H2.0](https://www.amazon.com/dp/B0CJZMP7L1) | The page reads, but 0 photos | Asks the shop owner to attach a photo |
| 24 | No price | [Life Fitness Integrity Series Treadmill](https://www.lifefitness.com/en-us/catalog/cardio/treadmills/integrity-series-treadmill) | The product described in full, 0 prices, "request a quote" | Asks for the price |

## What checking the pages found

These are guesses about where the app may fail, found before any ad was made. The run shows whether they do.

- **Most beauty shops give the app one photo.** 8 of the 9 skincare and supplement pages checked had a single distinct photo, listed twice at two sizes. For beauty, an ad from one photo is the normal case, so every scene's starting picture comes from the same photo.
- **Almost every page holds other products' prices**, in menus, "you may also like" sections and cart suggestions. The planner or the fact check may take the wrong one.
- **Many in-stock pages hold hidden "Sold out" or "Notify me" text** from the shop's template. The producer may think the product is unavailable.
- **Customer reviews are part of the page text**, and nothing tells the planner or the fact check whether a review counts as the page stating something (18).
- **Amazon doesn't block the app**: it reads the page, then finds no photos (23).
