# 19 · Titan Foam Fitness Mat

**Result:** finished, 25.78 s, 5 scenes (2 B-roll), no target length, 0 failed calls, ~3 min 5 s wall clock.

## Silent problems

- **Portrait prompt asks for "no products" and then for the product** — the plan's `person_looks` bakes the product into the presenter description, and `draw_person` pastes it after its own "no products" rule, so the portrait prompt contradicts itself and can invent a mat that isn't the page's. `[01:14:00] draw_person in.prompt`: "No text, logos or products in the picture. The person: A fit presenter … standing in a clean, bright home gym with the mat visible." Nothing checks the portrait for an invented product. Severity: med.

- **The sale is claimed but never quantified; the old price was available and unused** — the page states "$89.99 … 10% off (Save $9.02)" and "Limited Time Offer"; the ad says only "It's on sale now for eighty dollars and ninety-seven cents". The sale price and "on sale" are correct (`[01:13:44] plan_ad` line 5, overlay "$80.97 • Free shipping"; page: "Regular price $80.97 $89.99 $80.97 10% off (Save $9.02)", schema `"price": 80.97`), and no "was $X" claim was made, so nothing is wrong. Worth knowing: the flattened page text puts the label "Regular price" directly before "$80.97", so a model reading it literally could take $80.97 as the regular price; it got it right here only because the schema price and "Save $9.02" agree. Severity: low.

- **Page text is ~60% nav/promo junk, repeated 8 times** — "⚡️ FLASH DEAL: $600 Off the Selectorized Pec Fly & Reverse Delt Machine" and "Save Up to 65% During the Fall Savings Event" each appear 8 times in the 14,094-char `page_text` handed to plan_ad, fact_check, and every choose_* call (`[01:13:44] plan_ad in.page_text`). Nothing leaked into the plan, but every model call pays for it and the fact checker has "$600 Off" and "65%" sitting next to the product's own price. Severity: low (cost/risk only).

- **B-roll body is invented from a head-and-shoulders portrait** — scene 2's picture prompt asks for the presenter "stretching on the fully unrolled black mat" and scene 4 for "the presenter's hands and forearms, with the same skin tone and charcoal training-shirt sleeves"; the only reference is a head-and-shoulders portrait (`[01:14:54] choose_broll_picture scene 2 / scene 4`). Body, pose, and pants are made up, which is fine for a person but is also how the person can look different scene to scene. Severity: low.

- **"Mesh" bag vs "nylon" bag** — scene 5's prompt asks for "the matching black vented mesh storage bag" (`[01:14:54] choose_starting_picture scene 5`); the page says "vented nylon storage bag". It is a description read off the photo, not a claim in the ad, and the line says "nylon". Severity: low.

- **Producer over-states what "confirmed" means** — `[01:15:00] SAYS`: "Scene 2's voice line was confirmed word-for-word" and `[01:15:01] SAYS`: "All five voice lines have now been confirmed accurately". It compared the transcript text ("Its 72 by 36-inch surface…") to the line ("seventy-two by thirty-six-inch"); it never hears the audio, and the transcript normalises numbers, so a mispronunciation inside a number ("$80.97", heard as one token 1.1–2.5 s) would not show up. Same words, so no error here; the wording promises more than the check can give. Severity: low.

## Loud failures

None found. Zero `**ERROR**` observations; every step `attempt: 1`, `outcome: succeeded`.

## Waste

- **Music blocks the scene fan-out for 16 s** — `[01:14:35]` the producer calls `create_music` and all 10 scene tools in one turn, but tools run in order and `create_music` is synchronous (15.2 s, `[01:14:38]–[01:14:53]`), so no picture or audio started until `[01:14:54]`. Music could be started last, or in its own step. ~16 s of the 3 min.
- **Five wake-up turns that only say "Scene N is finished"** — `[01:16:08]`, `[01:16:14]`, `[01:16:18]`, `[01:16:20]`, plus `[01:16:38]` which does assemble. Four `produce` calls (~2 s each) whose only output is a one-line status. By design ("Tell the shop owner"), but 4 of the run's 16 producer calls.
- **Photo 2 never considered** — both photos are marked as showing the colour, every choose_* call was shown both and picked photo 1 with no word on photo 2 (known: "Every starting picture used photo 1"). The page's own image list has only one product image, so photo 2 may be a non-product image the page reader kept; it costs an input image on every choose_* call (5 calls).
- Exactly 5 pictures were requested in the same minute, which is OpenAI's input-image limit (5/min): this run avoided the 429s seen in #20 by one image.

## Bottom line

A shop owner would likely be happy: every line and overlay is on the page, the sale price is the one a buyer pays, the voice said every word, and the timing math is exact (cuts match transcripts to the hundredth). Single biggest fix: stop the plan's `person_looks` from carrying the product into a portrait prompt that forbids products (and, cheaper, strip the repeated nav/promo blocks from `page_text` before it is handed to five models).
