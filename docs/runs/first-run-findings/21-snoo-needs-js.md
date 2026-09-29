# 21 · SNOO Smart Sleeper (needs JavaScript)
**Result:** Stopped after `read_page` (14 s, 22:00:08); `check_page` said "unreadable"; no job continued. (known)

## Silent problems
- **The check was right, for the right reason** — the full `page_text` handed to `check_page` is 1,783 characters of header/footer only: social links, "View post on Instagram" ×10, ABOUT/SUPPORT/LEGAL menus, the copyright notice, and the SHOP menu, where "SNOO Smart Sleeper" is the only product mention. No description, no price, no product data for search engines. `[22:00:02] check_page` decision "unreadable", reason "mainly navigation, social links, and footer content … (only a product name in a menu)" matches that text exactly. Severity: none (correct).
- **The producer's ask can't be satisfied** — it told the owner "Please send another public product-page link with the SNOO description and photos" (`[22:00:08]`). This *is* the product's own page; there is no other link. The check's own instructions say the page "was fetched with a plain HTTP request, so nothing that needs JavaScript ran", but neither the check's reason nor the producer says "this page only shows its content with JavaScript", so the owner is sent to look for a link that doesn't exist. And the fallback the producer implies (send description + photos) doesn't exist either: `plan_ad`/`use_photos` refuse until a page has been checked readable (`_with_its_page`). Severity: low (honest dead end, but the guidance is wrong).
- **One photo was declared and never looked at** — `photo_count: 1` in the check handoff; since the check failed nothing was downloaded. Probably the site logo; harmless. Severity: low.

## Loud failures
None found.

## Waste
- 2 `produce` turns + 1 `check_page` (gpt-5-mini, 5.8 s). $0.0242 total, $0.0009 without produce (known). A pre-model heuristic (page text under ~2 KB with no price/description) would have skipped the model call, but the saving is under a cent.

## Bottom line
The stop was correct and honest; the only miss is telling the owner to find "another link" when the real problem is a JavaScript-only page AdForge can't read. Biggest fix: say that plainly (and, longer term, fetch with a JS-capable reader).
