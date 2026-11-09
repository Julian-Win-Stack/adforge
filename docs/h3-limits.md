# H3 limits and how we work around them

Boreal-H3 (Creatify's video model, "H3") films talking and B-roll clips, with their own speech and sound. It has habits that break ads. This doc lists each one, how AdForge gets around it, the grades that proved it, and whether the fix is in code.

**Keep it up to date.** When a grade shows a new H3 failure, or a fix lands or is dropped, add or edit an entry here in the same PR. Each entry follows the same pattern: **What H3 does**, **How we get around it** (with `file:function`), **Proof** (rounds and clip ids from Julian's grades), **Status** (in code / prompt rule / unsolved).

Rules proven by grades live in `docs/agents/proven-broll-rules.md` and change only with Julian's OK. This doc explains *why* those rules exist and where the code enforces them.

Paths below are under `backend/`.

## 1. It copies whatever is in the pictures

- **What H3 does:** It takes more from the pictures than the product's look: a shop photo's leg, sneaker, park path and drop height, the background, props, a second brush, another product, a stranger's face. Writing "photos only show the look" did not stop it.
- **How we get around it:**
  - Every clip is filmed from a start picture we draw and check, never from raw shop photos (`agents/scene_agent/film.py:Film.run` refuses a picture that hasn't passed its check).
  - Strangers' faces are swapped for our presenter before a photo is used (`jobs/face_swap.py`, `agents/scene_agent/draw_starting_picture.py:swapped`).
  - We send less, and send it clean: only the target product, cut out (`jobs/cut_out.py`).
- **Proof:** Oct 7 B-roll prompts. Krave stranger face (Oct 30). R7 chipolo s3, R7 beautyblender s4. Hand round 2 garject-B: extra photos beat the page video and H3 made up a use.
- **Status:** In code. Proven rule: "Photos are for looks only".

## 2. It invents parts and sides it has never seen

- **What H3 does:** It adds a third handle, an extra button or a second screen, puts bristles on both sides of a brush or turns it into two brushes, and adds a cable that doesn't exist.
- **How we get around it:**
  - The start picture pins the product's exact look in frame 1.
  - The video prompt keeps a lock line before its sound instructions: "The [product] keeps its exact shape, colour and printed words." After a failed clip the line grows to name every part that must stay (`agents/scene_forms.py:VideoForm.lock_line`).
  - In talking scenes this is the prompt's only shape lock: only the product is locked. The scene agent is told to let her face, head, shoulders and free hand move, never to hold still, keep the same grip, stay anchored or freeze (`agents/scene_agent/instructions.py:INSTRUCTIONS`, `VideoForm.beats`). The starting-picture and PRODUCT checks remain.
  - The PRODUCT check in the clip judge looks at 10 frames a second and fails a clip whose product changed (`jobs/clip_judge.py:judge_clip`).
- **Proof:** Hand round 1 wetbrush v3/v4, hand round 2 garject-A/A2, R5 cocolab s2 (3 versions), R7 c4.
- **Status:** In code. History: reference mode (example pictures, no start picture) went in Oct 16 and was dropped Oct 24-27. It came back as start-frame mode on Nov 2 (ADR 0011) because reference mode redrew the product in every clip.

## 3. It flips the product to a side it never saw (phones)

- **What H3 does:** A phone turns and shows cameras on both sides, or two fronts.
- **How we get around it:**
  - The camera box, the picture form, the scene agent and the planner all say "Never show a side or part no photo shows" (`jobs/picture_rules.py:SIDE_RULE`), and the picture check fails a starting picture that does (ticket #406).
  - The planner never plans a hand turning, flipping or spinning the product (`jobs/planning.py`).
  - A talking scene's `VideoForm.product_move` asks for one small move tied to a sentence, then back, with the photographed side staying toward the camera. It is the scene's one product action and is not repeated in beats. Holding her body still is no longer the workaround (spec #447); the side rule and product lock remain.
  - The clip judge's PRODUCT check catches a flip.
- **Proof:** R3: 3 of 3 phone clips flipped. Reference-mode test: 5 of 12. New wording for the phone-pocket scene: 3 of 3 passed.
- **Status:** In code. Proven rule: "Never show a side or part no photo shows" (widened from "never flip the product to a side no photo shows", Julian's OK Nov 6). Drops and tumbles still flip; we don't film them.

## 4. It makes up how the product is used, or adds steps

- **What H3 does:** It pours from a second bottle, scrubs the toilet seat, uses floss like a stick, pulls a pore strip off with a tool, or adds a twist nobody asked for. This is the most common fail in nearly every round.
- **How we get around it:**
  - One product action per clip (it moves the product or changes its shape or state; face, head, free-hand and camera moves don't count), and the voice carries the other steps.
  - A two-state claim is two clips joined by a cut.
  - Show the action the way the page shows it. A page-video piece is sent as a movement reference (`agents/scene_agent/reference.py:pieces_for`).
  - Don't film actions H3 can't do: R9 almost all passed "because we didn't film the risky and hard actions."
- **Proof:** R4 #8 s2/s3, hand round 1 cocofloss-s4-v2, R7 n01 s3, R7 n08 s3, R9 clip 3936.
- **Status:** Prompt rules plus proven rules. Some of these fails start in our planner or prompt, not in H3.

## 5. Its physics are wrong, and results appear where nothing touched

- **What H3 does:** Gel thickens on its own, foundation spreads past the sponge, a stain vanishes where nothing touched it, a scoop refills, a shirt changes colour, a toilet flushes before the button is pressed. It also makes up what contents look like (foam, foundation, serum).
- **How we get around it:**
  - Rule: a result shows only where the product touched.
  - From the first try, the scene agent can draw an end picture and send it as the clip's last frame (`agents/scene_agent/make_end_picture.py:MakeEndPicture`). If H3 refuses it, a developer notice is posted (`END_PICTURE_CLIP_REFUSED`).
  - Starting pictures (draws and edits) and end pictures each have their own six tries per run (#469). A failed end picture is remade while its budget remains; after six tries, filming may use the last failed end picture with a developer-only notice (`FAILED_END_PICTURE_FILMED`).
  - The prompt describes the result as a finished state backed by a short "no X", not a gradual quality (`VideoForm.shows_up_later`).
  - In scenes without her face, the scene agent except a planned still shot, never tells a hand or the product to stay unmoving (hold still, keep the same angle or tilt, stay in place, freeze); it describes the real movement and what it touches reacting. With an end picture the hand keeps working at the changing spot instead of staying on it (`agents/scene_agent/instructions.py:INSTRUCTIONS`, `VideoForm.beats`). The lock line still keeps the product's shape.
- **Proof:** R3 N5 s2, R5 native-pet s2, R5 blueland s2, R5 olipop s3, Oct 6 8b-3, R8 n02 s4, R8 n03 clip 3543, R9 n03 clip 3839, R10 clip 4137. End-picture test (Nov 4): mostly PASS. R12 p08 Liquid IV s2: beats held the hand and packet still ("the packet stays above the same entry point"); only the powder moved (hold-still ban for B-roll added Nov 8, untested). Positive-only wording failed (the foundation just vanished).
- **Status:** In code. Rewording the same shot never fixed physics (Oct 23).

## 6. It rushes, crams steps, and moves like AI

- **What H3 does:** It packs several steps into a few seconds, speeds things up, shakes unnaturally, and adds pointless zooms when told timings or "slowly".
- **How we get around it:**
  - One product movement per clip; face, head, free-hand and camera moves don't count (Julian, Nov 8 2026).
  - Code opens every prompt with only "Film this in real-time speed." (`agents/scene_forms.py:SPEED_LINE`).
  - No seconds, no "slowly/gently", no forced end-on-the-product zoom.
- **Proof:** Oct 6 Steve Madden bag (too many actions in 6 s), R6 beautyblender s2 (too fast), R4 #12 s2 (shaking), R3/R4/R6 hair tools (Wet Brush was given up in R7).
- **Status:** In code. Proven rule: "Only real-time speed from the opener".

## 7. It follows orders, not bans

- **What H3 does:** Prompts made of ban lists failed every time (round 5). Orders that clash, like "upright" with "nozzle down", bent the product.
- **How we get around it:** Describe what should happen, and back it up with a short "no X" at most. No contradictions. The start picture is checked before paying for a clip.
- **Proof:** Round 5 root cause (`docs/lessons.md`).
- **Status:** Prompt rule (`agents/scene_agent/instructions.py`, `VideoForm`).

## 8. Colours drift when pictures disagree

- **What H3 does:** Mixed red and black pictures made it invent the product.
- **How we get around it:** Every picture sent is in one colour. The page video's colour wins, and other photos are recoloured to match (`jobs/recolour.py:recolour`).
- **Proof:** Hand round 2 garject-A3 passed 4 of 4. The black-recolour control mostly failed (Oct 25).
- **Status:** In code. In R7 all 24 recolours fell back to the original photo. The redesign (#273) hasn't been run on real pictures yet.

## 9. Text and logos come out garbled

- **What H3 does:** Letters drift, even on clips that otherwise pass. Labels go soft.
- **How we get around it:**
  - Prompts describe on-screen text without spelling it out, and the form refuses quoted text (`agents/scene_forms.py:has_quoted_text`, checked in `Film.run`).
  - Picture prompts say "no text on props" (`PICTURE_ENDING`).
  - Change the start picture so a hand covers a badly placed logo (Creatify's fix, R9 n10 clip 3943).
  - Label words (Julian, Nov 6, ticket #427, replacing #406's one-third size gate): a scene that only shows the product to the lens holds it close, so its letters are big; a scene where the product is in use has no size rule (`jobs/picture_rules.py:LABEL_RULE`). The planner and the picture form are told it; no check reads it. The clip judge is unchanged: if it doesn't flag the letters, a customer won't see them either.
  - Open-state rule (R11 p08 clip 4250, a sealed stick pack "poured"): the starting picture shows the product ready for its action (pack torn, cap off) only when a page photo shows it open; with no such photo the planner picks an action that doesn't need it opened, and H3 never films the opening unless it is the point of the line (`jobs/picture_rules.py:PLAN_OPEN_RULE`). The picture check fails a product not ready for its action, or an open part no photo shows. No plan is sent back for it.
- **Proof:** R8 n12 clip 3506, R11 p08 clips 4198 and 4250.
- **Status:** In code: the label words (prompt only) and the open-state rule (prompt plus the picture check). The no-quoted-text rule (#362) stays.

## 10. Clip length is fixed, it fills spare time, and it stretches her speech over it

- **What H3 does:** A clip is 5 to 15 whole seconds and runs at least as long as its line. Any spare time gets an invented action, and she speaks slower to fill it: round 11's clips all spoke 1.93 to 2.10 words a second, slow for an ad. Voice words such as "an easy, unhurried pace" slow her more. A clip can also open on silence before her first word (Bondi: 1.66 s).
- **How we get around it:**
  - Lines are planned at 2.8 words a second, everywhere a length is counted (`jobs/pace.py:FIXED_WORDS_PER_SECOND`): the planner's line limits and word range (`jobs/planning.py:PLAN_INSTRUCTIONS`), the whole-clip cap (`jobs/checks.py:most_words_in_clips`) and the line rewrites.
  - Code sets the length from the line with little padding: words ÷ 2.8 + 0.6 s, rounded up, 5 to 15 s (`jobs/pace.py:h3_clip_seconds`). A line over 14 s at 2.8 words a second is refused before filming, never cut (`agents/scene_agent/lifecycle.py:_validate_line`).
  - Voice words never ask for a slow pace: a plan whose voice does is sent back (`jobs/planning.py:Plan._never_slow`, `jobs/pace.py:asks_for_a_slow_pace`).
  - Every clip's real pace is measured from its transcript (words ÷ first word's start to last word's end), kept in `clip.ruling["pace"]` and a `pace` developer notice (`jobs/clip_words.py:record_pace`). It never fails or re-films a clip: pace is fixed before filming (Julian, Nov 6).
  - The ad opens on its first word: silence over 0.3 s before the first clip's first word is cut in assembly (`jobs/assembly.py:opening_silence`).
  - No clip stalls after its line: assembly keeps at most 0.5 s after a clip's last word, picture and sound alike (`jobs/assembly.py:closing_silence_end`, #442). Round 12's AeroPress scene 3 had held 1.6 s of silence and no action; every clip Julian passed kept 0.45 s or less.
  - The film prompt asks for movement from the first frame (`agents/scene_forms.py:FIRST_FRAME_LINE`).
  - The beats must fill the whole clip with one action, and `after_last_word` says what she does after the line ends (`VideoForm`).
- **Proof:** `docs/lessons.md` #1; round 11 pace measures (spec #401).
- **Status:** In code (#407). Whether 2.8 is enough is read from the pace measures of the next round.

## 11. Speech and voice drift

- **What H3 does:** It speaks any quoted text, can say the line wrong or add a false start, drifts in voice between clips, and picks a gender at random if not told.
- **How we get around it:**
  - Code puts the checked line in `<d>` tags. The agent never writes speech (`agents/scene_forms.py:video_prompt`).
  - Extra leading words are trimmed (`jobs/clip_words.py:trim_leading_words`), and a word check fails a clip where a viewer would hear something different from the line: a changed or missing fact, garbled extra words, or a cut-off ending. A name said a little wrong or a small slip that keeps the meaning passes (`jobs/word_check.py:check`).
  - The same voice words go into every clip, gender first (`jobs/work.py:voice_words`). Some drift is accepted (ADR 0012).
  - New plans ask for a warm, natural, chatty voice with the person's age and a casual accent. Plans using announcer words are sent back naming the word; the slow-word refusal remains (`jobs/planning.py:NATURAL_VOICE`, `Plan._no_announcer_words`, `Plan._never_slow`). B-roll uses those same voice words off camera.
  - Every talking prompt puts "Her mouth moves in sync with every word." immediately after the checked line (`agents/scene_forms.py:video_prompt`). This asks H3 for lip sync; the word check still checks spoken meaning, not lip sync or whether she feels real.
- **Status:** Voice guards and prompt assembly are in code. The natural delivery, close sound and prototype evidence are in item 17.

## 12. What H3 accepts

- **What H3 does:** It takes one start picture or up to 9 example pictures, never both (a 400 error). An end picture needs a start picture. Reference videos only go with example pictures. There is no seed.
- **How we get around it:** A validator refuses a bad mix before sending (`gateway/types.py:BrollClipHandoff._made_one_way`, `jobs/work.py:sent_handoff`).
- **Status:** In code.

## 13. Creatify's hidden prompt rewrite

- **What H3 does:** `prompt_enhancement: auto` rewrites every prompt where we can't see it.
- **How we get around it:** Every clip sends it off (`jobs/work.py:sent_handoff` passes `prompt_enhancement=False`, so `gateway/creatify_adapter.py:submit_broll` sends `none`).
- **Downside:** Retakes of the same prompt come out nearly identical (0.98 likeness across 3 takes, hand round 1). A retry needs a real change.
- **Status:** In code (turned off Nov 2).

## 14. Failed renders and double charges

- **What H3 does:** It sometimes fails or refuses a render, and a paid call whose reply is lost might be charged twice.
- **How we get around it:** A try is never sent again while the charge is uncertain, and a clip already paid for is collected after a restart (`gateway/creatify_adapter.py`, `jobs/work.py:_clip_asked_for`). Each scene gets 6 tries per run (`agents/scene_agent/lifecycle.py:MOST_TRIES`). There's no published rate limit, so a 429 is retried (`docs/rate-limits.md`).
- **Status:** In code.

## 15. It copies a presenter who looks pasted onto the room

- **What H3 does:** When the start picture shows her pasted onto the background (light, shadows and edges that don't match the room), H3 films her that way. The pasted look starts in our picture, not in H3.
- **How we get around it:**
  - The shared picture of her in the room is drawn in one go: one picture call with her portrait passed only for her face, and words for her, her clothes and the room (`jobs/work.py:person_and_room`, `jobs/scenes.py:PERSON_IN_ROOM_PROMPT`). No step merges a finished portrait with a finished room picture.
  - The prompt carries realism words (a lived-in room lit only by its own lights, no ring light, no studio light, no beauty filter, no background blur) and a relaxed, natural pose instead of "stands straight and still, hands at their sides".
  - The B-roll room alone is her picture with her taken out (`ROOM_FROM_PERSON_PROMPT`), so B-roll happens in her room. A scene's own place is drawn the same way (`new_scene_place`, `presenter_in_place`). Every talking start picture is still an edit of the shared picture.
- **Proof:** R11 p01 and p10 clip 4252 (she looked pasted on). Not graded yet since the fix.
- **Status:** In code (spec #401, ticket #405). An ad with no presenter still draws its room alone from the before-picture.

## 16. It gets mirrors wrong

- **What H3 does:** A mirror's reflection comes out wrong (the judge also flags mirrors).
- **How we get around it:** A talking scene names one of four talking styles (selfie, propped waist-up, seated close-up, talking while doing it), never a mirror (`jobs/plan_rules.py:STYLE_AND_STATE`, in the planner's prompt; since Nov 9 2026 a plan breaking it goes ahead with a developer notice, `PLAN_RULE_NOT_KEPT`). The scene agent draws the starting picture in that style (`agents/scene_agent/draw_starting_picture.py:talking_jobs`).
- **Proof:** Julian, Nov 6 04:56 ("I would avoid almost all the mirrors because the judge will flag it, and it's really hard for the video generation API to get it right"). A walking limit was listed here without proof and was removed (Julian, Nov 8).
- **Status:** In code (ticket #403, ADR 0013).

## 17. Talking scenes sound like an announcer and move like a mannequin

- **What H3 does:** Page-copy lines, "ad pace" voice words and hold-still beats ask for an ad read with a frozen face and body. Round 12 had no acted feelings in 25 talking scenes, and 20 of 25 had hold-still beats (spec #447).
- **How we get around it:**
  - The planner and every line rewrite share a spoken-line rule: everyday words from her side, a setup then a reaction or payoff, keeping facts, numbers and product names unchanged. Her own opinion needs no source (`jobs/planning.py:SPOKEN_LINE_RULE`, `NOT_A_CLAIM`, `jobs/checks.py:REWRITE_INSTRUCTIONS`). The hook keeps its short length. The last line is asked to recommend it casually, keep the price unless the owner asked to omit it, ask people to get it, and include the brand when one is planned (`PLAN_INSTRUCTIONS`). The price and brand policies are unchanged.
  - Four talking-only video-form boxes ask for `delivery` matched to the line, one `feeling` acted as 2 to 4 face and head moves, one sentence-timed `product_move`, and one or two quiet concrete `sounds` from the room or action (`agents/scene_forms.py:VideoForm`). A light laugh is asked for only in a hook or close. Moves name the sentence, never quote a word. Her free hand, head and shoulders may move; only the product is locked. Talking prompts omit `small_life` because feeling replaces it.
  - Code places delivery before the checked line, lip sync immediately after it, then product move, feeling, after-last-word action, reference lines, the lock line and sound. The sound ends "her clear close voice. No music." Code supplies room tone once. Talking forms call her "the woman" (`agents/scene_forms.py:video_prompt`).
  - The existing voice-word guards also refuse unsuitable talking delivery before filming (`agents/scene_forms.py:unsuitable_delivery`, `agents/scene_agent/film.py:Film.run`). B-roll keeps its prompt wording and order, camera rules, `small_life` and sound line; only the shared voice description changes.
- **Proof:** Julian graded the earlier #447 prototype's 8 re-filmed talking scenes 8 of 8 "yes, she feels like a real person" (Nov 7, 2026). These are prototype grades, not a new full-ad grade of the built app.
- **Status:** Prompt rules and assembly are in code (#448–#455). The saved-scene replay was explicitly removed from #457's build scope. A paid full-ad after-score remains outside this build; it must measure whether removing hold-still beats bends products. If it does, the first fix is a smaller product move, not restoring hold still.

## 18. A selfie camera sways and shakes

- **What H3 does:** Asking for a handheld selfie with sway makes the phone shake, the one flaw Julian named in the natural-presenter prototype.
- **How we get around it:** Talking prompts open with "UGC, filmed on a steady phone" and put "The phone stays perfectly steady the whole time: no sway, no shake." after the camera box (`agents/scene_forms.py:video_prompt`). `VideoForm.camera` asks for a perfectly steady phone even in a selfie, permits only a slow push in, and forbids sway, shake, wobble, drift and handheld movement. These are instructions to H3, not a new camera check. B-roll's handheld opener and camera rules stay unchanged.
- **Proof:** Julian graded the four steady-camera selfie retakes 4 of 4 yes (#456). These are prototype retakes, not new clips filmed during the build.
- **Status:** Prompt wording is in code (#456). Starting pictures, clip lengths, captions and the word check are unchanged.

## How to fix a failed clip (Julian, R11)

Don't keep tuning the prompt. Change the picture.

- **Change the picture after a judge fail.** The scene agent is told never to film the same starting picture and end picture again after the clip judge failed their clip: draw a new starting picture (with or without a new action), make an end picture, or film another picture it already drew (`agents/scene_agent/instructions.py`). Code refused it from #408 until Nov 9 2026; the refusal never fired in round 12, so it was deleted and the instruction kept (rules review).
- **Fix order, told once at the first clip the judge fails** (a word or fix check fail gets no note) (`agents/scene_agent/lifecycle.py:FIX_ORDER`): wrong in the first frame, draw a new starting picture; goes wrong during the move, choose a simpler action; ends wrong or must stop at a set point, make an end picture.
- **End picture cases** (guidance, not a limit; also in the agent's instructions and the end picture tool): the clip must end on a result where the product touched; a push-in ends on a crop of the real label; the clip drifts or ends in the wrong place; the action must stop at a set point (R11 p01, a press kept going to the end).
- **At the second clip the judge fails** the agent is told once: consider a different action.
- After every failed clip, the agent gets that try's starting picture back with its frames.

From R11 p03, p05 clip 4301, p09 clip 4239. The same picture retried failed 5 of 6 times. A new picture fixed 4 of 6.

## Tried and dropped

- **Several shots inside one clip (Oct 7):** parts got lost at each cut, and a crack appeared.
- **"The camera stays still" (deleted Nov 1):** the real fault was the forced zoom, not camera movement. That general rule stays dropped for B-roll; #456 later added the talking-only steady-phone instructions in item 18 after Julian's selfie grades.
- **Fixed phone line "the side facing the camera stays toward the camera" (Oct 10):** the old fixed line stays dropped. The side rule lives in `VideoForm.camera`, and talking scenes also ask to keep the photographed side facing the camera in `VideoForm.product_move` (#454).
- **Reference mode for every clip (Oct 16):** it redrew the product in every clip. Start frames came back Nov 2.
- **A drawn side picture for every product (Oct 15):** 4 drawings were wrong.
- **One "priority line" leading a retry (Oct 25):** it fixed the brush's back, then a second brush appeared.
- **HeyGen for talking scenes:** H3 speaks again since Nov 3 (ADR 0012).
- **Spelling the label's letters to H3 (#410, Nov 6):** Julian: "letters spelled out doesn't really work".
- **A picture gate on label size (#406, Nov 6):** a facing label needed the product at a third of the picture's height. It failed 43 of 63 round 10-11 pictures and a product in use (a brush in her hair) can't meet it. Replaced by #427's words.

## Still unsolved

- **Hands:** the brush swaps hands (hand round 1, wetbrush v5). No fix yet.
- **Letters drift** on lettered products (see 9).
- **Hair tools and brushes** still move like AI.
- **Dreamfarm Garject** failed every round. Julian set it aside: "even creatify fails this".

## Sources

- Julian's grades: `runs/round-*/julian-grades/` and `runs/hand-rounds/` in the project files.
- Decisions: `decisions/` in the project files (Oct 7 B-roll prompts, the built-* records).
- In this repo: `docs/lessons.md`, `docs/creatify-ad-lessons.md` §2, `docs/adr/0011`, `docs/adr/0012`, `docs/adr/0012-legacy/`.
