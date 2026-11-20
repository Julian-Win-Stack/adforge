# Reading the page

A sweatshirt ad once described the fit of the sweatpants sold in a bundle on the same page. The sentence really was on the page, so the fact check let it through.

That matters because every fact in an ad comes only from the page or the user ([ADR 2](../adr/0002-facts-come-only-from-the-page-and-the-user.md)). So if the page reader picks up another product's text, the ad says wrong things about this product.

What I did: Firecrawl gives back the text, a full-page screenshot with a number on every picture, and the shop's own product record. Then:

- One model picks this product's photo numbers off the screenshot.
- A cheap model copies this product's passages word for word, and code drops any sentence that isn't on the page.
- The price comes from the user's words, or the main-looking option on the page with its size or pack. If there's no price on the page, there's no price in the ad.

It works. Other-product sentences: 0 of 102 leaked, on 70 pages, and 87% of useful sentences were kept. Other-product photos: 1 of 15 hard pages, against 9 of 15 with the AI filter I dropped.

It still can't read TikTok Shop, Chewy, Target or Temu. The user can paste the page text and photos instead.
