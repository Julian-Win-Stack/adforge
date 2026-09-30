# How real teams pull ONE product off a product page (research, 2026-09-30)

Question: how do production systems get the main product's text and photos from a
product page without picking up "you may also like" / "complete the look" products?

## The short answer

Nobody has a magic "ignore other products" switch. Reliable systems do, in order:

1. **Read the page's own structured data first.** JSON-LD `Product`, microdata,
   Shopify `/products/<handle>.js`, WooCommerce Store API, framework state blobs
   (`__NEXT_DATA__`, `__NUXT_DATA__`, `__remixContext`, `ShopifyAnalytics.meta.product`).
   These describe exactly one product by construction. Recommendation cards are never in them.
2. **Fail closed when it is ambiguous.** Firecrawl's `product` format: "ambiguous pages yield
   no product". Zyte returns a probability the page is a single-product page. Diffbot has a
   `multipleProducts` flag.
3. **Scope any AI/heuristic step to the main-product block**, never the whole page. Zyte's
   LLM attributes only get "the product section of the page". Practical rule: smallest DOM
   ancestor containing h1 + price + add-to-cart; exclude containers whose class/id match
   `carousel|recommend|related|also-bought|complete-the-look|sponsored|upsell|bundle`.
4. **Layout-aware ML (vendors only).** Zyte = full-page screenshot + HTML into a neural net.
   Diffbot = pixel-level vision + DOM. Klarna's academic model = DOM graph + CSS style
   features + LLM rerank. Not something a small team copies.
5. **Ad-creative startups publish no method.** What their docs show: Creatify's link endpoint
   "automatically scrape[s] content (images, descriptions, etc.)" and a second endpoint lets
   the API caller "Remove low-quality images/videos"; AdCreative.ai photos are "picked out from
   your website by AI" with the user confirming; Omneky prefers uploaded images and uses the
   page as fallback; Pencil connects to the store API. How their agents select images internally
   is not documented.

## Published accuracy numbers (only ones that exist)

| What | Number | Source |
|---|---|---|
| Zyte ML, product price / SKU / availability F1 (140 pages) | 0.918 / 0.841 / 0.957 | github.com/scrapinghub/product-extraction-benchmark |
| Diffbot, same benchmark | 0.824 / 0.765 / 0.943 | same |
| Plain schema.org (extruct), same benchmark | 0.685 / 0.537 / 0.626 | same |
| Zyte's own note on errors | "erroneously picking up a price from a related product" | zyte.com/blog/automatic-extraction-data-extractor-review |
| Any vendor's image-selection accuracy | none published | – |
| Klarna dataset (51,701 product pages): main-image nomination, DOM+style GCN | 0.61 (hardest of 5 fields) | arxiv.org/abs/2111.02168 |
| Klarna, GCN + LLM rerank (2025 follow-up) | 94.5% element nomination | link.springer.com/chapter/10.1007/978-3-032-29372-5_31 |
| Article extractors (trafilatura) on product pages, WCXB 2026 | F1 0.56 (vs 0.91 on articles) | arxiv.org/html/2605.21097 |
| Structured data coverage, top-100 UK/US shops | 45% of URLs have none | salt.agency structured-data audit |
| JSON-LD hit rate on one WooCommerce store | 48% | scrapingbee.com e-commerce cascade post |
| Screenshot+vision agent vs text-only (WebVoyager) | 59.1% vs 44.3% task success | arxiv.org/pdf/2401.13919 |
| Kadoa on GPT-4V screenshot scraping | "very inefficient and costly", hallucinates | kadoa.com/blog/using-gpt-4-vision-for-multimodal-web-scraping |

## Our own numbers on the 100 test pages (measured this session)

Three "structured data first" sources, scored against the photo truth (6,281 images, 87 pages)
and text truth (3,821 sentences) from the fix test. Image matching is by file name, so images
the page HTML never had (other colour variants, catalogue images) count as "not in truth".

| Source | Pages with output | Images returned | Other-product images | Wrong images (all this product's own graphics/thumbnails) | Truth product photos found | Other-product sentences leaked | Real product sentences found |
|---|---|---|---|---|---|---|---|
| Shopify `/products/<handle>.js` | 27 / 90 (28 / 100 URLs; 12 Shopify-style URLs 404) | 2–115 per page | 0 | 9 (sizing chart, ingredient graphic, blank, accessory, in-use shot) | 299 / 1,096 | 0 / 16 | 13 / 209 |
| JSON-LD `Product` (from saved HTML; 68 / 93 pages have one, 36 have several) | 63 / 90 | 49 pages: 1 image; 13 pages: 5+ | 0 | 11 (all 100 px thumbnails of the same image) | 84 / 1,526 | 0 / 62 | 4 / 417 |
| Firecrawl `product` format (1 credit/page, no LLM) | 85 / 90 (93 / 100 returned a product; 7 fail-closed with none) | 24 pages: 1; 21 pages: 2–5; 47 pages: 6+ | 0 | 36 (29 are 100–117 px thumbnails on Walmart/Home Depot; rest graphics; 1 Stanley replacement lid) | 285 / 1,858 | 0 / 102 | 14 / 435 |

Notes:
- Amazon via Firecrawl `product`: 132 and 323 images on the two pages checked; they are every
  variant's catalogue images (shades/sizes), not other listings.
- Mack Weldon sweatshirt feed: 10 images (4 swatches + 6 PDP shots), description 457 chars,
  and it does not contain the "Why We Love 'Em" block (that block is about the sweatpant).
- None of the three sources carries the long page copy; the page still has to be read for text.
- Raw outputs: `outputs/structured/{product,shopjs,jsonld}/<key>.json`; per-page scores
  `outputs/structured/score-*.txt` and `*-scored.json`; scripts `scripts/score_struct.py`,
  `scripts/fetch_product.py`.
- Structured sources present in the 93 saved pages: JSON-LD Product 68, og:image 69,
  ShopifyAnalytics.meta 30, __NEXT_DATA__ 14, __NUXT 2, __remixContext 2, any of the
  product-scoped ones 75.

## Tools and what each actually does

| Tool | Method | Handles other-product leak? |
|---|---|---|
| Firecrawl `product` format | no LLM; JSON-LD > microdata > RDFa > embedded state > OpenGraph; Shopify/Woo/Amazon/Walmart extractors; fail-closed | yes when structured data exists; returns nothing otherwise |
| Firecrawl `json` format | LLM over cleaned page with your schema, `onlyMainContent` | no isolation step documented |
| Zyte API `product` | screenshot + HTML neural net; `mainImage`, `images` (description-only images excluded); `probability` | yes, best measured; paid |
| Diffbot Product API | vision + DOM; `primary: true` image; `multipleProducts` flag | yes; paid; no numbers |
| Apify e-commerce tool / actors | platform detection then platform-specific extraction; JSON-LD > OG > microdata > CSS | yes on known platforms |
| Crawl4AI | markdown + PruningContentFilter (text density) + LLM schema extraction | partial (low-density cards often pruned, not guaranteed) |
| Jina Reader | Readability-style strip, `X-Target-Selector`/`X-Remove-Selector` | no unless you pass selectors |
| ScrapingBee / Oxylabs / Nimble / Bright Data | LLM-generated or hand-kept selectors per site, validated across pages | yes per site; needs setup per site |
| trafilatura / Readability | text density for articles | no |

## Engineering patterns that came up repeatedly

- **"Call the LLM last" cascade** (ScrapingBee): embedded JSON -> internal JSON/GraphQL
  endpoints -> selector healing -> LLM writes a per-site selector map once, validated by a
  cross-page audit ("selectors must work on every product page of this site"), then reused.
- **First-match-wins bugs**: JSON-LD parsers that take the first `Product` return a carousel
  item on `ItemList` pages. Fix: collect all Product candidates, match `name` to `<h1>` or
  `@id`/`url` to the canonical URL.
- **Buy-box scoping** (PriceStalker #236): global selectors fell through to carousel/sponsored
  prices; fix was scoping to the buy-box container plus exclusion selectors.
- **Image trust order**: platform JSON `images[0]`/`featured_image` -> JSON-LD `image` ->
  `og:image` -> `itemprop=image` -> first `<img>` in the gallery near the h1, largest
  `srcset`; drop images <~200px rendered, lazy placeholders, and images linked to another
  `/products/` URL.
- **Input format for LLMs**: flat JSON / markdown of the scoped block beats raw HTML
  (NEXT-EVAL 2025, F1 0.957 with flat JSON); markdown is ~80% fewer tokens than HTML.
- **Screenshots**: used as a verifier or fallback, not the primary extractor; DOM + layout
  features beat pixels alone (Klarna, SeeAct).

## Sources
Zyte: zyte.com/blog/automatic-extraction-data-extractor-review, docs.zyte.com/automatic-extraction/product.html,
zyte-common-items.readthedocs.io, docs.zyte.com/zyte-api/usage/extract/custom-attributes.html,
github.com/scrapinghub/product-extraction-benchmark. Diffbot: diffbot.com/docs/extract/product, /analyze.
Firecrawl: docs.firecrawl.dev/features/scrape, firecrawl.dev/tools/product-data-extractor.
Jina: jina.ai/reader. ScrapingBee: scrapingbee.com/blog/ecommerce-scraping-cascade-scrapy-local-llm.
Apify: apify.com/apify/e-commerce-scraping-tool. Crawl4AI: docs.crawl4ai.com/core/markdown-generation.
Shopify Ajax: shopify.dev/docs/api/ajax/reference/product. Kadoa: kadoa.com/blog/using-gpt-4-vision-for-multimodal-web-scraping.
Klarna: arxiv.org/abs/2111.02168, github.com/klarna/product-page-dataset. WCXB: arxiv.org/html/2605.21097.
SWDE/SimpDOM: ar5iv.labs.arxiv.org/html/2101.02415. MarkupLM: arxiv.org/pdf/2110.08518.
WebVoyager: arxiv.org/pdf/2401.13919. SeeAct: arxiv.org/abs/2401.01614. NEXT-EVAL: arxiv.org/pdf/2505.17125.
Trafilatura eval: trafilatura.readthedocs.io/en/stable/evaluation.html. SALT audit: salt.agency/blog/structured-data-implementation-across-top-100-ecommerce-sites.
Web Data Commons: webdatacommons.org/structureddata/2024-12/stats/stats.html. PriceStalker: github.com/mikeknight85/PriceStalker/issues/236.
Ad startups: docs.creatify.ai/api-documentation/url-to-video/link-to-video, omneky.com/api-docs,
help.adcreative.ai/en/articles/9760152-how-to-use-the-product-photo-ads, intercom.help/arcads.
