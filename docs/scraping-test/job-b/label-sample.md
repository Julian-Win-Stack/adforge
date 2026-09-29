# Label check: 20 random sentences

Each sentence was on the product page (Firecrawl found it) but the current scraper missed it. gpt-5-mini labelled it, then Claude checked the label.
Write **right** or **wrong** in the last column for the **Label** (ignore Claude's check if you disagree with it).

- product_info = describes this product: what it is, ingredients, sizes, directions, specs, claims, warranty
- noise = everything else: prices, stock lines, buttons, menus, reviews, text about OTHER products, legal text

Sampled from 3,821 labelled sentences on 70 pages: 10 random `product_info` and 10 random `noise`, shuffled.

| # | Product page | Sentence | Label | Claude's check | Your mark |
|---|---|---|---|---|---|
| 1 | Lemon Lime Hydration Multiplier | 🕷️💧🔬 here’s your first look at Liquid I.V.® x Spider-Man: Brand New Day. a cinematic collab for the everyday heroes, powered by our groundbreaking Hydrascience™ technology. | product_info | wrong (Spider-Man movie collab promo caption -> noise) |  |
| 2 | Life Extension Two-Per-Day Multivitamin  | The iodine in this supplement is from a synthetic starting material.Was this answer helpful to you | product_info | right |  |
| 3 | Liposomal Vitamin C | Most people know vitamin C is important for the immune system, but what they may not consider is how much of it actually gets absorbed. | product_info | wrong (generic filler from a clinician (third-party) review, not about this product) |  |
| 4 | Creatine Monohydrate Powder | Potent, clean Omega-3 for cardiovascular + brain health | noise | right |  |
| 5 | Paula's Choice SKIN PERFECTING 2% BHA Li | A BHA exfoliant is ideal for normal to oily or combination skin prone to bumps and blemishes. | product_info | right |  |
| 6 | Always Pan | Our space-saving design prioritizes multifunctionality so you can do more with less. | product_info | right |  |
| 7 | Paula's Choice SKIN PERFECTING 2% BHA Li | 2025 Marie Claire US Skin & Hair Awards, Best Exfoliator | product_info | right |  |
| 8 | Stanley Quencher H2.0 FlowState Tumbler  | Try disabling your extensions. | noise | right |  |
| 9 | Always Pan | The steam basket is so helpful, it's completely non-stick and so great for kids cooking too. | noise | right |  |
| 10 | Basic Nutrients 2/Day | Was this review helpful? | noise | right |  |
| 11 | Luxe Sateen Core Sheet Set | But even with that annoyance ... | noise | right |  |
| 12 | Greenies Regular Dental Dog Treats, Orig | $1.25 per count($1.25$1.25 / count) | noise | right |  |
| 13 | Luxe Sateen Core Sheet Set | Previous review media slide | noise | right |  |
| 14 | Men's Tree Runners | Select A Size Add to Cart - $100Notify Me | noise | right |  |
| 15 | Paula's Choice SKIN PERFECTING 2% BHA Li | Targets stubborn acne, part of a 3-step system. | product_info | wrong (describes CLEAR Extra Strength (other product, 3-step system)) |  |
| 16 | Nature Made Vitamin D3 2000 IU Softgels  | Gell caps, so no real taste. | noise | right |  |
| 17 | Basic Nutrients 2/Day | Plus, several other well-absorbed, glycinate minerals | product_info | right |  |
| 18 | Lodge Pre-Seasoned Cast Iron Skillet 10. | Reviewed in the United Kingdom on February 26, 2026 | noise | right |  |
| 19 | The Game Enrichment Feeder | We recommend using the lowest complexity setting (rotate the inner aperture so the largest hole is facing outwards), smaller snacks, and a high-reward treat like peanut butter on the top fins to entice your pup to engage | product_info | right |  |
| 20 | SOS Daily Rescue Facial Spray | [ ![[Duo]](https://www.tower28beauty.com/cdn/shop/files/HEROIMAGE-1d4f508a4-73c7-474e-b8ca-b067edf4807c.png?v=1774803592&width=400)![Tower 28 Beauty SOS Rescue Spray in Refill size. [Refill]](https://www.tower28beauty.co | product_info | right |  |
