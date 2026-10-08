# Blind judging brief (text test)

Folder: `/Users/phyonyanwinn/Project/ProJect/adforge/docs/scraping-test/text-test/`

An app makes video adverts from a shop's product page. The advert writer must get only information about **the product sold on that page**; information about any other product can end up in the advert. Different methods picked text (and, on some pages, photos) from each page; you judge every picked item. You are not told which method picked what, and must not try to find out (don't open `outputs/` or `scripts/`).

For each page you are given its key. Look up the product name and page link in `pages.json`. Open `tops/<key>.jpg` (the top of the page, so you can see what it sells; ignore the blue "I<n>" and red "T<n>" labels a script drew on it).

## Text: `text/<key>.json`

A list of sentences `{"id": "S1", "text": "..."}` in random order. Give each one verdict:

- `this_product`: tells something about the product sold on this page: what it is, its name, materials or ingredients, sizes, colours or shades it comes in, fit, dimensions, how to use it, benefits and claims, specs, what's in the box, its warranty, awards or press about it.
- `other_product`: about a different product, even from the same brand, set or collection: another model, flavour, scent or version sold separately, an accessory sold separately, a bundle or kit, a "you may also like" item, a comparison naming the brand's other products. Also a sentence mixing this product and another product in a way that would mislead an advert writer.
- `not_product_info`: nothing about any product: reviews and shopper Q&A, navigation, cart and checkout, prices and discounts, shop-wide shipping and returns, newsletter, cookie and legal text, general brand story that doesn't describe this product.
- `unclear`: you honestly cannot tell after looking at the top of the page.

Be strict with `other_product`: if a sentence names or describes a product that isn't the one on this page, it is `other_product`.

Write `judged/text/<key>.json`: `[{"id": "S1", "verdict": "this_product", "why": "one short phrase"}]`, one entry per sentence, in id order.

## Photos: `sheets/<key>-<k>.jpg` (only if such files exist for the page)

Contact sheets, each photo labelled P1, P2, ... in its top-left corner. Open every sheet with the Read tool and look at every tile. For each label give one verdict:

- `product`: shows the product sold on this page, clearly visible: any angle, close-up, packaging, in use, worn by a model.
- `product_other_variant`: the same product in a different colour, shade, flavour, pattern or size than the one the page shows first (only when the page clearly sells it in several options).
- `other_product`: a different product, even from the same brand, set or collection, or a lookalike with a different name, label, shape or colour. Bundles or kits in which this product is only one item count as `other_product`.
- `no_product`: the product can't be seen: scenery, texture, ingredients, text or a chart, a logo, a person or pet without it.
- `unclear`: you honestly cannot tell. Zoom in first before using this.

Write `judged/photos/<key>.json`: `[{"label": "P1", "verdict": "product", "why": "one short sentence"}]`, one entry per label, in label order.

## When done

Reply with one line per page: key, the count of each text verdict, and (if any) the count of each photo verdict.
