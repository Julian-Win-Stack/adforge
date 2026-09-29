# First run: what the traces show went silently wrong

Read against [first-run.md](first-run.md) (the run log). That log records what broke loudly; this
records what the Langfuse traces show went wrong *without* an error, per ad and across ads.
Per-ad detail with timestamps and quotes: [first-run-findings/](first-run-findings/).

How it was done: all 3,805 observations of the 27 sessions were pulled from Langfuse
(`GET /api/public/v2/observations`, `fields=core,basic,io,metadata,metrics,model,scores`), rendered
to a readable transcript per session ([render-traces.py](first-run-findings/render-traces.py)), and
read in full. Where the trace alone couldn't settle a question, the voice files in the worker's
`/app/media` were measured with `ffmpeg silencedetect`, and the `adforge` database was queried.

## Across the run (new; not in first-run.md)

Ordered by how much they hurt a finished ad.

1. **The price line is cut off mid-word in 13 lines across 12 finished ads.** When the voice says
   "sixty dollars" or "fourteen ninety-nine", the transcriber (ElevenLabs Scribe) writes it as one
   token, `$60` / `$14.99`, whose `end` time is only the *first* spoken word's. `assembly.cuts` ends
   each clip at `words[-1].end + 0.1`, so the clip stops before "dollars" / "ninety-nine" is said.
   Measured against the real audio: 0.31–0.51 s of speech lost in 02, 03, 08, 09, 11, 13, 14, 16,
   20, 23 (both), 01 (first and 3rd). Lines where Scribe spelled the number out ("fifty dollars",
   #15) are cut correctly. The price line is usually the last line of the ad. *High.*
2. **Every presenter portrait is drawn holding an invented product.** `plan_ad`'s `person_looks`
   nearly always names the product ("holding the bottle toward camera", "with the mat visible") and
   is pasted into `PORTRAIT_PROMPT` right after "No text, logos or products in the picture"
   ([work.py:245](../../backend/jobs/work.py:245)). Scene prompts then have to say "Replace the
   bottle she is holding". Seen in 01, 02, 08, 09, 10, 16, 19, 23 and others; in #10 the portrait's
   invented dog was carried into the B-roll. The shop owner approves a portrait with a fake product.
   *High.*
3. **Captions print whatever the transcriber heard, and nobody checks it against the line.**
   "Mooshee" (#16), "Gripmonk" (#12), "Beard Brand Foxhunt" (#11) went into on-screen captions.
   The producer called #16 "matches", and in #11 said it would remake the line, was refused, then
   told the owner it was a "pronunciation" matter. *High.*
4. **B-roll actions never finish.** The motion prompt describes an action for the whole clip, but
   the clip is trimmed to the last spoken word + 0.1 s, and TTS audio has ~0.4–0.5 s of trailing
   silence. So every "slips it into the pocket" / "drizzle" / "places five items" loses its last
   0.4–1.2 s, *independently* of the whole-second rounding that `71dec51` fixed. *Medium.*
5. **The length check is one-sided.** `fits_target` only fails when the script is too *long*
   ([checks.py:110](../../backend/jobs/checks.py:110)), so every "30-second" ask came out 24–27 s and
   the producer still said "fits your 30-second target". The estimate (words ÷ words-per-second from
   one continuous read) is also off by 0.7–2.5 s — about the size of the 1 s allowance — which in #08
   forced a needless "shorten?" question. *Medium.*
6. **The fact check passes claims the page only makes elsewhere or with a disclaimer.** #04: two
   lines describe the Ace Sweatpant from a cross-sell block. #03: "supports skin, hair, nails, bones,
   joints" with the page's FDA footnote dropped. #20: "clinically tested to improve skin clarity"
   without the 33-women/4-week footnote. #23: "everyday hydration companion" (not on the page).
   #10: page says both "rubber" and "silicone", unflagged. *Medium.*
7. **`colour_photos` is the only gate on which photos a scene can use.** #20: only the award-badged
   marketing image reached all six scenes; the real texture photos were never offered, so the
   gel-on-face B-roll was invented from words. #17: the B-roll got a lifestyle photo of a different
   man when plain bottle photos existed. #16: an infographic photo was picked for the text on it.
   *Medium.*
8. **Size-blind prompts.** Every talking-scene prompt says "holds the product beside their face":
   a 425 lb treadmill held at chest height in 3 of 5 scenes (#24), a 17 lb chair hoisted in one scene
   and on the floor in others (#14), studs "a few pixels" on an open palm (#13). *Medium.*
9. **Image 429s are self-inflicted and the backoff is too short.** All scenes' pictures fire at
   once into a 5-images/min limit; the gateway retries at +2 s and +9 s against an API that says
   "try again in 12s", so try 3 usually fails too (#11 scene 6, #15 scene 2, #23 scene 3). *Cost.*
10. **The producer stops with no tool call and no question.** #15 ended a turn with `calls: []`
    and sat 68 s until the user nudged it, then invented a reason ("the presenter tool only allows
    one creation"). Same input in #16/#17 went straight on: non-deterministic. *Medium.*
11. **The producer over-claims.** "Verified word for word", "confirmed exactly", "restarted from
    scratch" (#18, when `read_page` was a no-op), "server error" for our own 400 (#01 redo). The
    "[Attached 1 picture]" messages are the model copying the app's placeholder text back. *Low–med.*
12. **`page_text` carries nav, promo and cross-sell junk** into five model calls per ad (#19: ~60%
    of the text is "$600 Off the Selectorized Pec Fly" ×8). *Cost, and cause of 6.*
13. **When `plan_ad` returns an unreadable reply, nothing keeps the reply.** #18's five failures
    store only pydantic's truncated message, so what the planner actually wrote can't be known.
14. **Producer turns are ~35–43% of each ad's cost**, mostly wake-ups that only relay "scene X is
    finished".

## Per ad

| # | Product | Result | Worst silent problem | Sev |
|---|---------|--------|----------------------|-----|
| 21 | SNOO | stopped, correct | Asks for "another public product-page link" that can't exist; should say the page needs JavaScript | low |
| 22 | Nécessaire collection | stopped, correct | Could pass the product names back and ask "which one?" | low |
| 23 | Stanley (first) | failed (known) | Portrait held an invented tumbler; voice designed with no gender vs a female portrait; "Get it new for…" copies Amazon's Buy-New label; B-roll invents the lid mechanism | high |
| 23 | Stanley (redo) | 28.32 s | "$37.75" cut mid-word; scene 2 B-roll is a motionless tumbler | high |
| 24 | Life Fitness treadmill | 24.52 s | Man holds a full-size commercial treadmill at chest height in 3 of 5 scenes | high |
| 01 | Naturium (first) | failed (known) | Invented serum-on-skin B-roll the planner should have asked about; fact check passed it | high |
| 01 | Naturium (redo) | failed (known) | Told the owner "server error" for our own 400; portrait paid for but never shown | med |
| 01 | Naturium (3rd) | 28.66 s | Two back-to-back B-rolls of the same still bottle = 45% of the ad; "$27" cut mid-word; caption "Multi Active" loses the hyphen | med |
| 02 | Merit Flush Balm | 27.06 s | "thirty dollars" cut after "thirty"; portrait held an invented jar | high |
| 03 | Vital Proteins | 29.32 s | Footnoted health claim spoken flat with overlay "Everyday support"; "$27" cut | med |
| 04 | Mack Weldon | 24.46 s | Two lines describe the *Ace Sweatpant* (cross-sell block); fact check passed all five | high |
| 05 | Steve Madden bag | 25.68 s | Clean facts; packing B-roll loses 1.2 s of its motion | low |
| 06 | Anker power bank | 30.16 s | Every number right; pocket/cable B-roll endings cut; all six scenes from one hero photo | low |
| 07 | Great Jones | 25.72 s | Scene 5 asks for "three quick shots" in one 5 s start-frame clip; every B-roll trimmed to last word | med |
| 08 | Molly's Suds | 14.4 s | Needless shorten question (estimate 16.1 s, real 14.4 s); "$9.99" cut; portrait held a fake bottle | med |
| 09 | Momofuku | 5.34 s | Drizzle shot cut from 3.17 s to 2.0 s; "$10" cut; producer said audio "confirmed exactly" | med |
| 10 | maxbone | 21.9 s | 11 refusals were output-stage moderation on the same portrait+photo pair; retries reworded but never changed photo/pose, ~$0.85 burned; portrait's invented dog reused in B-roll | high |
| 11 | Beardbrand | 32.16 s | Caption "Beard Brand Foxhunt"; producer hid the refused remake as a "pronunciation" matter; first-run.md's "line was remade" is wrong | high |
| 12 | Smartish | 23.46 s | Caption prints "Gripmonk" (known cause, new consequence: captions come from the transcript) | high |
| 13 | gorjana studs | 11.2 s | Studs a few pixels on a palm; price scene lasts 0.74 s and cuts "dollars" | high |
| 14 | Branch chair | 32.22 s | Chair hoisted to chest in scene 5, on the floor in 1 and 6; "$199" cut | med |
| 15 | Nécessaire kit | 28 s | Producer stopped with no call and no question for 68 s, then invented a tool limit | med |
| 16 | Mushie | 28.28 s | First caption reads "Meet the Mooshee"; producer called it "matches"; "$14.99" cut | high |
| 17 | Brickell | 18.08 s | Clean. B-roll reference was a lifestyle photo of a different man | low |
| 18 | Starface | failed (known) | Name check compared `hydro-stars® + big yellow` to spoken lines — unsatisfiable; raw plans not kept; producer claimed a restart that didn't happen | high |
| 19 | Titan mat | 25.78 s | Clean. Portrait prompt had "with the mat visible"; page_text 60% promo junk | low |
| 20 | Supergoop | 29.34 s | All six scenes from one award-badged image; texture photos never offered; clinical claim without footnote; "$38" cut | med |

Ads a shop owner would likely accept as-is: 05, 06, 17, 19 (and 07, 09 with small fixes). The rest
each carry at least one thing a viewer would notice.

## Corrections to first-run.md

- #11: "Scene 1's line was remade" — it was not; the remake was refused ("audio already made")
  and the caption reads "Beard Brand Foxhunt".
- #12's mispronunciation is in the *caption* too, not only the voice: captions are built from
  the transcript's words.

## Suggested order of fixes

1. Don't end a cut at a merged `$N` token; fall back to the audio's real end (or spell numbers out
   for the transcriber). Fixes 12 ads' price lines.
2. Strip the product from `person_looks` before `PORTRAIT_PROMPT` (or have the planner give
   looks and product-handling separately).
3. Treat a transcript that spells the product name differently from the plan as a failed step,
   and let a line be re-voiced after checks pass.
4. Cut B-roll at the audio's end minus trailing silence, not at the last word, and give the
   motion prompt the clip's real playable length.
5. Two-sided length check with a better estimate; size-aware "holds / rests a hand on / stands
   beside" in picture and motion prompts.
6. Rate-limit image edits to the API's 5/min and honour its retry-after.
