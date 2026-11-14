# Human-only checks: what the AI judge can't see

Julian judges these by eye. We tried each one on both models the eval judge can use, GPT (`gpt-6.1-sol` on Azure) and Gemini (`gemini-3.8-flash`). We also tried several wordings. Neither model ever got them right. So these checks are **not part of the eval judge** (spec #644), and the graded cases that test only these faults are **left out of the judge's score**: they count as neither a catch nor a miss. They are listed in `eval-store/judge-cases/human-only.json` in project files.

This is a limit of today's models on our inputs, not something a better prompt has been shown to fix. Try again only when a new model comes out, and run it on the cases below first.

Decided by Julian on Nov 14, 2026: "if we truly cannot, just remove those test cases and even remove that question."

## 1. Person looks pasted onto the background (starting pictures)

**The fault:** the presenter looks cut out and placed onto the room. Julian's words: "the presenter looks like she is in a visual background", "the background is a bit tilted, not the woman".

**His fails:** Bissell Little Green round 11 scene 1, AeroPress round 11 scene 1, Ridge Wallet round 15 scene 1 (starting pictures).

**What we tried:**

| Try | GPT | Gemini |
|---|---|---|
| Question "person not pasted onto the room" (light, shadows, outline, scale, feet meeting the floor) | 0 of 3 | 0 of 3 |
| Five named signs of a pasted person | 0 of 3 | not tried |
| A 1-10 "looks like one photo" score | gave his 3 fails and 10 of his passes the same 9/10 | not tried |
| Named check: background tilted against the person | 0 of 3 | not tried |
| General "anything unnatural" question | 0 of 3 | not tried |
| Full re-run, Nov 14 | 0 of 3 | not run |

Both models described the pictures as "one photo, lighting matches, edges natural".

**What we did:** removed the question (was P3) from the judge. It was never tested on any other fault, so nothing it caught is lost.

## 2. Hair passing through the brush (clips)

**The fault:** strands of hair go through the bristles or the brush body instead of around them.

**His fails:** Revlon One-Step round 12 take 4482, round 13 take 4862.

**What we tried:**

| Try | GPT | Gemini |
|---|---|---|
| First physics question | 0 of 2 | 0 of 2 (video at 5, 10 and 24 frames a second) |
| Tuned physics draft, then several movement wordings ("strands go around tools") | 0 of 2 | not tried |
| Step-by-step physics question (full re-run, Nov 14) | 0 of 2 | not run |

Both models said the hair "slides along the bristles naturally". Our guess, not tested: thin strands crossing a brush are too small and too fast to see, both in frames and in the video.

**What we did:** took the 2 cases out of the score. The physics question stays: in its Nov 14 test it caught 8 of his 10 physics fails, and these 2 were the only misses.

## 3. Wrong hand for the side of the body (clips)

**The fault:** the bottle hangs on her right side but is held by her left hand.

**His fail:** Owala FreeSip baseline-2 scene 2.

**What we tried:**

| Try | GPT | Gemini |
|---|---|---|
| Staging question ("which hand") | 0 of 1 | 0 of 1 (video at 5, 10 and 24 frames a second) |
| "First find the body, then name the hand" | 0 of 2 tries, answers contradicted each other | not tried |
| Full re-run, Nov 14 | 0 of 1 | not run |

Most of her body is out of frame, so neither model could tell left from right.

**What we did:** took the case out of the score. The staging question stays for body position, product part and target.

## 4. Presenter feels like a real person (face and voice)

Removed on Nov 13, 2026 ("AI cannot judge it"). The evidence is in spec #644 under Out of Scope. This was tested on Gemini only, because it is the model that hears the voice.

## Not on this list

Julian also took some cases out as taste calls, not because a model failed. These are worn versus held (Crocs round 12 scene 2), grip while only holding (BlenderBottle round 12 scene 4), and held but not used (Bondi Sands round 12 scene 2). A one-line wording did fix the Crocs case in a test, so it is not proven impossible.

Faults the judge still misses but that haven't been tried on both models yet stay in the score. Examples: the cream colour in Krave round 9 take 3896, and water moving by itself in Chemical Guys round 11 take 4301.
