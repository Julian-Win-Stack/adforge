# 17 · Brickell Beard Oil
**Result:** finished ad, 4 scenes, 18.08 s (no target), 01:04:47 → 01:09:44, no errors.

## Silent problems
- **B-roll reference photo contains a different man** — scene 2's picker chose photo 2 because it "clearly shows the bottle open beside a man massaging his beard" (`[01:06:36] choose_broll_picture`), i.e. a lifestyle shot of someone else. The picture model was then given [portrait, photo 2] and told "Show the same man from the first picture… Place the open product bottle from the second picture beside him". Whether the presenter's face survived or blended with the photo's man can't be seen in the trace. Photos 1 and 4 (plain bottle) were available. Severity: low–med (unverified).
- **Portrait prompt carries a scene direction** — `person_looks` ends "…with the bottle visible in each direct-to-camera scene", so `draw_person` (`[01:05:43]`) asked for "No text, logos or products in the picture. The person: … with the bottle visible in each direct-to-camera scene." No scene prompt says "replace" anything, so no evidence a bottle was drawn, unlike #16. Severity: low.
- **Picker added a benefit gesture** — scene 3's prompt: "his other hand lightly touches the lower edge of his beard to suggest softness" (`[01:06:36]`). Harmless, but it's the picture model implying the claim rather than the line saying it, and the clip's motion prompt then asks for "minimal hand movement". Severity: low.
- **Mid-line pause in the price line** — transcript `[01:06:43]`: "$25" ends 1.80 s, "as" starts 2.66 s: a 0.86 s gap inside "twenty-five dollars … as a one-time purchase". Cosmetic. Severity: low.
- **Short ad** — 18 s with no target; 4 scenes of 3.9–5.6 s. Fine against the doc's lessons (Meta 6–15 s, TikTok 21–34 s), just noting the planner picked the low end. Severity: low.

Person suits buyer: yes. Plan `[01:05:28]`: "A well-groomed bearded man in his early 30s, wearing a crisp casual shirt in a bright, modern bathroom"; voice "warm adult male". Consistent across all four scene prompts: "exact face, well-groomed beard, hairstyle, and light blue casual shirt", "same bright, modern bathroom" (scene 3 adds "glass shower"), and the B-roll uses "the same man from the first picture". Nothing drifted.

Facts: every line is on the page. "Shines. Conditions. Moisturizes." → scene 1; "Pour 2-3 drops … massage them upward into your beard, making contact with the skin beneath" → scene 2 and overlay "2–3 drops"; "Lightweight formula won't leave your beard heavy or greasy" / "Softens" → scene 3; "1 oz", "$25.00", "One-time purchase price" → scene 4 and overlay "$25 one-time purchase". The $22.50 subscription question (known) is by design in `PLAN_INSTRUCTIONS`. Transcript "$25" for spoken "twenty-five dollars" gives a caption that matches the overlay.

## Loud failures
None found.

## Waste
- 23 producer turns (49 s), 10 narration only, e.g. `[01:09:11]`, `[01:09:14]`, `[01:09:21]` each just report one clip finishing.
- 4 pictures fired at once — under the 5/min limit, no 429s.
- Timing consistent: cuts = last word + 0.1 s; spans sum to 18.08; silent clip asked for 5.17 s (4.8 s audio) and came back usable; music 22 s > 18.08 s. Clips took 113–145 s each, the bulk of the 5 min.

## Bottom line
A shop owner would likely be happy: right presenter for the buyer, right price, every claim from the page, nothing broke. Biggest fix: stop the B-roll picker handing the image model a lifestyle photo with another person's face when a plain product photo exists.
