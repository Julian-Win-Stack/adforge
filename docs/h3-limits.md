# H3 limits and how we work around them

Boreal-H3 (Creatify's video model, "H3") films every clip, with its own speech and sound. Each habit that breaks ads, our workaround, its proof and status.

**Keep it up to date.** When a grade shows a new failure, or a fix lands or is dropped, edit the entry in the same PR. Entries: **What H3 does**, **How we get around it** (`file:function`), **Proof** (rounds and clip ids from Julian's grades), **Status**.

Rules proven by grades live in `docs/agents/proven-broll-rules.md` and change only with Julian's OK; this doc says why they exist and where code enforces them.

Paths below are under `backend/`.

## 1. It copies whatever is in the pictures

- **What H3 does:** It copies more than the product's look: a leg, the background, props, another product, a stranger's face. Prompt words didn't stop it.
- **How we get around it:**
  - Every clip is filmed from a start picture we draw and check, never from raw shop photos (`agents/scene_agent/film.py:Film.run` refuses an unchecked picture).
  - Strangers' faces are swapped for our presenter first (`jobs/face_swap.py`, `agents/scene_agent/draw_starting_picture.py:swapped`).
  - Only the target product is sent, cut out (`jobs/cut_out.py`).
  - A scene agent's pick that isn't the product alone goes to the image model, the picture check and (through the sent list) the clip judge as a true crop: one AI call per ad boxes the product in every page photo, and code cuts the box from the photo's real pixels, so nothing covered is made up (`jobs/side_photos.py:for_a_pick`, #524). The video model's reference pictures and the producer's made pictures still get the redrawn cut-out (`jobs/product_only.py`); so do the picks when that call fails.
- **Proof:** R7 chipolo s3, R7 beautyblender s4, hand round 2 garject-B.
- **Status:** In code. Proven rule: "Photos are for looks only".

## 2. It invents parts and sides it has never seen

- **What H3 does:** It adds a third handle, an extra button, a second screen or brush, bristles on both sides, or a cable that doesn't exist.
- **How we get around it:**
  - The start picture pins the product's look in frame 1.
  - For each side of the product the side picker found, its chosen page photo goes to the image model and the picture check beside the scene agent's picks, as its true crop, labelled from the page with the picker's side and its line on what the crop shows, so a side the agent never picked isn't guessed, even one only shown with a person (`agents/scene_agent/page_views.py:added`, round 13 root cause 1, #516, #525). A side whose first ranked photo can't go takes its next one; a side the agent's picks already cover gets none. When the picker fails, every page photo of the product on its own goes instead, with a developer notice. Short of room, views of sides the picks don't show go first; one left out posts a developer notice. The clip judge gets the same views its starting picture was drawn and checked with, read from the picture's sent record, and judges colour only against the starting picture and the agent's picks (`page_views.drawn_with`, #520).
  - The prompt keeps a lock line before its sound: "The [product] keeps its exact shape, colour and printed words." After a failed clip it names every part that must stay (`agents/scene_forms.py:VideoForm.lock_line`).
  - Only the product is locked; she is never told to hold still or freeze (item 17).
  - The clip judge's PRODUCT check looks at 10 frames a second and fails a clip whose product changed (`jobs/clip_judge.py:judge_clip`).
- **Proof:** Hand round 1 wetbrush v3/v4, hand round 2 garject-A/A2, R5 cocolab s2, R7 c4.
- **Status:** In code (start frames, ADR 0011).

## 3. It flips the product to a side it never saw (phones)

- **What H3 does:** A phone turns and shows cameras on both sides, or two fronts.
- **How we get around it:**
  - The camera box, picture form, scene agent and planner all say "Never show a side or part no photo shows" (`jobs/picture_rules.py:SIDE_RULE`), and the picture check fails a starting picture that does.
  - The planner never plans a hand turning, flipping or spinning the product (`jobs/planning.py`).
  - A talking scene's one product move is small, tied to a sentence, photographed side to camera (`VideoForm.product_move`).
  - The clip judge's PRODUCT check catches a flip.
- **Proof:** R3: 3 of 3 phone clips flipped. New phone-pocket wording: 3 of 3 passed.
- **Status:** In code. Proven rule: "Never show a side or part no photo shows". Drops and tumbles still flip; we don't film them.

## 4. It makes up how the product is used, or adds steps

- **What H3 does:** It pours from a second bottle, uses floss like a stick, adds a twist. The most common fail.
- **How we get around it:**
  - One product action per clip (face, head, free-hand and camera moves don't count); the voice carries other steps. A two-state claim is two clips with a cut.
  - Show the action as the page shows it; a page-video piece goes as a movement reference (`agents/scene_agent/reference.py:pieces_for`).
  - Don't film actions H3 can't do.
- **Proof:** R4 #8 s2/s3, hand round 1 cocofloss-s4-v2, R7 n01 s3, R7 n08 s3, R9 clip 3936.
- **Status:** Prompt rules plus proven rules. Some fails start in our planner or prompt.

## 5. Its physics are wrong, and results appear where nothing touched

- **What H3 does:** Gel thickens on its own, a stain vanishes where nothing touched it, a scoop refills, a toilet flushes before the button. It makes up what contents look like.
- **How we get around it:**
  - Rule: a result shows only where the product touched.
  - From the first try the scene agent can draw an end picture as the clip's last frame (`agents/scene_agent/make_end_picture.py:MakeEndPicture`). If H3 refuses it, a developer notice is posted (`END_PICTURE_CLIP_REFUSED`).
  - Starting and end pictures each get six tries per run, stored on the run. When `change_scene` changes the action or kind after a picture budget is used up, the run gets 3 more tries of each kind past those used, never past 12 of each. A B-roll scene never films a failed starting or end picture (`film` refuses: change the scene with the ladder); a talking scene may film its closest failed picture (`CLOSEST_PICTURE_FILMED`) or, once its end-picture tries are used, the last failed end picture (`FAILED_END_PICTURE_FILMED`).
  - A run films 6 clips. A B-roll scene that `change_scene` switches to talking (ladder step 5) gets 2 clips of its own, once a run: its limit becomes the larger of 6 and the clips used at the switch plus 2 (`SceneAgentRun.clips_at_talking_switch`, `agents/scene_agent/lifecycle.py:clip_limit`). A B-roll run that used its 6 clips, even on a failed render, stays open and is told to switch to talking or give up (`TALKING_STEP`); only then does the cap end it. A switched scene films its closest failed picture once its own talking pictures in the run are used up and failed; its earlier B-roll pictures don't block it (#521).
  - The result is described as a finished state backed by a short "no X" (`VideoForm.shows_up_later`).
  - A cream, foam, gel or makeup is filmed being applied only when a page photo or video shows it; otherwise any scene shows the result where it touched or a dab on a fingertip (`jobs/planning.py:SPREAD_ON_BODY_RULE`, round 13 root cause 4: applying never passed, Glow Recipe R6, Krave R8-R10, Bondi R12-R13; Julian passed the Nov 10 2026 test, Bondi result and Krave and Glow Recipe fingertip clips).
  - Without her face (except a planned still shot), a hand or the product is never told to stay unmoving (`agents/scene_agent/instructions.py:INSTRUCTIONS`, `VideoForm.beats`).
- **Proof:** R3 N5 s2, R5 blueland s2, R5 olipop s3, R8 n02 s4, R9 clip 3839, R10 clip 4137; end-picture test mostly passed. The B-roll hold-still ban (after R12 p08 s2) is untested.
- **Status:** In code. Rewording the same shot never fixed physics.

## 6. It rushes, crams steps, and moves like AI

- **What H3 does:** It packs steps into a few seconds, speeds up, shakes, and adds zooms when told timings or "slowly".
- **How we get around it:**
  - One product movement per clip.
  - Every prompt opens with only "Film this in real-time speed." (`agents/scene_forms.py:SPEED_LINE`).
  - No seconds, no "slowly/gently", no forced end zoom.
- **Proof:** Steve Madden bag (too many actions in 6 s), R6 beautyblender s2, R4 #12 s2, R3/R4/R6 hair tools.
- **Status:** In code. Proven rule: "Only real-time speed from the opener".

## 7. It follows orders, not bans

- **What H3 does:** Ban-list prompts failed every time (round 5). Clashing orders ("upright" with "nozzle down") bent the product.
- **How we get around it:** Describe what should happen, backed by a short "no X" at most. No contradictions. The start picture is checked before paying.
- **Proof:** Round 5 root cause (`docs/lessons.md`).
- **Status:** Prompt rule (`agents/scene_agent/instructions.py`, `VideoForm`).

## 8. Colours drift when pictures disagree

- **What H3 does:** Mixed red and black pictures made it invent the product.
- **How we get around it:** Every picture sent is one colour. The page video's colour wins; other photos are recoloured (`jobs/recolour.py:recolour`). The one exception: a page view added for its shape (section 2) reaches the image model in its own colour, labelled as showing the shape only with the ad's colour named, and the picture check judges colour only against the agent's picks (`agents/scene_agent/page_views.py`). H3 still gets only the one-colour start picture.
- **Proof:** Hand round 2 garject-A3 passed 4 of 4; the black-recolour control mostly failed.
- **Status:** In code. In R7 all 24 recolours fell back to the original photo; the redesign (#273) is not yet run on real pictures.

## 9. Text and logos come out garbled

- **What H3 does:** Letters drift and labels go soft, even on clips that otherwise pass.
- **How we get around it:**
  - Prompts describe on-screen text without spelling it, and the form refuses quoted text (`agents/scene_forms.py:has_quoted_text`, checked in `Film.run`).
  - Picture prompts say "no text on props" (`PICTURE_ENDING`).
  - A new start picture can have a hand cover a badly placed logo (R9 n10 clip 3943).
  - Label rule: a show-only scene holds the product close so letters are big; a product in use has no size rule (`jobs/picture_rules.py:LABEL_RULE`). Prompt only.
  - Open-state rule: the product is shown opened only if a page photo shows it open; otherwise the action mustn't need opening (`jobs/picture_rules.py:PLAN_OPEN_RULE`). The picture check fails a product not ready for its action, or an open part no photo shows.
- **Proof:** R8 n12 clip 3506, R11 p08 clips 4198 and 4250.
- **Status:** In code: label rule (prompt), open-state rule (prompt plus picture check), no quoted text.

## 10. Clip length is fixed, it fills spare time, and it stretches her speech over it

- **What H3 does:** A clip is 5 to 15 whole seconds, at least as long as its line. Spare time gets an invented action, and she speaks slower to fill it (round 11: 1.93 to 2.10 words a second). Slow voice words slow her more. A clip can open on silence.
- **How we get around it:**
  - Lines are planned at 2.8 words a second wherever length is counted (`jobs/pace.py:FIXED_WORDS_PER_SECOND`, `jobs/checks.py:most_words_in_clips`).
  - Length is words ÷ 2.8 + 0.6 s, rounded up, 5 to 15 s (`jobs/pace.py:h3_clip_seconds`). A line over 14 s is refused before filming, never cut (`agents/scene_agent/lifecycle.py:_validate_line`).
  - Voice words never ask for a slow pace (`jobs/planning.py:NEVER_SLOW`).
  - Real pace is measured per clip and posted as a `pace` developer notice; it never fails a clip (`jobs/clip_words.py:record_pace`).
  - Assembly cuts silence over 0.3 s before the first word and keeps at most 0.5 s after each clip's last word (`jobs/assembly.py:opening_silence`, `closing_silence_end`).
  - Movement from the first frame (`agents/scene_forms.py:FIRST_FRAME_LINE`); beats fill the clip with one action; `after_last_word` covers the time after the line.
- **Proof:** `docs/lessons.md` #1; round 11 pace measures.
- **Status:** In code. Whether 2.8 is enough is read from the next round's pace measures.

## 11. Speech and voice drift

- **What H3 does:** It speaks any quoted text, can say the line wrong or add a false start, drifts in voice between clips, and picks a gender at random if not told.
- **How we get around it:**
  - Code puts the checked line in `<d>` tags; the agent never writes speech (`agents/scene_forms.py:video_prompt`).
  - Extra leading words are trimmed (`jobs/clip_words.py:trim_leading_words`). The word check fails a changed or missing fact, garbled words or a cut-off ending; small slips pass (`jobs/word_check.py:check`).
  - The same voice words go into every clip, gender first (`jobs/work.py:voice_words`). Some drift is accepted (ADR 0012).
  - Plans ask for a warm, chatty, never-slow voice (`jobs/planning.py:NATURAL_VOICE`, `NEVER_SLOW`). B-roll uses the same voice words.
  - Talking prompts add "Her mouth moves in sync with every word." after the line.
- **Status:** In code. Delivery and sound: item 17.

## 12. What H3 accepts

- **What H3 does:** One start picture or up to 9 example pictures, never both (a 400 error). An end picture needs a start picture. Reference videos only go with example pictures. No seed.
- **How we get around it:** A validator refuses a bad mix before sending (`gateway/types.py:BrollClipHandoff._made_one_way`, `jobs/work.py:sent_handoff`).
- **Status:** In code.

## 13. Creatify's hidden prompt rewrite

- **What H3 does:** `prompt_enhancement: auto` rewrites every prompt where we can't see it.
- **How we get around it:** Every clip turns it off (`jobs/work.py:sent_handoff` passes `prompt_enhancement=False`; `gateway/creatify_adapter.py` sends `none`).
- **Downside:** Retakes of one prompt come out nearly identical (0.98 likeness). A retry needs a real change.
- **Status:** In code.

## 14. Failed renders and double charges

- **What H3 does:** It sometimes fails or refuses a render, and a paid call whose reply is lost might be charged twice.
- **How we get around it:** No resend while a charge is uncertain; a paid clip is collected after a restart (`gateway/creatify_adapter.py`, `jobs/work.py:_clip_asked_for`). Six tries per scene (`agents/scene_agent/lifecycle.py:MOST_TRIES`). A 429 is retried (`docs/rate-limits.md`).
- **Status:** In code.

## 15. It copies a presenter who looks pasted onto the room

- **What H3 does:** If the start picture shows her pasted onto the background, H3 films her that way. The fault starts in our picture.
- **How we get around it:**
  - Her picture in the room is drawn in one call, her portrait used for her face only, never by merging a finished portrait and room (`jobs/work.py:person_and_room`, `jobs/scenes.py:PERSON_IN_ROOM_PROMPT`). The prompt asks for a lived-in room lit by its own lights and a relaxed pose.
  - The B-roll room is that picture with her taken out (`ROOM_FROM_PERSON_PROMPT`); a scene's own place likewise (`new_scene_place`, `presenter_in_place`).
  - A talking start picture is drawn from her portrait and the room alone, since the picture model copied the shared picture's camera (`agents/scene_agent/draw_starting_picture.py:make_picture`; 4 of 4 better in Julian's test).
- **Proof:** R11 p01 and p10 clip 4252. Not graded in a full round since the fix.
- **Status:** In code. An ad with no presenter draws its room from the before-picture.

## 16. It gets mirrors wrong

- **What H3 does:** A mirror's reflection comes out wrong, and the judge flags mirrors.
- **How we get around it:** A talking scene is selfie, propped, or talking while doing it (filmed propped), never a mirror (`jobs/plan_rules.py:STYLE_AND_STATE`, `SETUP_LOOKS`). A plan breaking it goes ahead with a developer notice (`PLAN_RULE_NOT_KEPT`). The starting picture is drawn in that setup (`agents/scene_agent/draw_starting_picture.py:talking_jobs`, `jobs/work.py:talking_portrait_and_room`).
- **Proof:** Julian: avoid mirrors: "the judge will flag it, and it's really hard for the video generation API to get it right".
- **Status:** In code (ADR 0013).

## 17. Talking scenes sound like an announcer and move like a mannequin

- **What H3 does:** Page-copy lines, "ad pace" voice words and hold-still beats give an ad read with a frozen face and body (round 12: no acted feelings in 25 talking scenes).
- **How we get around it:**
  - Lines use everyday words from her side, a setup then a payoff, facts and names unchanged; her own opinion needs no source (`jobs/planning.py:SPOKEN_LINE_RULE`, `NOT_A_CLAIM`, `jobs/work.py:_rewrite_scene`). The hook (about 20 words, at most 26) is her problem, then the product as her answer (`HOOK_RULE`, `HOOK_WORDS`). The last line recommends it casually, with price and brand as planned (`PLAN_INSTRUCTIONS`).
  - Talking-only boxes (`agents/scene_forms.py:VideoForm`): `delivery` like telling a friend, never ad or announcer words; one `feeling` as 2 to 4 face and head moves (the hook gets a calm face instead); one sentence-timed `product_move`; one or two quiet `sounds`. Talking prompts omit `small_life`. Instructions only.
  - `video_prompt` sets the order (delivery, line, lip sync, product move, feeling, after-last-word, references, lock line, sound) and ends the sound on "her clear close voice. No music."
- **Proof:** Prototype grades only: 8 of 8 re-filmed talking scenes felt real; 5 of 5 calm hooks passed (round 13's acted hooks looked fake).
- **Status:** In code. A full-ad run must still check whether dropping hold-still bends products; if so, try a smaller product move first, not hold still.

## 18. A selfie camera sways and shakes

- **What H3 does:** A handheld selfie with sway makes the phone shake.
- **How we get around it:** Talking prompts open "filmed on a steady phone" and add "no sway, no shake" (`agents/scene_forms.py:video_prompt`). `VideoForm.camera` allows only a slow push in. No camera check; B-roll is unchanged.
- **Proof:** Julian graded four steady-camera selfie retakes 4 of 4 (prototype).
- **Status:** In code.

## 19. Left with nothing to move, it moves something by itself

- **What H3 does:** A planned still shot (a showcase with no hand) is 5 to 7 seconds of nothing, while code's lines ask for "movement from the very first frame" and the B-roll opener said "handheld". H3 fills the clip by moving the product itself: a drawer slides open (R13 Dash s2), a sprayer moves (R12 Bissell).
- **How we get around it:**
  - A still shot is filmed as a small camera move pinned at both ends: the scene agent picks `still_shot` on film (pull back for where it fits, push in for how it looks) and a crop centre; code cuts the other end from the starting picture's own pixels, so nothing in the scene can move (`agents/scene_agent/still_shot.py:crop`, `agents/scene_agent/film.py:Film.run`). The prompt adds "Only the camera moves ... nothing opens, slides, turns or lifts" (`agents/scene_forms.py:STILL_SHOT_CAMERA`). B-roll only.
  - No "handheld" in the B-roll opener (`agents/scene_forms.py:BROLL_OPENER`).
  - The scene agent is told code's exact fixed lines, so it never writes against them (`agents/scene_forms.py:CODE_LINES` in `agents/scene_agent/instructions.py:INSTRUCTIONS`).
- **Proof:** Physics try (Nov 9): 5 of 5 still shots used the camera move; Julian passed R13 Dash s2 2 of 2.
- **Status:** In code.

## 20. It films moves that can't happen

- **What H3 does:** Given a planned move where a part must pass through something solid, it films it anyway: a clog's heel strap, fixed at both ends, swings through the wearer's ankle (R12 Crocs s3 and s4, R13 Crocs s3). Nothing before filming looked at the move's path; the picture check judges one frame.
- **How we get around it:**
  - Before every clip, B-roll or talking, the move check sees exactly what H3 gets (first frame, last frame or crop, video prompt) and asks one question: is something solid in a moving part's path, or must a stiff part bend? It judges the path from the frames, not the prompt's words (`agents/scene_agent/move_check.py:check`, `agents/scene_agent/film.py:_check_the_move`).
  - A failed check refuses the film without using a clip try. The refusal and the scene agent's instructions say to re-plan with `change_scene` the way a real person does it (set the part before the product goes on, show it on the product alone, or show the result), then draw a new starting picture, a new end picture and a new video form; never shrink the move, start it partway or reword it; this holds even when the producer's instruction names the move (`film.py:REPLAN`, `instructions.py:INSTRUCTIONS`). No code block: after 3 failed checks in a run the clip is filmed with a `MOVE_CHECK_FILMED_ANYWAY` notice; a check with no answer films with `MOVE_UNCHECKED`.
- **Proof:** Free proof (Nov 9): 41 of 41 right (fails all 4 strap plans, including one reworded "flex outward"; passes 16 passed R13 B-roll and 21 passed R13 talking clips). Strap re-test: without the producer clause 4 of 6 takes gave up; with it all 6 re-planned after one refusal and filmed. Julian: "it works".
- **Status:** In code. Not built: a planner move rule, a halfway picture, a check of the body after filming.

## How to fix a failed clip (Julian, R11)

Don't keep tuning the prompt. Change the picture.

- **After a judge fail**, the scene agent is told never to film the same starting and end picture again (`agents/scene_agent/instructions.py`).
- **Fix order and clip steps**, told once a run at its first judge-failed clip (`agents/scene_agent/lifecycle.py:CLIP_STEPS`): wrong in the first frame, new starting picture; wrong during the move, simpler action; ends wrong or must stop at a set point, end picture. Then the try-another-way ladder's steps 3 to 5: a simpler action, the result instead of the process, a talking scene. The run's first failed picture gets steps 1 and 2 (`PICTURE_STEPS`): hide a wrong part the scene doesn't need, bring a needed part with small print closer, redraw a wrong shape fresh; or film the nearest version that can happen. Every picture and film result says the tries the run has used (#517).
- **End picture cases:** a result where the product touched; a push-in onto the real label; a clip that drifts or must stop at a set point.
- After each failed clip, the agent gets that try's starting picture back with its frames.

Evidence (R11 p03, p05 clip 4301, p09 clip 4239): the same picture retried failed 5 of 6 times; a new picture fixed 4 of 6.

## Tried and dropped

- **Several shots in one clip:** parts got lost at each cut.
- **"The camera stays still" for B-roll:** the real fault was the forced zoom. (Talking scenes have item 18's steady phone.)
- **Fixed phone line "the side facing the camera stays toward the camera":** replaced by the side rule in `VideoForm.camera` and `VideoForm.product_move`.
- **Reference mode (example pictures) for every clip:** it redrew the product in every clip.
- **Page photos with no drawn starting picture (round 7):** backgrounds leaked and parts were invented.
- **A drawn side picture for every product:** 4 drawings were wrong.
- **One "priority line" leading a retry:** fixed the brush's back, then a second brush appeared.
- **HeyGen for talking scenes:** H3 speaks the line itself (ADR 0012).
- **Spelling the label's letters to H3:** Julian: "letters spelled out doesn't really work".
- **A picture gate on label size** (product a third of the picture's height): failed 43 of 63 pictures, and a product in use can't meet it. Replaced by the label rule (item 9).
- **Code refusals of slow, announcer or delivery words, and of re-filming a judge-failed picture pair:** never fired on a real plan or clip, so deleted; the instructions stay.
- **Holding her body still** to stop flips or bends: replaced by the product lock and `product_move` (items 2, 3, 17).

## Still unsolved

- **Hands:** the brush swaps hands (hand round 1, wetbrush v5).
- **Letters drift** on lettered products (item 9).
- **Hair tools and brushes** still move like AI.
- **Dreamfarm Garject** failed every round. Set aside: "even creatify fails this".

## Sources

- Julian's grades: `runs/round-*/julian-grades/` and `runs/hand-rounds/` in the project files.
- Decisions: `decisions/` in the project files.
- In this repo: `docs/lessons.md`, `docs/creatify-ad-lessons.md` §2, `docs/adr/0011`, `docs/adr/0012`, `docs/adr/0012-legacy/`.
