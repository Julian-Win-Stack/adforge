# How I rebuilt the product page scraper

This is how I got from a simple scraper to the one in the app now, what I tried along the way, and why I dropped each idea.

## The problem

In my first full test run, the Mack Weldon sweatshirt ad described the fit of a sweatpant. The sweatshirt's page had a section selling the matching Ace Sweatpant. The scraper kept that section, and the planner read it as a fact about the sweatshirt. The fact check let it through too, because the sentence really was on the page. It happened again in the second run ([04-mack-weldon-sweatshirt.md](runs/second-run-findings/04-mack-weldon-sweatshirt.md)).

So the scraper didn't just need to read more. It needed to know which product each piece of the page was about.

## Splitting it up

"The scraper is bad" was too vague to fix, so I broke it into three problems:

1. **Opening the page.** Some stores block scripts, and some pages only fill in their text after JavaScript runs.
2. **Text.** Get this product's text, and nothing about other products.
3. **Photos.** Same thing, for photos.

They needed different fixes, so I tested them separately.

I decided what "good enough" meant by asking whether a mistake would hurt the ad. The ad must never state another product's facts or show another product. Missing a small detail is fine, as long as there's enough left to write a good ad.

## How I tested

I used the same 100 live product pages for every method: 40 small brand stores, 30 big retailers (Sephora, Target, Best Buy, Home Depot and others), 10 Amazon pages and 20 others. Later rounds used the 15 hardest of those pages plus 20 ordinary ones. Keeping the pages fixed meant every method was compared on the same pages.

## Step 1: my own scraper

The scraper I started with did a plain download of the page, with no JavaScript ([results-100.md](scraping-test/results-100.md)).

- It opened **73 of 100** pages. Big retailers blocked it: Sephora, Home Depot, Petco and Macy's said no outright, and Best Buy and Walgreens never answered.
- On some pages it missed the product description entirely, because that text only shows up after JavaScript runs. The clearest case was Paula's Choice: the download was a nearly empty page with the title and nothing else. An AI judge counted missing product text on 39 of 70 pages, but I later found the judge was often wrong (see below), so I treat that number as rough.
- For photos it only took the few the page declares for Google, so it missed important ones. When I widened it to take the photos on the page, it returned hundreds on some pages, mostly copies of the same product.

## Step 2: Firecrawl

Firecrawl is a hosted browser. It opens the page like a real visitor, runs the JavaScript, and gets past most bot blocking.

- It opened **94 of 100** pages, including 26 of the 30 big retailers.
- It got the full text, including text that only loads with JavaScript.
- Its product record, the shop's own record of the product that the app also asks for, gave the right price for the product I was targeting on **36 of 36** pages where it found one. I checked it later, on the 37 pages of the colour-list test ([#110](https://github.com/Julian-Win-Stack/adforge/issues/110)): 12 against the price I told the planner, 22 against the price the page declares for Google, and 2 by hand. On the 37th page, Bose's, it found no product at all.

I thought about running my own browser instead. It would fix the JavaScript pages but not the blocking, since stores like Sephora block by browser fingerprint and IP address. Getting past that means proxies and constant upkeep every time a store changes its defences. Firecrawl already does that for 1 credit a page, so I used it.

Firecrawl didn't fix the other two problems:

- **Photos:** it returned 16 to 640 images per page. Most were the right product, just far too many: every colour, every size, the same photo at different resolutions. This was the same flood I got from my own scraper.
- **Text:** it copies everything on the page, cross-sell sections included. The sweatpant section was right there in its output.

So Firecrawl solved opening the page and gave me complete text. Picking the right text and photos was still on me.

## Step 3: filtering the photos with AI

My first idea was to filter Firecrawl's image list with AI, so it would keep only this product's photos ([fix-test/results.md](scraping-test/fix-test/results.md)). Across the 100 pages that was about 7,800 images. It got better over a few rounds, and I think I could have made it work. But there were just too many photos to filter, and every round added more to build. I wanted a simpler way.

## Why I stopped trusting the score

To grade each method, I had an AI judge mark every photo and sentence as right or wrong. The first photo filter came out at 77% right.

When I checked the judge's verdicts by hand, a lot of them were wrong. A re-check of 40 photos the judge marked "wrongly kept" found that 27 were actually the right product. Two AI reviewers often disagreed with each other. Later, of 50 sentences the judge said my text method "missed", 14 weren't this product's text at all ([missed-useful-copy-sol-v2.md](scraping-test/copy-test/missed-useful-copy-sol-v2.md)).

I didn't try to fix the judge. Instead I looked at the mistakes themselves: every photo and sentence the judge said had leaked in or been missed. For each one I asked a simpler question: **would this hurt the ad?**

- A sentence about another product that could end up in the script: serious. That's the sweatpant bug.
- A photo of another product: serious.
- A missed FAQ answer, a review, a care instruction: fine. A 30-second ad doesn't need them.
- A photo of the right product in another colour: fine. The planner picks one colour anyway.

I kept improving each method until the mistakes left were all in the "fine" group. That's how I decided when to stop.

The numbers in the rest of this doc still come from AI judges. I use them to compare methods against each other, not as exact scores, and the decisions rest on the mistakes I looked at myself.

## Step 4: let the AI look at the page like a shopper

A shopper never sees a list of 600 image links. They see the page. So I tried giving the AI the page itself ([screen-test/results.md](scraping-test/screen-test/results.md)):

1. Firecrawl opens the page and runs a small script that draws a numbered box on every picture.
2. It takes one full-page screenshot.
3. One AI call looks at the screenshot and answers with the numbers of this product's photos. It also gets the shop's own product record (name, brand, official photos) as a reference.

My bet was that what the page shows a shopper, mostly the main gallery, is what matters. On the 15 hardest pages:

| Method | Photos of another product | Pages with a wrong product |
|---|---|---|
| Filtering photos with AI (step 3) | 27 | 9 of 15 |
| Screenshot | **1** | **1 of 15** |

It costs about 16 cents and 21 seconds per page.

## Step 5: where the text comes from

The screenshot could give me the text too, but only the text a shopper sees. A lot of product text sits inside closed tabs, like "How to apply" and "Ingredients", and the screenshot misses it ([text-test/results.md](scraping-test/text-test/results.md)). To fix that, I'd need a script that opens the tabs on every product page. Every store builds its tabs differently, so that script would have to cover every store's edge cases, including stores I've never seen. That's a lot of work for something I already had another way to get.

Firecrawl's page text already includes what's inside closed tabs, because the text is in the page, just hidden. So I used the screenshot only for photos and took the text from Firecrawl ([copy-test/results.md](scraping-test/copy-test/results.md)):

1. An AI copies, word for word, only the passages about this product from Firecrawl's text.
2. Each copied sentence is looked up in the real page text. If it isn't there, it's dropped. So the AI can't invent or reword a fact.

On 70 pages, run twice:

- **0 of 102** sentences about other products leaked through.
- **87%** of the useful product sentences were kept.
- It cost **less than a cent per page** with gpt-6-luna. The more expensive models I tried weren't clearly better, and the differences were within the run-to-run noise, so I picked the cheap one.

Mistakes I accepted: FAQ answers that only appear when clicked, and doctors' write-ups on one supplement page. Neither is needed for a short ad.

## Where it landed

For each product link, Firecrawl makes three calls at once: the page text, the marked screenshot, and the shop's product record.

- **Text:** a cheap model copies this product's passages, and each sentence is checked against the page.
- **Photos:** a model picks this product's photos from the screenshot, using the shop's record as a reference. Copies are merged and each photo is downloaded at its biggest size.
- **Price:** only from the shop's product record. The text step leaves prices out, because a page shows other products' prices too, and the price the page declares for Google is taken out as well. If the record has no price, the planner asks the shop owner.
- **If something fails** (no Firecrawl key, a timeout, the picker finding nothing), the app falls back to the old way and posts a notice in the chat, so a failure is never silent.

The photo picker still lets a few wrong photos through. It doesn't happen often, and it's almost always a photo where this product sits next to other products, like a set or a line-up. So the planner has its own rule: leave out any photo that shows another product next to this one. I tested it on a few products whose photos still had this problem, and the other products didn't make it into the ad. The text didn't need a rule like this, because almost nothing about other products got through the text step.

On the Mack Weldon page, the sweatpant section no longer gets through. The text step keeps the sweatshirt's own description, fit and care, and drops the "Why We Love 'Em" block about the Ace Sweatpant.
