# 05 · Steve Madden Kenzo Bag Gold
**Result:** Finished ad, 25.68 s (30 s asked), 5 scenes, 59 model calls, 0 failed.

## Silent problems
- **Lines: nothing invented about the bag** — (checked, OK). Product name shortened to "Steve Madden KENZO BAG", dropping "GOLD" so the colour isn't spoken (correct per the rules). "sophisticated clutch", "evening occasions or everyday elegance", "sleek metal frame", "stone kiss-lock detail", "removable crossbody strap" / "hands-free", the five essentials, "$88", "Duster bag included" are all on the page. Only scene 2's "a polished finishing touch" isn't page wording (page: "adds a touch of luxury") — puffery. Severity: low.
- **Picture prompts add looks the page never states** — `[23:54:52] choose_starting_picture` scene 3: "gold metal frame, kiss-lock, chain strap … lifting or displaying the attached gold crossbody chain"; the page says only "Removable crossbody strap" (23–24 in), never "chain" or the frame's colour. `[23:54:52] choose_broll_picture` scene 4: "preserving its exact … frame, kiss-lock, lining, interior details". These come from photos 3 and 4 (allowed for how it looks) but are unverifiable from the trace; if a photo was misread the ad shows hardware the bag doesn't have. No size or extra hardware invented. Severity: low.
- **Scene 4 B-roll: five props placed "one at a time" in a clip that then loses its last 1.2 s** — `[23:55:35] make_clip seconds 7.0` (Boreal ≈7.04) for 6.28 s audio; kept 5.88 s (assemble 16.74→22.62) → ≈1.16 s of the motion cut (17%), the largest cut in this run. Motion prompt: "Her hands place the phone, small wallet, keys, small headphones, and compact makeup into the open bag one at a time" — ~1.2 s per item and the last item(s) are what's cut. The picture also asks the image model to invent five objects and their sizes inside a 9.5×5.5×3 in bag ("a picture is a claim"). 0.40 s of the cut is trailing silence, which 71dec51 does not remove. Severity: med.
- **Scene 2 B-roll cut** — asked 6.0 for 5.84 s audio, kept 5.40 (6.46→11.86) → ≈0.64 s of "glide … toward the stone kiss-lock" cut (11%); 0.44 s of it trailing silence. Severity: low.
- **Ad 4.3 s under the 30 s asked** — 68 words at 2.6 words/s ≈ 26 s; the length check only catches over-length. Producer `[23:54:27]`: "the script fits your 30-second target"; final "25.68 seconds" with no mention of the ask. Severity: med.
- **Possible sold-out state passed in silence** — page_text has "Select a Size / ONESZ / Email me when available" (twice, Shopify's sold-out button) beside "Add to cart"; the schema says InStock. In #10/#11/#15 the same kind of conflict became a question (known); here neither `plan_ad` nor `fact_check` mentioned it, so the behaviour is inconsistent. The ad makes no stock claim, so nothing spoken is wrong. Severity: low.
- **Portrait prompt contradicts itself** — person_looks ends "holding the bag in a softly lit neutral dressing area" and is pasted into `[23:53:53] draw_person` after "No text, logos or products in the picture". If the portrait got an invented bag, scenes 1/3/5 ("the woman from the first image") inherit it. Severity: low.
- **Price token timing** — `[23:55:00] transcribe_line` scene 5: "$88" 0.26→0.70 (0.44 s) then a 0.80 s gap before "and"; the transcriber's normalised-number timings can't confirm how the price was read (same blind spot as 04). Severity: low.
- **Conversation re-orders between producer turns** — scene 1's picture notice appears as new at `[23:55:23]` and again, after freshly inserted "[Attached 1 picture]" entries, at `[23:55:25]`; scene 3's at `[23:55:25]` and `[23:55:28]`. Severity: low.

## Loud failures
None found. The duster bag not being shown was said honestly by the picture step and relayed by the producer `[23:55:28]` (known).

## Waste
- 22 producer turns, 9 with no tool call; `produce` ≈ $0.44 of $1.34 (33%).
- Clips collected in 32–44 s each (vs 92–125 s in 04 and 06); whole run 23:53:24→23:56:22 (3 min). No repeats, retries or idle gaps.
- page_text ~14k chars, mostly two copies of the mega-menu plus "You may also like" with six other bags' names ("EVELYN BAG BLACK, MAKIA BAG BLACK …"), sent to 8 model calls.

## Bottom line
The owner would likely be happy: every spoken claim and the price are on the page and the unpictured duster bag was honestly left out. Biggest fix: stop trimming the end of B-roll motion (rounding + trailing silence took 1.2 s off the packing shot) and keep "one at a time" motion prompts to what fits the line.
