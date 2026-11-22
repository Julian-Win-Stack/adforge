# Reading the page

You give the app a product link. It gives back this product's text, price and photos. The whole ad is made only from these ([ADR 2](../adr/0002-facts-come-only-from-the-page-and-the-user.md)).

**The problem:** a page shows more than one product: bundles, "you may also like", reviews. Once, a sweatshirt ad described the fit of the sweatpants in a bundle on the same page. The sentence really was on the page, so the fact check let it through.

**Why 3 Firecrawl calls, not one:** Firecrawl opens the page in a real browser.
- **Page read** gives all the page's text and every photo on it. That's about 60 photos on average and sometimes hundreds, review photos included. Picking the right ten or so out of that is really hard. The text is more reliable, a bit structured, and easy to filter. So I only take the text from it.
- **Marked screenshot** scrolls the whole page, puts a number on every picture and takes one tall screenshot. I think the photos a real shopper sees, mainly the gallery at the top, are enough to make an ad. So there are no hundreds of photos to filter. An AI call looks at the screenshot and picks this product's numbers.
- **Product record** is the shop's own short record: title, price and a few official photos. It tells that AI call which product we're targeting.

**Then:** another AI call copies this product's sentences word for word, and code drops any sentence that isn't on the page. The price comes from the record, or the main-looking option on the page. The user's words win. No price on the page means no price in the ad.

**It works.** Text: 70 pages had 102 sentences about other products. 0 got in, and 87% of the useful ones were kept. Photos: another product's photo got in on 1 of 15 hard pages, against 9 of 15 with the AI filter I dropped.

**Still can't read:** TikTok Shop, Chewy, Target, Temu. The user can paste the text and photos in instead.
