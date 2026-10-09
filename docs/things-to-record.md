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




----------------------------------------

Every B-roll scene now gets a drawn start picture, and the picture is checked before we pay for video (2026-10-08).

Before, a scene that "needed" something the main photo couldn't show skipped the start picture and sent the shop photos straight to the video model. The photos' own scenes leaked into the clip (#12 failed that way; drawn, it passed), and a wrong "need" on Lemi Shine dropped the picture and the video model invented a dirty tile.
Now the extra photos go to the picture model instead, each with one job ("only how the tea's colour looks").
The check looks at the start picture beside the shop photo: real object shapes, product matches the photo, label turns with the product, the "before" is shown, no face when there should be none, and no clashing orders in the prompts. A picture that fails is redrawn from fixed prompts, at most twice. A redraw costs cents; a bad clip costs about $0.40.
GIF shop photos are turned into PNG before drawing, because the picture model refuses GIFs (BrüMate can cooler).

----------------------------------------

## Why we shipped the test-ad fixes (2026-10-09)

Julian graded the 5 combined test ads (08 Oct). Each fix answers one of his grades:

1. Voice cut off at the end of every ad. ffmpeg's amix threw away the last ~1.2 s of voice when the voice ended while the music was still longer. The voice is now padded and the mix cut at the ad's end.
2. Too much talking. 7 talking scenes sat in the middle of 5 ads. Now scene 1 talks, the last talks or shows the product at its best, and every middle scene is B-roll. A line nobody can film ("no bleach") is said over another B-roll.
3. Pointless zoom on the toilet. The planner wrote "the camera pushes toward" itself. Now the one movement is a hand or the product, never the camera.
4. Le Duo pulled straight while the line promised curls. The planner was told to split "turning" off as its own step. Now it keeps what the page says happens during the action ("twist while sliding down").
5. Phone case showed the back camera on both sides. The clips turned and flipped the phone from a start picture of one side, so the model invented the other side. Now the product is never turned to show another side; another side gets its own scene.
6. Shower cleaner: the "builds up" line was talking, and before looked the same as after. New B-roll kind "shows the problem"; a scene about a thin film needs a real photo of it, or the voice says it over another B-roll; the picture check needs the problem plainly visible.
7. Every B-roll looked phone-filmed. The "handheld phone video, casual, not cinematic" opener is gone; code now says "The camera stays still." and the video model picks the look.

Full list with file:line: project files decisions/built-test-ad-fixes.md.
