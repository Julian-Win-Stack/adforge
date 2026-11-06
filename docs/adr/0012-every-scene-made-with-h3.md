# Every scene made by its scene agent, with H3 speaking the line

Extends [0011](0011-one-agent-per-broll-scene.md), rather than replacing it: one scene agent
now owns every scene. **Approved Nov 3 2026; switched on in #339.** This records the live path. Spec and build graph:
[#327](https://github.com/Julian-Win-Stack/adforge/issues/327), tickets #328–#341. Julian
approved the design Nov 3, 20:21–21:37, and all eleven build choices at 22:04 ("yes. i am ok
with all of your picks"). The design record is `handoffs/h3-talking-scenes/spec.md` in the
project files, with `chat-log.md` and `creatify-voice-facts.md` beside it. Times here are those
recorded in the spec.

## What was wrong

HeyGen moves lips but ignores motion text, so the presenter cannot demonstrate the product
while talking. The producer makes those scenes outside the scene agent: no clip judge or
scene-agent repair. In the two Creatify first versions Julian liked, the presenter talked
while doing something with the product, with H3 making the picture, voice and lips together.
Separately designed Inworld speech also needs line-audio steps, voice overlays and overlap
rules to stitch it to silent B-roll.

## Decisions and reasons

- **Every scene is one H3 clip owned by its scene agent.** Reuse 0011's drawing, picture
  check, filming, clip judge, fixes, checked owner quotes, caps, pause and restart for talking
  scenes too. One path lets the presenter demonstrate while talking and lets failed talking
  clips be repaired. This is part of Julian's Nov 3, 20:21–21:37 approval.
- **H3 makes the voice from the same voice words in every clip.** Code inserts the checked
  line exactly as written in speech tags; the agent never rewrites the spoken words. This
  follows Creatify's approach without a voice file. Julian accepted the voice drifting in
  tone, pace and loudness between clips at 21:21. HeyGen and Inworld voice design, line
  speaking and voice measuring are deleted in the contract, not left as unused alternatives.
- **An explicit scene kind replaces inference from the action.** Talking scenes show her
  face while she shows, uses or shows the result of the product. B-roll shows hands and the
  product, face out of frame, with H3 speaking off camera. The planner asks whether she can
  show the line with the product in her hands or on herself at arm's length; if not, it
  records why. This makes the choice gradeable without exposing the kinds to the owner.
  Julian kept the first scene talking at 21:59. The one-action rule stays. Showing the
  product to the lens counts as a talking action (Julian, 20:24); B-roll's holding rule stays.
- **A word check proves H3 said the fact-checked line.** H3 can say a name, price or claim
  wrong. Code tidies capitals, punctuation and numbers; an exact match passes for free.
  Only differences reach a small Azure text model, which fails material changes and accepts
  harmless wording differences. Every check records the line, heard words, verdict and
  code/model source in a developer notice that does not page Sentry. This lets Julian grade
  the checker and measure the share of free matches. It does not replace the fact check
  ([0002](0002-facts-come-only-from-the-page-and-the-user.md)).
- **The word check and product/motion judge both run.** Their answers return together so one
  re-film can fix both; a failure in either fails the clip and counts against the six-clip
  cap. The judge is told she talks, but never checks words or lips. If Scribe or the word
  model gives no answer, keep the clip unchecked, tell the owner to listen and continue:
  Julian, 22:01, "yes. but don't block the things". With quality checks off, both checks are
  off but transcription remains for captions. These build choices were accepted at 22:04.
- **Talking fixes simplify the action before giving up.** Fix the judge's cause, then use a
  simpler action that still proves the line, then show the product to the lens while saying
  it. Since [0013](0013-more-broll-whole-clips.md) (Nov 6), that last step is open only to
  the first and the last scene; a middle scene is B-roll or talks while using the product. This preserves a way to make the scene without HeyGen. A B-roll give-up offers a
  talking scene or an owner photo; a talking give-up offers a photo or another line (22:04).
- **Use one fixed speaking speed, then play clips whole with their own sound.** Without an
  advance voice file, plan at 2.5 words/second: aim for 8–10 seconds per line, enforce 4–14
  seconds, and compute clip seconds as rounded-up speech seconds plus one, bounded 5–15.
  The extra second is an action after her last word. Default to 3 or 4 scenes, or fit the
  owner's target. These build choices were accepted at 22:04. Julian rejected cutting after
  the last word at 21:31. The clip transcript times captions showing the written line;
  music stays at today's level, with no generated music in clips. Voice overlays, early
  cuts and lip-match skips go. *Changed by spec #401 (#407): lines are planned at 2.8
  words/second and a clip lasts its speech seconds plus 0.6, rounded up, bounded 5–15, as
  round 11 spoke at about 2 words/second; the first clip's silence before its first word
  is cut. See `docs/h3-limits.md` §10.*
- **A voice change re-films all scenes after the owner agrees to the cost.** The sound is
  baked into the clip, so changing words cannot change an existing voice. No advance voice
  sample is made. Julian kept re-filming after hearing the alternatives at 22:02; accepted
  at 22:04. This is the cost-approval exception to
  [0010](0010-one-agent-after-the-first-clip.md).
- **Expand, switch, contract.** A per-ad marker keeps each new behavior off until the switch;
  then delete the old providers and branches. Old ads keep their clips, transcripts,
  versions, stored kinds and costs; new clips in old ads use H3 and may sound different
  (22:04). Checkpoint restart still collects a paid H3 render once
  ([0003](0003-checkpoints-not-conversation-replay.md)). The retired-provider exception is
  explicit in [#340](https://github.com/Julian-Win-Stack/adforge/issues/340) and the
  [parent spec](https://github.com/Julian-Win-Stack/adforge/issues/327): a resumed old
  HeyGen step ends as a failed clip when its purpose is unavailable, with a normal failure
  notice and no new submission or payment. Stored clips, provenance and call costs remain
  readable. This avoids a half-built live path while preserving completed work.

## Alternatives and accepted costs

Keeping HeyGen preserves its inability to demonstrate while speaking. Keeping separate Inworld
speech preserves the overlays and timing paths this decision removes. Both are rejected.
H3's voice may drift and it can mispronounce a word; accepted drift is not judged in code, and
the word check catches changed content rather than grading a voice. If the first graded round
shows too much drift, the fallback is **our voice first, then lip sync**; it is not built now.
Voice changing existing clips or applying a new voice only to later clips was offered at
22:02; Julian kept re-filming the whole ad.

Longer clips increase H3 spend, while HeyGen and Inworld spend goes away; Scribe and the small
word model add checks. H3's real price must be checked against a bill before quoting the first
round. The paid run waits until this build merges and round 8 is graded, with its exact cost
first; Julian alone grades it. Those grades may change the fixed speed, the arm's-length rule
or whether long silent endings need cuts. Loosening the one-action rule, music ducking,
automatic lip-sync checks and a separate-voice fallback are outside this build.

## Historical evidence

Superseded provider plans, API replies and original grading records are preserved in
[0012-legacy](0012-legacy/README.md). They describe the retired paths and are not current
operating instructions. Stored clips, provider names, grades and costs remain unchanged.
