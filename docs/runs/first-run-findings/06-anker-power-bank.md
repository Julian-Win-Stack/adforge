# 06 · Anker 313 Power Bank (PowerCore 10K)
**Result:** Finished ad, 30.16 s (30 s asked), 6 scenes, 67 model calls, 0 failed.

## Silent problems
- **Numeric specs in lines and overlays: all correct** — (checked, OK). "10,000mAh" / "10,000mAh capacity" (page "Total Capacity: 10,000mAh"), "charge a phone up to three times", "up to 2.4 amps" / "Up to 2.4A" (page "up to 2.4 A"), "$25.99", "30-day money-back guarantee", "palm or pocket", trickle mode for "earphones and Bluetooth speakers": each on the page. Not mentioned: 15W max USB-C output, the three ports, 7.5 oz. Caveats omitted, not contradicted: "Does not support Qualcomm Quick Charge", "USB-C cable, Lightning cable, and AC adapter not included", "The USB-C input port has no output function".
- **Scene 3 B-roll loses a quarter of its motion, including the action the line is about** — `[23:59:06] make_clip seconds 4.0` (Boreal ≈4.04) for 3.46 s audio; kept 3.02 s (assemble 11.46→14.48) → ≈1.0 s cut (25%). Motion: "The hand holds the power bank briefly, then naturally lowers it and slips it into the dark overshirt pocket" — the pocket is the end. 0.44 s of the cut is trailing silence, which survives 71dec51. Severity: med/high.
- **Scene 5 B-roll: its second action is cut** — asked 7.0 for 6.18 s audio, kept 5.76 (19.64→25.4) → ≈1.28 s cut (18%). Motion: "presses the power bank's circular button, then smoothly plugs the cable into the power bank" — the plug-in is last. Scene 2: asked 5.0 for 4.82, kept 4.36 (7.1→11.46) → ≈0.68 s cut (14%) of a shot where "Both objects and the cable remain still" anyway. Severity: med.
- **Every scene, all three B-rolls included, is made from photo 1** — six `choose_*_picture` calls `[23:58:24–26]` all give "clearest unobstructed view"-type reasons; photos 2–6 (the schema's -02…-06 detail shots) unused, so the "Tech and gadgets" guide's "design close-up, the feature working in hands" is staged from one hero shot. (Known pattern for #24/#19/#01; not noted for #06.) Severity: med (quality).
- **Cables shown; page says none is included** — scene 2 prompt: "connected to the phone by a charging cable"; scene 5: "holding a cable ready to connect the power bank to a pair of wireless earphones in their charging case". Page: "USB-C cable, Lightning cable, and AC adapter not included." Not an inclusion claim, but the ad never says so. Scene 5's picture prompt also forbids "indicators" while the page's trickle mode is signalled by "the LED indicator turns green". Severity: low.
- **"shows" altered in the prompt** — scene 3's shows: "slips it into a coat pocket"; prompt and motion: "overshirt pocket". Severity: low.
- **Brand names split in the captions** — `[23:58:29] transcribe_line` scene 4 heard "Power IQ and Voltage Boost"; captions are built from heard words, so the ad prints "Power IQ" / "Voltage Boost", not PowerIQ / VoltageBoost. Producer `[23:58:33]`: "Scenes 1, 3, and 4 were spoken correctly" — true of the sound, not the on-screen text. (Scene 2's "10,000mAh" was heard "10,000 milliamp hours": a correct reading.) Severity: low.
- **Portrait prompt contradicts itself; gender drifts** — person_looks "… holding the power bank clearly on camera" inside `[23:57:23] draw_person` "No text, logos or products in the picture". Plan says "A friendly adult … they"; scene 6's prompt says "him/he" (decided by the portrait). Severity: low.
- **4 of the 10 "usable" photos are the white variant** — the schema lists 6 black then 6 white images; the plan marks 1–6 black; photos 7–10 were downloaded, stored and sent to `plan_ad` (10 images) but can never be used for a black ad, and the producer told the owner `[23:57:02]` "10 usable photos". Severity: low.
- **Conversation re-orders between producer turns** — scene 1's picture notice shown as new at `[23:58:56]` and `[23:58:58]`; scene 4's at `[23:58:58]`/`[23:59:00]`; scene 6's at `[23:59:00]`/`[23:59:03]`; scene 3's at `[23:59:03]`/`[23:59:06]` — each time "[Attached 1 picture]" entries were inserted ahead of older notices. Severity: low.

## Loud failures
None found.

## Waste
- 24 producer turns, 9 with no tool call; `produce` ≈ $0.52 of $1.58 (33%).
- Boreal collect 102–125 s per clip; whole run 23:56:52→00:01:12 (4.3 min), ~2.1 min waiting on clips. No repeats or retries.
- page_text ~10.6k chars including the Affirm legal text twice, six reviewer quotes about other products ("Anker Nano Pro will work flawlessly …" ×3), and membership upsells; sent to 9 model calls.

## Bottom line
The best of the three: every number matches the page and it lands on 30.16 s for a 30 s ask. The B-roll is the weak spot — the "slips it into the pocket" and "plugs in the cable" endings are cut off and every shot starts from the same hero photo; fix the end-of-motion trimming (trailing silence as well as rounding) first.
