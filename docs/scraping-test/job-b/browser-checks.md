# Browser check of unclear pages (2026-09-28, Claude's built-in browser, real Chromium)

All six pages loaded as full, live product pages in a real browser. None is dead.

| Page | Current scraper | Firecrawl | In a real browser |
|---|---|---|---|
| Target Goodfellow tee (retail-04) | thin: 1,925-char JS shell | thin: 1,037-char JS shell | Live. Full product page (title, fit & style bullets, sizes). Content is rendered by JavaScript after load. |
| Nordstrom Zella leggings (retail-23) | empty: 200 with no text | thin: 72 chars, a "Moved Permanently" redirect notice | Live. Redirects to /s/live-in-high-waist-leggings/4312529 and shows the full page. Firecrawl did not follow the redirect. |
| Macy's Levi's 501 (retail-25) | blocked: HTTP 403 | thin: 1,082-char shell (nav and deals only) | Live. Full product page with price, colour and sizes. |
| Sundays chicken dog food (small-33) | ok | empty: no answer after 316 s | Live. Full page. Firecrawl timed out; our scraper had no trouble. |
| Wild Earth performance formula (small-34) | ok | empty: no answer after 318 s | Live. Full page. Same: Firecrawl timeout. |
| Walmart onn 4K stream (retail-05) | ok | empty: no answer after 316 s | Live. Full page. Same: Firecrawl timeout. |

So: every failure in the test was a block, a JavaScript-only page, or a Firecrawl timeout. No URL was dead.
