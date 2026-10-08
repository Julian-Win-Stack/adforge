# Blind photo judging brief

Folder: `/Users/phyonyanwinn/Project/ProJect/adforge/docs/scraping-test/screen-test/`

An app makes video adverts from a shop's product page. It must use only photos of **the product sold on that page**. Different methods picked photos from each page; you judge every picked photo. You are not told which method picked what, and must not try to find out (don't open `outputs/` or `scripts/`).

For each page you are given:
- the product name, the page link, and the shop's description;
- `tops/<key>.jpg`: a screenshot of the top of the product page, so you can see what the page sells. Blue "I<n>" and red "T<n>" labels were drawn on it by a script; ignore them;
- `sheets/<key>-<k>.jpg`: contact sheets, each photo labelled P1, P2, ... in its top-left corner.

Open every sheet with the Read tool and look at every tile. For each label give one verdict:

- `product`: shows the product sold on this page, clearly visible: any angle, close-up, packaging, in use, worn by a model.
- `product_other_variant`: the same product in a different colour, shade, flavour, pattern or size than the one the page shows first. (Only use this when the page clearly sells it in several options.)
- `other_product`: a different product, even from the same brand, set or collection, or a lookalike with a different name, label, shape or colour. Bundles or kits in which this product is only one item count as `other_product`.
- `no_product`: the product can't be seen: scenery, texture, ingredients, text or a chart, a logo, a person or pet without it.
- `unclear`: you honestly cannot tell. Zoom in first (Read the sheet again; look carefully) before using this.

Be strict and concrete: name what you see in `why` (one short sentence).

Write one file per page: `judged/<key>.json`, a JSON list with one entry per label on that page's sheets, in label order:

```json
[{"label": "P1", "verdict": "product", "why": "Orange SOS spray bottle, front."}]
```

Every label on every sheet must have exactly one entry. When done, reply with one line per page: key, number of labels, and the count of each verdict.
