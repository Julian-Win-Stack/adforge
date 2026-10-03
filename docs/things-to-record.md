Firecrawl fixes two things:

Blocked sites. Big stores like Sephora, Home Depot, Chewy, Best Buy and Macy's refuse our plain download. Firecrawl gets in. Our scraper got 73 of 100 pages, Firecrawl got 94.
Text that only appears after JavaScript runs. Some pages send an empty shell and fill in the description later with JavaScript. Our scraper only sees the shell. Firecrawl runs the JavaScript and sees the full text. This happened on 39 of the 70 pages both scrapers could read.

Firecrawl does not fix three things:

Photos. The photos are already in the HTML we download today. Firecrawl just gives a bigger, messier list. Either way we need a filter to pick the real ones.
Text about other products on the same page (the sweatpant problem). Firecrawl copies "you may also like" and "shop the look" sections too. We have to cut those ourselves.
Speed. Firecrawl is slower, sometimes minutes per page






----------------------------------------


Swtiched from Borel to heygen because when Borel is used in talking scenes, 
The person moved in slow motion.
The lips didn't match the words.
The face was over-expressive.





----------------------------------------

## Step by step of how we have been fixing and improving the scraping part of the app

Yes. The docs show you're fixing the step where the app reads a product page and pulls out that product's text and photos to write an ad. The first real run ([docs/runs/first-run.md](docs/runs/first-run.md)) showed it going wrong. For example, the Mack Weldon sweatshirt ad picked up text about a *sweatpant* from a "shop the look" section on the same page.

There are really three separate problems:
1. **Getting the page at all.** Some stores block the scraper, and some pages only fill in their text after JavaScript runs.
2. **Other products' text.** "You may also like" and FAQ sections leak into the ad.
3. **Other products' photos.** Sibling items and cross-sell tiles get picked as product photos.

## Methods tried, in order

**1. The current scraper (the starting point).** `backend/jobs/page.py` does a plain download of the page, with no JavaScript. It keeps the visible text plus the page's JSON-LD (structured product data stores embed for Google). Photos come only from JSON-LD and og:image.
- It got 73 of 100 pages. Big retailers blocked it (Sephora, Home Depot, Chewy and others).
- It missed text on 39 of 70 pages because that text only appears after JavaScript runs.

**2. Firecrawl (a hosted headless browser)** ([results-100.md](docs/scraping-test/results-100.md)). It runs the page in a real browser.
- It got 94 of 100 pages and saw the JavaScript-loaded text.
- It did **not** fix other-product text or messy photo lists. It returns 16 to 640 images per page, mostly junk.

**3. Photo filter "Job A".** Cheap rules come first: drop icons, SVGs, tiny images and banners. Then one gpt-5-mini call per image asks "is this the product?"
- 77% of the photos it kept were right (550 wrong ones kept).

**4. Fix test** ([fix-test/results.md](docs/scraping-test/fix-test/results.md)):
- **Text:** the page is cut into sections at each heading. The AI then labels each whole section as this product, another product, or not product info. Other-product sentences leaking through dropped from 102 to 11.
- **Photos:** one AI call first picks a **reference photo** of the product. Every other image is then compared to it ("is B the same product as A?"). A **link rule** is added: an image that links to a different page is probably another product's card. Accuracy rose to about 91–97%.
- Background research ([industry-research.md](docs/scraping-test/fix-test/industry-research.md)) found what real teams do: read the page's structured data first, and only point the AI at the main product area of the page.

**5. Screen test, "read the page like a person"** ([screen-test/results.md](docs/scraping-test/screen-test/results.md)).
- A script (`mark.js`) draws numbered boxes on every picture and heading, then takes a full-page screenshot. The AI looks at the screenshot and picks which boxes belong to the product.
- It was compared against the **shop's own photo list** (Shopify's `.json`, Amazon's gallery data, JSON-LD).
- On 15 hard pages, the screenshot method had the fewest wrong photos: 1 wrong product, against 27 for the fix-test method.

**6. Text test** ([text-test/results.md](docs/scraping-test/text-test/results.md)). `open.js` first clicks open closed accordions and tabs. Then two versions were compared:
- **Pieces:** the page text is cut into smaller numbered chunks and the AI picks chunks. It keeps more product text but leaks more other-product text: other products showed up on 24 of 34 pages.
- **Copy:** the AI copies product sentences word for word off the screenshot. Each copied sentence is checked against the real page text, so made-up sentences can't get in. It leaks almost nothing (5 of 34 pages) but misses text hidden in tabs that stayed closed.

The pattern: you started with a simple download, moved to a real browser (Firecrawl), then kept adding smarter ways to choose only this product's content, ending with screenshot-based methods. The latest work is weighing "pieces" (more complete, leakier) against "copy" (cleaner, misses hidden text). According to [second-run.md](docs/runs/second-run.md), none of the rebuilt scraper is in the app yet; the second run still used the current scraper.



----------------------------------------

## docs/scraping-journey.md   <- Look at this. This is how I got the final scraping method. Talk about this and make this visible for the reviewers. 


----------------------------------------


