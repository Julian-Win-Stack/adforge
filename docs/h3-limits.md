# H3 limits and how we work around them

Boreal-H3 (Creatify's video model, "H3") films every B-roll clip. It has habits that break ads. This doc lists each one, how AdForge gets around it, the grades that proved it, and whether the fix is in code.

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
  - The video prompt ends with a lock line: "The [product] keeps its exact shape, colour and printed words." After a failed clip the line grows to name every part that must stay (`agents/scene_forms.py:VideoForm.lock_line`).
  - The PRODUCT check in the clip judge looks at 10 frames a second and fails a clip whose product changed (`jobs/clip_judge.py:judge_clip`).
- **Proof:** Hand round 1 wetbrush v3/v4, hand round 2 garject-A/A2, R5 cocolab s2 (3 versions), R7 c4.
- **Status:** In code. History: reference mode (example pictures, no start picture) went in Oct 16 and was dropped Oct 24-27. It came back as start-frame mode on Nov 2 (ADR 0011) because reference mode redrew the product in every clip.

## 3. It flips the product to a side it never saw (phones)

- **What H3 does:** A phone turns and shows cameras on both sides, or two fronts.
- **How we get around it:**
  - The camera box, the picture form, the scene agent and the planner all say "Never show a side or part no photo shows" (`jobs/picture_rules.py:SIDE_RULE`), and the picture check fails a starting picture that does (ticket #406).
  - The planner never plans a hand turning, flipping or spinning the product (`jobs/planning.py`).
  - The clip judge's PRODUCT check catches a flip.
- **Proof:** R3: 3 of 3 phone clips flipped. Reference-mode test: 5 of 12. New wording for the phone-pocket scene: 3 of 3 passed.
- **Status:** In code. Proven rule: "Never show a side or part no photo shows" (widened from "never flip the product to a side no photo shows", Julian's OK Nov 6). Drops and tumbles still flip; we don't film them.

## 4. It makes up how the product is used, or adds steps

- **What H3 does:** It pours from a second bottle, scrubs the toilet seat, uses floss like a stick, pulls a pore strip off with a tool, or adds a twist nobody asked for. This is the most common fail in nearly every round.
- **How we get around it:**
  - One action per clip, and the voice carries the other steps.
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
  - The prompt describes the result as a finished state backed by a short "no X", not a gradual quality (`VideoForm.shows_up_later`).
- **Proof:** R3 N5 s2, R5 native-pet s2, R5 blueland s2, R5 olipop s3, Oct 6 8b-3, R8 n02 s4, R8 n03 clip 3543, R9 n03 clip 3839, R10 clip 4137. End-picture test (Nov 4): mostly PASS. Positive-only wording failed (the foundation just vanished).
- **Status:** In code. Rewording the same shot never fixed physics (Oct 23).

## 6. It rushes, crams steps, and moves like AI

- **What H3 does:** It packs several steps into a few seconds, speeds things up, shakes unnaturally, and adds pointless zooms when told timings or "slowly".
- **How we get around it:**
  - One movement per clip.
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
  - Label size rule (Julian's R11 note, ticket #406): a label faces the camera only when the product fills at least a third of the picture's height; otherwise the product is far away, turned away or covered by a hand, never in between (`jobs/picture_rules.py:LABEL_RULE`). The planner and the picture form are told it, and the picture check answers whether a label faces the camera and how much of the height the product fills; code fails a facing label under a third and the picture is redrawn (`agents/scene_agent/picture_check.py:verdict`).
  - Open-state rule (R11 p08 clip 4250, a sealed stick pack "poured"): the starting picture shows the product ready for its action (pack torn, cap off) only when a page photo shows it open; with no such photo the planner picks an action that doesn't need it opened, and H3 never films the opening unless it is the point of the line (`jobs/picture_rules.py:PLAN_OPEN_RULE`). The picture check fails a product not ready for its action, or an open part no photo shows. No plan is sent back for it.
- **Proof:** R8 n12 clip 3506, R11 p08 clips 4198 and 4250.
- **Status:** In code: the label size and open-state rules (prompt rules plus the picture check). Not tested yet: spelling the label out to H3 (#362 stays until that test wins).

## 10. Clip length is fixed, and it fills spare time with made-up action

- **What H3 does:** A clip is 5 to 15 whole seconds and runs at least as long as its line. Any spare time gets an invented action.
- **How we get around it:**
  - Code sets the length from the line: 2.5 words a second, rounded up, plus 1 second of action (`jobs/checks.py:h3_clip_seconds`). A line over 14 s is refused before filming (`agents/scene_agent/lifecycle.py:_validate_line`).
  - The beats must fill the whole clip with one action, and `after_last_word` says what she does after the line ends (`VideoForm`).
- **Proof:** `docs/lessons.md` #1.
- **Status:** In code.

## 11. Speech and voice drift

- **What H3 does:** It speaks any quoted text, can say the line wrong or add a false start, drifts in voice between clips, and picks a gender at random if not told.
- **How we get around it:**
  - Code puts the checked line in `<d>` tags. The agent never writes speech (`agents/scene_forms.py:video_prompt`).
  - Extra leading words are trimmed (`jobs/clip_words.py:trim_leading_words`), and a word check fails a clip that changed the line (`jobs/word_check.py:check`).
  - The same voice words go into every clip, gender first (`jobs/work.py:voice_words`). Some drift is accepted (ADR 0012).
- **Status:** In code. Mostly about talking scenes; B-roll uses the same voice off camera.

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

## How to fix a failed clip (Julian, R11)

Don't keep tuning the prompt. Change the picture.

- **Code refuses the same picture.** After the clip judge fails a clip, filming the same starting picture and end picture again is refused (`agents/scene_agent/film.py:_refuse_a_judge_failed_pair`). The agent must draw a new starting picture (with or without a new action), make an end picture, or film another picture it already drew. Only the judge's own fail refuses: an unsure or unchecked verdict, a fix check's fail, or a word check's fail allows the same picture. Each refusal posts a developer notice (`SAME_PICTURE_REFUSED`). With no picture slot and no other picture left to film, the run ends as at the cap and the owner picks from its tries.
- **Fix order, told once at the first fail** (`agents/scene_agent/lifecycle.py:FIX_ORDER`): wrong in the first frame, draw a new starting picture; goes wrong during the move, choose a simpler action; ends wrong or must stop at a set point, make an end picture.
- **End picture cases** (guidance, not a limit; also in the agent's instructions and the end picture tool): the clip must end on a result where the product touched; a push-in ends on a crop of the real label; the clip drifts or ends in the wrong place; the action must stop at a set point (R11 p01, a press kept going to the end).
- **At the second fail** the agent is told once: consider a different action; a changed action must still show the scene's proof.
- After every failed clip, the agent gets that try's starting picture back with its frames.

From R11 p03, p05 clip 4301, p09 clip 4239. The same picture retried failed 5 of 6 times. A new picture fixed 4 of 6.

## Tried and dropped

- **Several shots inside one clip (Oct 7):** parts got lost at each cut, and a crack appeared.
- **"The camera stays still" (deleted Nov 1):** the real fault was the forced zoom, not camera movement.
- **Fixed phone line "the side facing the camera stays toward the camera" (Oct 10):** now only a field description in `VideoForm.camera`.
- **Reference mode for every clip (Oct 16):** it redrew the product in every clip. Start frames came back Nov 2.
- **A drawn side picture for every product (Oct 15):** 4 drawings were wrong.
- **One "priority line" leading a retry (Oct 25):** it fixed the brush's back, then a second brush appeared.
- **HeyGen for talking scenes:** H3 speaks again since Nov 3 (ADR 0012).

## Still unsolved

- **Hands:** the brush swaps hands (hand round 1, wetbrush v5). No fix yet.
- **Letters drift** on lettered products (see 9).
- **Hair tools and brushes** still move like AI.
- **Dreamfarm Garject** failed every round. Julian set it aside: "even creatify fails this".

## Sources

- Julian's grades: `runs/round-*/julian-grades/` and `runs/hand-rounds/` in the project files.
- Decisions: `decisions/` in the project files (Oct 7 B-roll prompts, the built-* records).
- In this repo: `docs/lessons.md`, `docs/creatify-ad-lessons.md` §2, `docs/adr/0011`, `docs/adr/0012`, `docs/adr/0012-legacy/`.
