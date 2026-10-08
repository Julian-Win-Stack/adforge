# Second run: what the traces show went silently wrong (ads 01–09)

Method as in [first-run-trace-findings.md](../first-run-trace-findings.md). Observations were
pulled per chat session with `GET /api/public/v2/observations?sessionId=<chat session id>`
(the trace session id equals the chat session id; pages with `meta.cursor`) by
[fetch-traces.py](fetch-traces.py), rendered with [render-traces.py](render-traces.py), and read in full.
Price-line speech ends were measured with `ffmpeg silencedetect` in the worker. Captions were
recomputed with `assembly.timed_script` on the traced transcripts. Voice pitch was a rough
autocorrelation median. Portraits were looked at only to see whether a product was in them.

Cause tags: page, plan, picture, voice, clip, assembly, producer.

| # | Product | Result | Worst silent problem | Cause | Sev |
|---|---------|--------|----------------------|-------|-----|
| 01 | Naturium serum | 29.36 s | Nothing wrong a viewer sees. The price cut has only 0.11 s to spare | assembly | low |
| 02 | Merit Flush Balm | 26.64 s | "thirty dollars" spelled out still merged to `$30`. Saved by 0.09 s | assembly | low |
| 03 | Vital Proteins | 24.56 s, after size question | 5.4 s short of 30 s, called "fits your 30-second target" | plan | med |
| 04 | Mack Weldon | 24.5 s | Sweatpant cross-sell in a line again. Presenter holds the sweatshirt by his face instead of wearing it | page / plan | med |
| 05 | Steve Madden bag | 24.28 s | 5.7 s short of 30 s, unflagged | plan | med |
| 06 | Anker power bank | 31.04 s, after USB-C question | The woman's voice measures 136–145 Hz (male range). Producer says visuals "avoid" USB-C when nothing did | voice / producer | med |
| 07 | Great Jones Dutch Baby | 24.06 s | 11 lb cast-iron pot planned as "handheld", held beside her face in 3 scenes. "Three quick cuts" in one clip again | plan | med |
| 08 | Molly's Suds | 15.48 s, after price question | Clean (all three first-run defects gone) | – | low |
| 09 | Momofuku (6 s) | 5.7 s | Clean. Producer misquotes the transcript | producer | low |

## Across ads 01–09

1. **Short of target with no warning**: 4 of the 8 ads that had a target came in 3.4–5.7 s short (02, 03, 04, 05). `fits_target` is still one-sided. *plan, med.*
2. **Wrong pose because no size fits**: 2 (04 a sweatshirt as "handheld", never worn; 07 an 11 lb pot as "handheld"). *plan, med.*
3. **Several shots asked of one start-frame clip**: 2 (07 "three quick cuts" and three copies of the pot in one frame; 05 five items placed in 5.56 s). *plan/picture, low–med.*
4. **Price-line margin is thin**: 6 ads end a talking scene on a number (01, 02, 03, 04, 06, 08). Speech runs 0.41–0.54 s past the token; the 0.6 s margin leaves 0.06–0.19 s. None was cut. Spelling the number out (02) doesn't stop the merge. *assembly, low (risk).*
5. **Last caption ends 0.4–0.5 s before the last word**: 7 (every price-last line, plus 09). *assembly, low.*
6. **The voice may not match the presenter's gender**: 1 likely (06, 136–145 Hz) and 2 borderline (01 ~157–163 Hz, 09 ~155 Hz). Needs listening. *voice, med.*
7. **Cross-sell text passed as fact**: 1 (04). *page.*
8. **Duplicate photos** (same file at another size): 7 of 9. *page, low.*
9. **An em-dash glues two caption words** ("10K—a", "bowl—just"): 2. *assembly, low.*

**Known, unfixed:**
- Image 429: 5 wasted calls in 3 ads (02 ×1, 06 ×2, 07 ×2).
- Producer over-claiming: 3 (01 "verified word-for-word", 06 "avoids the USB-C detail", 09 misquote).
- Producer stalling: 0.
- A line changed after its fact check: 0 (never tried).

**Questions:** 3 asked (03 size/price, 06 a USB-C conflict, 08 one-time vs subscription), all reasonable. The price rule ("different prices to choose between (such as a single item, a pack and a subscription)") is unchanged since 2026-09-21. No planner prompt change since the first run touches price. So asking vs choosing silently is the model's own variation. #03 asked about size, not subscription; its page_text this time carried the 25.7 oz / $39.99 option. See [08](08-mollys-suds-cleaner.md).

## First-run fixes

| Fix | Holds? |
|-----|--------|
| Price cut (NUMBER_MARGIN_SECONDS 0.6) | Yes, 6/6 price-last talking lines; only 0.06–0.19 s to spare |
| No fake product in the portrait | Yes, 9/9 |
| Captions from the script's words | Yes, 9/9 (e.g. "MERIT", "caramel-y", "100%", "10,000mAh", "98%") |
| B-roll keeps its whole clip | Yes, 17 B-rolls play their whole voiced clip (= audio length, by design) |
| 2 s length allowance | Yes at the top (01, 06 passed over 30). **Not two-sided**: 4 ads 3–6 s short passed |
| Pose by product size | Code holds; **the plan's size is wrong for 2 of 9** (garment, 11 lb pot) |
| Voice gender | In the handoff 9/9; in the audio, 06 doubtful |
| ASCII voice descriptions | Not tested: no description had a non-ASCII character (samples with ’ and — were accepted) |
| Talking clips on HeyGen Avatar IV | Yes, 27 talking clips, each as long as its audio |
