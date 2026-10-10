# B-roll: which pictures the video model gets, and how it is prompted

- Date: 2026-10-03
- Ticket: #94. Follows the Boreal-H3 check on scene #16 (`docs/runs/second-run-review/check/`).
- Status: the logic is agreed and tested (five tests, 2026-10-03, about $4). Ready to build.
  Items 42 to 56 were settled with the user on 2026-10-05, before building.
  Nothing is built yet. Issue #94 is not to be edited (the user, 2026-10-03).

## Decided

1. **The logic comes first, the tools second.** We agree how a B-roll scene's pictures and
   prompts are chosen, test that, and only then change the app's tools to match.
2. **Which way a scene is made depends on what it shows** (the three steps below).
3. **Start + end is not the main way.** The end picture in the 2026-10-01 test and in the
   scene #16 check was only there to learn how Creatify's API works. It is used only where
   the product itself doesn't change between the two pictures.
4. **Pictures of the product in use come only from the product page or the shop owner**, as
   facts do (ADR 0002). Not from customer reviews, other shops or social posts, and never
   from an image model: an AI-made picture of the gel is just another guess.
5. **No credits are spent before the user agrees to the exact test.**

**The user's decisions on 2026-10-03** (each is written out in its section below):

6. **The video's shape is set with `aspect_ratio: "9:16"`.** Example pictures can be any
   shape; no upright picture has to be made for way 3.
7. **Example pictures (way 3):** real shop photos, untouched; each with one job, said in
   the prompt; two or three, not nine; no printed words when a cleaner photo exists; no
   people (the count and the people rule are replaced by items 27 and 28). The prompt numbers them ("Image 1", "Image 2") by the order they are sent.
8. **The starting picture (way 1) gets only the product photo,** used only for how the
   product looks. No in-use photos and nothing from the shop's backgrounds: the setting is
   the ad's choice, described in words.
9. **The scene shows the product doing what the line claims,** not standing idle beside the
   action.
10. **Three forced steps:** read the usage from the page's "how to use"; plan the scene with
    it; check the picture before paying for the video.
11. **End on the result.** The starting picture sets up the "before"; the video prompt does
    the action and ends on the result, described in words. No end picture.
12. **Two kinds of scene,** one prompt-writing agent with a rule set per kind: "does a job
    you can see" (the result comes from the product being used) and "showcase"
    (using or wearing it the way the line claims is the proof; since 2026-10-09 no longer
    the default when unsure: a middle scene no kind fits asks the shop owner, else is
    said to camera).
13. **The clip isn't cut where the spoken line ends.** It plays to its end.
14. **Only the presenter is shown.** Any hand is fine. Tested after building; the production
    prompts carry the rule from the start.
15. **Framing:** the prompt says only that the main action is in the middle. Nothing about
    the top and bottom. No test needed.
16. **The judge waits for the critic.** It will watch the whole video and is first tested
    against the user's grades. Until then there is no automatic check of the video.
17. **Build now; test after building.**
18. **Where the judge's questions come from (2026-10-03).** Two places. Fixed questions,
    written once in code, for every scene ("Does the product look the same as in the shop
    photo?"; for "does a job" scenes, "Is the promised result visible by the end?", with the
    result filled in). Usage questions, written by a model per product from the usage fact,
    with strict rules: ask only what the thing judged can show (nothing about movement for a
    still), only what the usage fact says, and only a few.
19. **When the judge says "no" to a picture (2026-10-03).** It is made again, up to 2 more
    times, with the judge's reason added to the prompt each time. If it still fails, the
    scene is made simpler but still true to the line. As a last resort it becomes a talking
    scene, and a grey notice says so. The shop owner is never asked because a picture failed.
20. **When the shop owner is asked (2026-10-03).** Only when information is missing, and
    only at planning, before any picture is made: a scene needs a photo of something no
    photo shows (such as the toilet cleaner's gel), or a "does a job" product's page has no
    "how to use". The message offers three answers: describe it in words, attach a photo, or
    "Use without proven result", where the app says how it will show the scene and the shop
    owner approves or changes it. What the shop owner types is used as written (a
    user-supplied fact).
21. **Two evals guard this (2026-10-03).** "Should we ask?": written situations, each with
    the right answer; the app's choice must match every time. "Wrong no": pictures the user
    graded as good; the judge must not say "no" to them.
22. **Three builds, one at a time (2026-10-03),** so each is debugged on top of something
    that already works. Build 1: the new B-roll logic with no judge at all; the picture check
    in item 10 waits for build 2. Build 2: the picture judge (items 18 to 21). Build 3: the
    video judge, once it agrees with the user's grades (item 16). Build 1 saves each scene's
    usage fact and promised result, so the judge can be tried on its clips later. Whether
    builds 2 and 3 are worth it is decided from how many of build 1's clips come out wrong.
23. **The gap after a B-roll line (2026-10-03).** The next line starts over the end of the
    B-roll clip (an early cut), so the voice never stops and the ad lasts as long as its
    voice: the length check doesn't change. When the next scene is a talking one, its first
    part is skipped by the same amount, so the lips still match the words. A B-roll scene
    that ends the ad plays its end over the music only. Each B-roll clip is asked for the
    fewest whole seconds that cover its line (at least 5), so gaps stay small.
24. **A B-roll line takes at least 4 seconds to say (2026-10-03).** Boreal-H3 makes only 5 to
    15 whole seconds (Creatify publishes no way around it), so a short line leaves a long
    gap, and a short next line could be said entirely over the end of the clip. With 4 s,
    the next line starts over at most about 1 s of the clip.
    - The planner is told only "a B-roll line has at least about 10 words", with no upper
      number: any upper number in the hint makes the planner squeeze good lines to fit it.
      This rough number, from a typical speaking speed, is the one exception to "no fixed
      words-per-second value": it is only a writing hint, never a decision.
    - After the voice is measured, only two things force a rewrite of that one line (as a too
      long line is today): under 4 s, or over 14 s (the clip can't pass 15 s). Anything
      between is left alone: no squeezing and no splitting a scene to fit.
    - Tested before it is built: the planner is run several times on the saved test products
      (text only, no pictures or videos) to see how long its B-roll lines come out.
25. **The higher cost of B-roll is accepted (2026-10-03).** Boreal-H3 costs about $0.40 for
    a 5 s clip, against about $0.05 for the old Boreal; the user doesn't worry about it.
26. **Photo notes (2026-10-04).** The photo picker notes each photo it keeps while picking.
    The model fills in fixed choices only, and plain code reads them. One field: **Face:
    yes/no** (only a stranger's face matters; a body or a hand can't be recognised). No
    added-text field (item 29). No sentence of what it shows: the planner already sees the
    photos themselves. No "product alone" field: way 1's image maker gets the shop's main
    photo (the first on the page), and the prompt says to take only the product from it.
27. **Example pictures and people (2026-10-04).** Replaces item 7's "two or three" and "no
    people".
    - Up to 5 pictures in all, the presenter's portrait counted (5 are free). Only what the
      scene needs, each with its job named. Cut back only if tests show the model mixing
      them up.
    - The planner labels each B-roll scene "person in the scene: no face / has face". Code sends the presenter's portrait when it is "has face".
    - Shop photos with a face may be sent, with the portrait, and the prompt says to
      use the presenter. Tested after building.
    - Not yet decided: a photo with a face for a scene that shows no person, and
      photos with printed words. Decided in items 28 and 29.
28. **Which shop photo does a job (2026-10-04).**
    1. A photo without a stranger's face for the job, if there is one (code picks it from
       the photo notes).
    2. Otherwise the photo is sent whole, and the prompt names its job and what to ignore
       ("Image 2 is only for the gel's colour. Don't show the woman in it.").
    3. When the scene shows a person, the presenter's portrait goes too, and the prompt says
       to use the presenter.
    - Cropping a photo to its useful part (a model gives the box, code cuts, and the photo
      note is run again on the cut photo) is plan B, built only if tests show the shop's person
      leaking into videos.
    - Tested with videos after building, first with `prompt_enhancement: auto` (the setting
      Creatify measured Boreal-H3 with), then with `none` if the shop's person leaks.
    - Tested before building, pictures only: do the photo notes mark faces the way the user
      would?
29. **Added text on photos is ignored (2026-10-04).** Photos with text put on top (banners,
    headlines, charts, Supplement Facts) are treated as normal photos: no field, no rule, no
    prompt line. Counted on the test products' 111 photos: 18 have added text, but 10 of the
    collagen's 12 and 7 of the power bank's 12 (Amazon-style listings), and the in-use photos
    of both are among them (`docs/runs/second-run-review/added-text-photos.jpg`). One test
    (collagen) sent such a photo and nothing was copied. Watched for in the after-build
    tests: text drawn into a clip can't be taken out.
30. **How build 1's clips are judged (2026-10-04).** The user watches each B-roll clip and
    grades it pass or fail, writing down what went wrong on each fail. No list of rules is
    fixed beforehand: there aren't enough clips yet to know the real failure types. The
    user's list of what went wrong becomes the judge's questions in builds 2 and 3.
    - The products (`docs/test-products.md`): the six already tested on B-roll (2 blush,
      3 collagen, 5 bag, 6 power bank, 7 pot, 8 toilet cleaner), plus 1 the serum (one
      photo: does the app ask instead of making something up?) and 9 the chili crunch
      (food: only foods the page names).
    - If more than 1 in 5 B-roll clips fail, build 2 (the picture judge) is worth building.
      If almost all pass, the judge waits.
31. **Build 1's edges (2026-10-04).** Start + end stays out of build 1. Boreal-H3 replaces
    the old Boreal for B-roll, and the old adapter's code is removed (talking scenes use
    HeyGen). `CONTEXT.md` is updated where it no longer matches: "Clip" (B-roll clips aren't
    cut to the line), "Finished ad" (the early cut), "Starting picture" (way 3 has none).
32. **Asking the shop owner is in build 1 (2026-10-04).** Item 20 (asking at planning when
    a needed photo or the "how to use" is missing) and its "Should we ask?" eval move from
    build 2 to build 1: it is the planner asking, not the judge. The "Wrong no" eval stays
    in build 2.
33. **The one real ad at the end of build 1 needs no permission (2026-10-04).** It is one
    ad, to check that everything runs. Every other paid run still needs the user's yes.
34. **Two B-roll scenes in a row (2026-10-04).** The second B-roll clip plays from its start,
    so its "before" isn't lost; its line starts over the end of the first clip as usual, so
    its picture runs up to about 1 s behind its line (no lips to match). The next talking
    scene skips that much more at its start, so its lips match.
35. **A B-roll line's length is never asked about (2026-10-04).** It is the app's problem,
    not the shop owner's. A line still under 4 s after 2 rewrites is kept: the next line
    starts over more of the clip's end. A line over 14 s is shortened, up to 3 times (a
    limit, so a bug can't keep paying for rewrites). One still over 14 s means something is
    wrong in our logic or prompts: that scene becomes a talking scene (talking clips last
    up to 18 s), so the shop owner still gets the ad, and the job records a warning so we
    find and fix the cause.
36. **A line whose real audio is over 15 s is rewritten (2026-10-04),** shorter, without
    telling the shop owner, then its audio is made again before the clip is paid for.
37. **Build 1 is built on the current branch (2026-10-04),** not a new one.
38. **Every photo gets a Face note (2026-10-04).** Photos the picker never saw (ones the
    shop owner attaches, or the page's declared photos when the picker fails) get their own
    small Face-note call when they are saved.
39. **A rewritten B-roll scene is rewritten whole (2026-10-04).** When the fact check sends
    a B-roll scene back, the rewrite gives back its line, "shows", kind, person, usage fact,
    result and needed photos together, so they always match.
40. **Way 3 needs no new tools (2026-10-04).** The producer calls the same tools as today.
    Inside "make starting picture": way 1 makes the picture; way 3 makes none, and picks
    the shop photos and writes the prompt. Inside "make clip": way 1 sends the picture;
    way 3 sends the photos. The producer never knows which way a scene uses.
41. **A picture from the shop owner's words comes later (2026-10-04).** When the shop owner
    has no photo and describes the thing in words, a picture could be made from their words,
    which they approve, reject or give feedback on. Once approved it is their fact, not our
    guess, so it doesn't break item 4. Not in build 1: there the words go into the video
    prompt. Ticket #95, built if the graded run shows clips from words alone come out
    wrong.
42. **"Describe it in words" is not offered in build 1 (2026-10-05).** Replaces item 20's
    and item 41's "the words go into the video prompt". When a needed photo is missing, the
    question offers only "attach a photo" or "use without proven result". Answering in words
    comes back with #95.
43. **The ad's colour is the one with the most clear photos (2026-10-05).** The planner
    still picks one colour per ad (so an ad never mixes colours), but now picks the colour
    with the most photos where the product is clearly seen; on a tie, the colour of Photo 1
    (the first photo on the page).
44. **A needed photo can be any photo except one showing the product in another colour
    (2026-10-05).** Replaces "one of the job's photos in the ad's colour" (the marked
    photos): in-use photos (blush on a cheek, gel in a bowl) are often not marked because the
    product isn't clearly seen in them. The planner is told the rule; code can't check
    colour, since no colour is saved per photo. Whether to save one is decided after #110.
45. **The main photo is picked by the existing picture chooser (2026-10-05),** for way 1 and
    way 3 alike. Replaces item 26's "the first photo on the page": the first photo may be a
    poor one. The chooser picks the best marked photo for the scene, as it does today.
46. **The fact check also sees each B-roll scene's needed photos (2026-10-05),** not only the
    marked photos, so a scene a needed photo proves (the gel's colour) isn't failed wrongly.
47. **A line whose real audio is over 15 s is shortened up to 3 times (2026-10-05).** Adds a
    limit to item 36. Still over 15 s: the scene becomes a talking scene and the job records
    a warning, as in item 35.
48. **No photo clearly shows the product: the planner asks (2026-10-05).** New rule: the
    shop owner is asked to attach a photo of the product. A case for it is in the "Should we
    ask?" eval.
49. **The "Should we ask?" eval (2026-10-05).** 8 cases: a needed photo missing (ask); "how
    to use" missing on a "does a job" product (ask); a "showcase" product without "how to
    use" (don't ask); everything present (don't ask); the serum with one photo, a scene
    wanting its texture (ask); the blush with a cheek photo (don't ask); the power bank shown
    charging a phone, no photo of it, the page says it charges phones (don't ask); no photo
    clearly shows the product (ask). Claude drafts the cases, the user approves them, then
    each runs 3 times, reported as a pass rate per case. The user approved the cost.
50. **No judge until the graded run, and no B-roll for real shop owners until a judge exists
    (2026-10-05).** Confirms item 22. The decision is tracked in #109.
51. **What build 1 doesn't measure is tracked (2026-10-05):** the fact check's accuracy and
    claims made across lines (#108), the photos the colour list throws away (#110), and
    evals for every AI call (#111). None of them blocks build 1.
52. **The second kind is named "showcase" (2026-10-05),** replacing "looks good", which read
    as confusing. Same meaning: the product shown at its best. "Does a job" stays.
53. **Only faces matter for people (2026-10-05).** "Who is in it" has two labels, "no face"
    (nobody, or only a hand or body) and "has face" (the presenter's face is shown; the
    portrait is sent). Replaces item 27's three labels. A hand or body is never a concern.
54. **The after-build tests are approved (2026-10-05):** the graded run with the people and
    added-text checks, about $35 to $50 plus re-runs; ask again only if the counted figure
    is over $50. Every result goes into "Test results".
55. **Way 3's picture labels come from structured output (2026-10-05, the user's idea).** The
    prompt writer's answer has one required slot per picture (`image_1` ... `image_N`) plus
    `action`; code joins them into "Image 1 is ... Image 2 is ... <action>". No picture can
    be left unnamed, and the AI still writes each picture's hint.
56. **A B-roll scene that becomes a talking scene is shown in the chat (2026-10-05).** The
    warning of items 35 and 47 is posted as a notice with the reason ("Scene 3 couldn't be
    made as a product shot because its line is too long for a clip, so it will be said to
    camera instead."). The one exception to "the user is never told which scenes are which".
    Only that scene changes; the rest of the plan stays.
57. **Every B-roll scene gets a drawn starting picture; way 3 is gone (2026-10-08).** Shop
    photos are no longer sent to the video model as example pictures: their own scene
    leaked into the clip (#12), and a wrong "need" that skipped the starting picture let
    the video model invent a dirty tile (N3 Lemi Shine). A scene's needs photos now go to
    the picture model with its main photo, and the drawn picture is checked beside the shop
    photo before the clip is paid for. Items 7, 40 and 55, and "references" in "The logic"
    below, describe the old way 3 and are kept as history.

## What Boreal-H3 takes

From Creatify's API page (Create a Boreal task) and the scene #16 check.

| Way | What the picture is | Good for |
|---|---|---|
| Start picture (`image_url`) | The exact first frame | The framing is decided; the video moves forward from it |
| Start + end (`end_image_url` too) | The exact first and last frames | The move between two real compositions; the end must be reachable |
| References (`reference_image_urls`, up to 9) | Guides for how things look, not frames | Showing the model what something looks like |

- The ways can't be mixed: references with a start or end picture are refused (400).
- 5 to 15 whole seconds, 0.4 credits a second at 768p. The first 5 references are free.
- In the prompt, references are named by order: "Image 1", "Image 2".
- `prompt_enhancement` is `auto` (Creatify rewrites our prompt, unseen) or `none`. For
  Boreal-H3, `auto` "applies the rewrite the model was evaluated with", and Creatify's
  launch numbers were measured with it ("the same rewritten prompt"). How it rewrites isn't
  published; likely into the structured layout H3 was trained on (a guess).
- **The video's shape, to verify.** The API page's words for `aspect_ratio` (read
  2026-10-03): "auto follows the start image's own ratio (boreal-h3: the first reference
  image's), and is 16:9 without an image. A named ratio renders that ratio; with a start
  image, boreal centre-crops the image to it, and boreal-h3 takes only auto." And for
  `image_url`: "boreal-h3 always follows the image's ratio."
  - With a start picture: the video copies the start picture's shape. Sure.
  - With references and `auto`: the video copies the first reference's shape. Sure (the
    scene #16 check gave a square video from a square photo).
  - With references and `aspect_ratio: "9:16"`: **works** (tried 2026-10-03). One square
    shop photo (Merit blush, 2000×2000) as the only reference gave an upright 768×1376
    video, 2 credits. So the first reference doesn't have to be upright, and no picture
    has to be made for references. Kept in `docs/runs/second-run-review/check/shape-check/`.

## The logic: which way for which scene

Since item 57 (2026-10-08), step 1's "references" is no longer built: every B-roll scene is
made from its starting picture.

1. **Does the scene show something the pack photo can't?** Such as gel coming out, blush on
   a cheek, powder in a drink: a texture, a colour, the inside, a result.
   - **No real photo of it:** the scene isn't made. The planner asks the shop owner to
     attach a photo or use the scene without a proven result (item 42).
   - **A real photo of it:** use **references**, so the model sees that photo.
2. **Otherwise** (the product standing, picked up, turned, a close-up of the pack): use the
   **start picture only**. We control the framing and nothing has to be invented.
3. **Start + end:** only when the product doesn't change between the two pictures, such as
   a camera move or the product being put down.

## Only the presenter is shown (decided 2026-10-03)

A B-roll scene that shows a person shows the ad's presenter, nobody else. Found in the
blush test: the shop's cheek photo (Image 2) carried its model into the video though the
prompt said she wasn't the woman in it; and the bag test's woman was made up. That test
sent no presenter's portrait, so the model had no other woman to use, and
`prompt_enhancement` was `auto`, so Creatify may have rewritten the sentence. A people photo
sent together with the presenter's portrait has never been tried (corrected 2026-10-04).

- The presenter's portrait is given to the picture maker (way 1) or as an example picture
  (way 3) whenever the scene shows a person (item 27).
- Shop photos with a face may be sent (item 27, replacing "never sent"), with the
  presenter's portrait and a prompt that says to use the presenter. Tested after building;
  if the shop's person leaks into the video, they stop being sent.
- Tested after building, not before (decided 2026-10-03). The production prompts (the
  picture maker's and the prompt-writing agent's) must carry this rule from the start.
- A hand alone (the toilet cleaner's) can be any hand; it doesn't have to match the
  presenter (decided 2026-10-03).

## Using the product right: three forced steps (agreed 2026-10-03)

Why: the toilet cleaner's test went wrong twice. First the scene had the bottle standing
idle, copied from the plan without checking the page's "how to use". Then the redo's prompt
said "nozzle tucked under the front rim", but the picture showed the nozzle hanging in the
open bowl, with the label upright on an upside-down bottle, and nobody compared it with the
"how to use" before the video was offered. The rule "held the way it is really used" was
already here; nothing forced it. These steps force it, so the app can do it every time.

1. **Read the usage.** From the page's "how to use", write one short usage fact: where the
   product goes and how it is held. Toilet cleaner: "the nozzle goes under the rim; squeeze;
   the gel runs down the bowl; scrub."
2. **Plan with it.** The scene shows the product doing what the line claims, the way the
   usage fact says.
3. **Check before paying.** After the picture is made, a checker model looks at it and
   answers yes/no questions built from the usage fact ("Is the nozzle under the rim?", "Is
   the label the right way up for how the bottle is held?"). Any "no": the picture is
   remade. The video is paid for only once every answer is "yes". Questions ask only what
   a still picture can show: "touching the ring but not yet scrubbing" got a "no" on a
   good picture, because movement can't be seen in a still.

**End on the result (agreed 2026-10-03).** Why: the toilet cleaner's redo showed the gel
going on but never the stain going away, so it proved nothing the line promised ("fights
rings and stains").

1. **Read the usage and the result.** Step 1 above also writes the result the product
   promises, when it has one you can see. Toilet cleaner: "the stain ring is gone and the
   bowl is clean."
2. **The start picture sets up the "before"; the video prompt ends on the result.** The
   picture shows what the result will change (the stain ring visible, with the gel on it),
   so the result has something to change. It doesn't show the result. The prompt-writing agent describes the result
   happening on screen as the clip's ending. No end picture: it is described in words. An
   end picture (start + end) is only a fallback if the video maker keeps not reaching it.
3. **Check the video** once it is made: "Is the promised result visible?" If not, it is
   made once more. Not trusted yet: see "The judge" below.

**Two kinds of scene (agreed 2026-10-03).** The planner labels each B-roll scene with a
kind. The prompt-writing agent gets the shared rules plus only that kind's rules, so a rule
for one kind can't be misused on another. One agent with a rule set per kind, not one agent
per kind: the shared rules would be copied into each and drift apart, and a new kind is a
small rule set, not a new agent.

| Kind | Examples | Its own rules |
|---|---|---|
| Does a job you can see | toilet cleaner, blush, collagen powder, power bank, blender | The result comes from the product being used; end on it |
| Shows the problem (added 2026-10-09) | a shower's soap scum, a stained mug, before the product is used | The problem shown plainly in the starting picture with the product in view; nothing about it changes; the one movement is a hand or the product |
| Showcase (used or worn as the line claims) | a bag carried as a clutch, clothes worn | Used or worn the way the line claims; never only held, placed or pointed at |

Until 2026-10-09, when the planner wasn't sure, it picked "showcase": it can't invent a
result. Round 2's showcases (a hand holding, setting down or standing up the product) all
failed (#12 s2, #8 s4, N3), so now a middle scene that no kind fits asks the shop owner for
what would show it, and is said to camera if they can't give it. A result is filmed
happening, never a finished thing with the product beside it (#8 s4). First drafted
as three kinds (changes something, worn or carried, other); "worn or carried" and "other"
were merged because their rules were the same, and gadgets moved to "does a job", because a
gadget shown only looking nice never proves it works.

- **Does a job you can see.** *The result comes from the product being used:* the change
  happens because the product (or the tool used with it) acts, not by itself. The clip ends
  on the result. It is not "nothing changes except under the brush": gel already on a stain
  loosens it wherever it touches, and that is normal (the user's grade of the scrub retry).
  A result shown on
  a screen is shown without numbers or words (the charging light comes on, not "80%"): video
  makers garble them.
- **Showcase.** The product used or worn the way the line claims, such as a bag carried as
  a clutch (#5, graded PERFECT); never a hand only holding, placing, setting down or
  pointing at it (round 2, 2026-10-09); ending on what the line proves, filmed; no result or change the page doesn't prove. (Until
  2026-10-08 it ended "at its best" with "a slow camera move" allowed: the forced ending made
  pointless zooms in #15 and #4, and the user graded the retest without it "a lot better".)

The checker's questions come from templates, not written by hand:
1. Is the promised result visible by the end, coming from the product being used? (does a
   job)
2. Does the product look the same as in the shop photo? (every kind)

A third template, "Is everything else unchanged where the action didn't happen?", was
dropped: it failed a clip the user passed.

**The judge (not trusted yet, 2026-10-03).** The checker and Claude both judged the toilet
cleaner's scrub clips from still frames (the checker from the last frame only, Claude from a
strip of six) and both called a clip the user graded perfect a fail. A still can't show how
the action unfolds. Plan, not built: the judge watches the whole video, many frames in order,
and is first tested against the user's grades of the clips already made (blush, collagen,
pot, bag, and the four toilet cleaner clips). It becomes the critic only once it agrees with
the user on those; where it disagrees, its questions are fixed, not the grades.

**The clip isn't cut to the line (decided 2026-10-03).** Today the app cuts each B-roll clip
where its spoken line ends, which can cut off the action's end (the result). The clip now
plays to its end even after the line has finished. The next line starts over its end, so the
ad's length check doesn't change (item 23).

## Prompt rules

From the MiniMax H3 prompt guides (Boreal-H3 is built on H3) and the four mistakes in #94.

**Every B-roll prompt**
- The product does what the line claims, on screen (decided 2026-10-03). The toilet
  cleaner's first test showed the bottle standing idle while a hand scrubbed: it showed
  nothing the line says ("the clinging gel fights rings and stains") and was graded useless.
  Its redo shows the gel coming out over a hard-water ring.
- One continuous shot, doing what "shows" says in order at a natural, real-time pace. No
  seconds, timings, "slowly" or "gently" (2026-10-08): "one action per 2 to 3 seconds" gave
  stopwatch timings, #5 "3 actions in 6 s" and #7 "far too slow", both failed by the user.
- One movement per clip, no exceptions (the user's decision, 2026-10-08): the plan's "shows"
  says which movement (a hold-up "shows" was turned back into the drop its line claims,
  #12, 4 runs of 4). When it lists several, the one where the product or its tool does the
  work is filmed, not putting the product on before it or rinsing after it ("the one that
  proves the line" picked the toilet's flush over the scrub graded perfect). A person or hand
  "shows" names is in the clip (the bag scene's wearer was left out). The
  voice carries the rest. The fiddly change between two
  states (clipping, unclipping, folding) is never filmed. Why: the toilet scrub-only clip was
  graded perfect; the bag clip that filmed the strap change failed, one clip per state
  passed; "two or three actions that flow as one" let clamp + rotate + glide through on a
  curling iron and the video model skipped the middle. The planner owns splitting such
  lines; this is the prompt writer's backstop.
- The product, or the tool used with it, acts; the product may stand in view when holding it
  would bend its shape (2026-10-08: the toilet clip the user graded perfect had the bottle
  standing on the tank; holding it bent its neck).
- A job's result shows only where the product or its tool touches, and nothing else
  changes; it happens as the touch passes, never all at once or time-compressed. When a
  tool works after the product is put on, putting it on is never filmed or drawn (the user,
  2026-10-08: "just scrub it and then that scrub area become clean", "you don't need to even
  put in the gel at all"; the toilet clip's whole ring had vanished where nothing touched it).
- Never two orders that can't both be true ("upright" + "nozzle pointing down" bent the
  bottle; "squeeze" + "no gel" put gel on the rim). Each order is checked against the photos.
- Code starts every B-roll video prompt with "A {N}-second video at real-time speed. The
  camera stays still.", N the clip's real length; the video prompt no longer says "9:16"
  (the API sends it), and never moves the camera or says where or how close it ends. Until
  2026-10-09 it opened "A {N}-second handheld phone video, casual, not cinematic, real-time
  speed.", as both best-graded clips did; the test ads of 2026-10-08 all looked
  phone-filmed (the user: "there is no extra benefit"), and "handheld" moves the camera,
  against "the one movement in a B-roll is a hand or the product, never the camera" (the
  user, 2026-10-08 23:57). No look words either way: the video model chooses.
- The starting picture's prompt opens "An upright 9:16 photo in a real, ordinary <place>."
  (no "taken on a phone", no "casual", since 2026-10-09) and says plainly what must be
  right: where the camera is, its height and angle, every part of the object named as a real, ordinary one, the problem as it
  looks before, counts and left/right, the hand at the first moment. A product in an unusual
  pose gets the pose said once and what it does to each part (the label turns with it).
- The product is held the way it is really used, taken from the page's "how to use"
  (the toilet cleaner's bottle is used upside down, not with the nozzle pointing up).
- The prompt says only that the main action or product is in the middle of the frame, and
  nothing about the top and bottom (decided 2026-10-03). "The top and bottom stay empty"
  made the video maker paint pale strips and black bars there. If a test shows the overlay
  or captions covering the product, the first thing to add is "with some space around it".
- The product is shown, not described (decided 2026-10-03). The model always gets a real
  photo of the product: as a reference, or inside the start picture, which is made from
  the real photo. The prompt doesn't describe the product's shape, colours or brand name
  in words. If no reliable photo exists, the shop owner is asked to attach one in the
  chat. The H3 guides advise naming the details in words as well; that is untested, and
  is the first thing to try if the product changes shape in the test.
- The prompt doesn't say "no speech, no sound, no added text" (decided 2026-10-03). Code
  already throws the clip's sound away and lays the line's audio over it. Text the model
  draws into the picture can't be taken out afterwards, so the test watches for it.

**References**
- Say what each picture is for, by number: "Image 1 is the bottle: keep its shape and label
  exactly. Image 2 is the gel: its colour and how thick it is."
- A picture with several things in it gets one stated job, so the model doesn't copy the
  wrong part.

- Which pictures to send (agreed 2026-10-03):
  1. Real shop photos, untouched.
  2. Each picture has one job, and the prompt says it.
  3. Two or three pictures, not nine. (Replaced: up to 5 in all, item 27.)
  4. Clean photos first. (Replaced: photos with people are sent whole when no other photo
     does the job, with the prompt naming what to ignore, item 28; added text is ignored,
     item 29.)
- The video's shape is set with `aspect_ratio: "9:16"`; the pictures can be any shape.

**Start picture (way 1)**
- The picture maker gets the shop's real product photo (plus the presenter's portrait for a
  "has face" scene, item 27), and the prompt uses the photo only
  for how the product looks (decided 2026-10-03). No in-use photos, and nothing from the
  shop's photos' backgrounds or scenes: the setting is the ad's choice (a cup can be shown
  on a beach), so it is described in words in the prompt.

**Start + end**
- The prompt describes the path, not the pictures: opening state, the action, the final pose.
- Before the video is paid for, the pair is checked: the product is unchanged, the end can
  be reached in the clip's length, the product is held as it is really used, and the action
  is clear of the text areas.

## What we don't know yet


- Whether references keep the framing and the clear text areas without a start frame, and
  whether using our own 9:16 starting picture as Image 1 fixes that.
- Whether the label stays readable in a reference clip (it was tilted and partly out of
  frame in the scene #16 check).
- Whether `prompt_enhancement: none` gives better or worse clips than `auto`.
- Whether "Show only what is described; add or change nothing about the product", which
  the app adds to every B-roll prompt (`NOTHING_MADE_UP` in `backend/jobs/scenes.py`), does
  anything. From the runs so far it seems to do nothing. Decided 2026-10-03: the test
  prompts leave it out and rely on the real photo of the product; it isn't compared with
  and without.
- How to make an end picture without the image model redrawing the product. Until there is
  an answer, step 3 stays narrow.
- Whether the photo picker (rebuilt in #92) now keeps Merit's five "worn on a cheek" photos.
- Whether Creatify has its own Boreal-H3 prompt guide. Only the API page and guides for the
  base model were found.

## The test (done 2026-10-03; results below)

Checks the logic on scenes from the second run that already have real photos
(`docs/runs/second-run-review/picked_photos.json`). All 5 s at 768p, 2 credits a clip.

| Step of the logic | Scenes | What is sent |
|---|---|---|
| 1, references | Merit blush (photo: worn on a cheek), collagen powder (photo: a scoop poured into a mug), Dutch Baby pot (photo: cooking with food in it) | A 9:16 picture first for the framing, the pack photo, the in-use photo, and a prompt that names each one's job |
| 2, start picture only | Two scenes that only show the pack, such as the bag and the power bank | The starting picture and a motion prompt |

The exact scenes, pictures and prompts are written out and shown to the user before any
request is sent. Five clips would cost 10 credits, about $2.

What a clip must pass: the product keeps its shape and label; nothing is shown that the
photos don't show; the action could really happen; the action is clear of the text areas.

## Test results

Way 3 (references), 2026-10-03, 7 credits. Clips, prompts and frame strips in
`docs/runs/second-run-review/check/refs/`. All upright 768×1376 from square or wide photos.

- **Blush (02-2):** the action is right. The woman from Image 2 became the woman in the
  video, though the prompt said she isn't. Pale see-through bands were drawn at the top
  and bottom.
- **Collagen (03-2):** the tub and the spoon pouring are right; no words copied from
  Image 2's printed text. The tub's name is cut off at the left. The user noted the
  coffee's colour doesn't change once the powder is stirred in.
- **Pot (07-4):** beef bourguignon and stirring, as asked, not Image 2's soup. Black bars
  were added at the top and bottom.
- **Toilet cleaner (08-3), ending on the result described in words:** the brush scrubs and
  the stain comes off and washes down into the water. The user: the stains were removed,
  and the checker's "no" ("Is the stain ring gone?") was wrong. Flaw the user noted: stain
  came off where the brush hadn't scrubbed. Kept as `smear-08-3*`.
- **Toilet cleaner retry, the result tied to the scrubbing in the prompt:** **passed**, graded
  perfect by the user. The ring fades as it is scrubbed and runs down. The checker said no
  to both questions, and Claude, from six still frames, called it a fail: both wrong.
- **Likely cause of the bands and bars:** "the top and bottom stay empty" in the prompt
  made the model paint empty strips. The pot's bars may also come from Image 2's black
  border. Fix decided: the prompt only says the main action is in the middle (see "Every
  B-roll prompt"). Not tested on way 3, and won't be: the user judged it safe.

## Sources

- Creatify, Create a Boreal task: https://docs.creatify.ai/api-reference/boreal/post-boreal.md
- fal, MiniMax H3 prompting guide: https://fal.ai/learn/devs/minimax-h3-prompting-guide
- RunDiffusion, MiniMax H3 prompt guide: https://www.rundiffusion.com/minimax-h3-prompt-guide
