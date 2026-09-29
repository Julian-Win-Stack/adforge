# Label check brief

`labels.json` holds, per page, sentences that Firecrawl's markdown had and the current scraper's
text lacked, each labelled by gpt-5-mini as `product_info` or `noise`:

- `product_info`: DESCRIBES this product to a shopper: what it is, ingredients or materials,
  sizes, dimensions, weight, colours, specs, features, directions, benefits and claims, clinical
  results, warnings, box contents, compatibility, warranty, certifications, an FAQ answer about it.
- `noise`: anything else: bare prices, discounts, stock or delivery lines, quantity pickers, buy
  buttons and tab names, navigation, cookie banners, cart UI, promos, newsletter sign-ups,
  customer reviews and Q&A, anything about OTHER products (recommendations, other products'
  prices), footer links, legal text, site help text, template code, broken fragments.
  Test: would an ad writer need this sentence to describe the product correctly? If not, noise.

Check EVERY label on your pages. Write `<checks-dir>/<key>.json`:
  {"key": "<key>", "checks": {"1": "right", "2": "wrong", ...}}
with one entry for every sentence number `n` on that page (the value is "right" or "wrong";
"wrong" means the label should be the other one). Add `"notes": {"12": "why"}` for wrongs only.
Do not call any API. Report back only: pages done, right/wrong counts per label, and the
3 most notable wrong labels (one line each).
