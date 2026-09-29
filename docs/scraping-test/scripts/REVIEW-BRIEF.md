# Contact-sheet review brief

You are checking a photo filter's decisions. Each page's contact sheet shows every image the
filter downloaded from a shop's product page, as numbered thumbnails:
- K1, K2, ... = KEPT by the filter (green). Caption: size, the AI's verdict and its reason.
- D1, D2, ... = DROPPED by the filter (red). Caption: size and the drop reason (e.g.
  `ai-not-product: ...`, `tiny-120x120`, `smaller-copy-of-N`, `banner-shaped`).
The product name is in the sheet's title. Sheets over 40 tiles continue in `<key>-2.png` etc.

For EVERY numbered tile give one verdict:
- `correct`: kept and it is a photo/graphic of THIS product (same product; a lifestyle shot
  where the product is visible or clearly implied counts), or dropped and it is not.
- `wrongly_kept`: kept, but it is not this product (a different product, a sibling flavour,
  format or product from the same brand, a logo, an icon, UI, an unrelated stock photo).
  Same product in another colour or size: `correct` (the filter is told to keep those).
- `wrongly_dropped`: dropped, but it is a photo/graphic of this product. A `smaller-copy-of-N`
  drop is `correct` if tile N (or the kept copy of that same picture) is on the sheet. A tiny
  drop (`tiny-...`) is `correct` regardless: a picture under 200 px is useless for an ad.

Rules:
- Use the sheet's `<key>.json` (same folder) for the full item list and URLs. Every `id` in
  its `items` must get a verdict. Do not skip any.
- If a thumbnail is unclear, open the full-size file: `<cache-root>/<key>/<file>` from the json.
- Do not call any API. Judge by looking.
- Write `<verdicts-dir>/<key>.json` as:
  {"key": "<key>", "verdicts": {"K1": {"verdict": "correct", "note": ""}, "D1": {"verdict": "wrongly_dropped", "note": "front of the same tub"}, ...}}
  Notes only where the verdict is not `correct` (one short phrase).
- Then run `python3 /tmp/st/check_verdicts.py <sheets-dir> <verdicts-dir>` on your pages
  only if asked; otherwise just make sure every id is present.
- Report back only: pages done, counts of correct / wrongly_kept / wrongly_dropped, and the
  2-3 most notable mistakes (one line each). Do not paste the verdict files.
