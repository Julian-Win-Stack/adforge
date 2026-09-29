# Brief for URL-picking subagents

Goal: real, live product pages (one product each, not category or search pages) for a scraper comparison.

Rules:
- NEVER invent a URL. Every URL must come verbatim from a WebSearch result, a WebFetch of a real page, or a link found on a real page.
- Confirm each URL with curl before listing it:
  curl -sL -o /dev/null --max-time 25 -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36" -H "Accept-Language: en" -w '%{http_code} %{url_effective}\n' "URL"
  - 200 and the final URL is still a product page (not redirected to the homepage or a search page): confirmed_by = "curl-200".
  - 403 / 429 / 503 / 5xx from a big retailer that blocks bots: allowed ONLY if the URL came verbatim from a search result. confirmed_by = "search-result". Record the status code.
  - 404 / 410 / redirect to homepage: drop it, find another.
- Avoid these domains (already in the pilot): naturium.com, vitalproteins.com, anker.com, greatjonesgoods.com, maxbone.com, smartish.com, starface.world, supergoop.com, happiestbaby.com. Avoid Amazon ASIN B0CJZMP7L1.
- Prefer popular, currently sold products (in stock, this year's listing). Spread across the categories asked for.
- Different products should come from different stores where possible (max 2 per store for small brands / others; big retailers listed per store below).

Output: a JSON array written to the file named in your task, each item:
{"key": "<group>-<nn>-<short-slug>", "url": "...", "product": "<product name>", "store": "<store name>", "store_type": "small-brand|big-retailer|amazon|other", "platform": "shopify|woocommerce|bigcommerce|magento|salesforce|custom|unknown", "category": "beauty|supplements|electronics|home|pets|clothing", "confirmed_by": "curl-200|search-result", "curl_status": <int>, "curl_final_url": "..."}

Platform hints: Shopify pages have /products/<slug> and cdn.shopify.com or /cdn/shop/ in the HTML. WooCommerce has /product/<slug> and "woocommerce" in the HTML classes. BigCommerce has "bigcommerce" / stencil in the HTML. Magento has "Magento_" in the HTML. Check with: curl -sL --max-time 25 -A "<same UA>" URL | grep -oi -m3 'cdn.shopify.com\|woocommerce\|bigcommerce\|Magento_\|demandware' 

Return to the caller only: how many confirmed, how many were curl-200 vs search-result, and any group you couldn't fill. Do not paste the list back.
