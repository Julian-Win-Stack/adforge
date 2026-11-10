# Round 13: Testing round 12's fixes, plus 3 new products (November 9, 2026)

## What this round tested

- Round 12's fixes, on main 08030f8: six tries each for starting and end pictures, logo and tiny-print fixes for pictures, a natural grip, any body part in no-face scenes, the page's videos and how-to photos used, one product version and size in every scene, plain words for what the product is, new hooks, an ending on her emotional "after", two real camera setups for talking scenes, and checks and rules that never fired dropped.
- Round 12's 12 products plus 3 new everyday ones: LifeStraw Peak Solo, the Dash Mini Waffle Maker and Bee's Wrap. All 15 ran at once on the Azure machine. 14 ads were made; KONG stopped before filming because Azure's content filter blocked a check (#497). Cost $106.36.
- Graded on a grading page: whole ads, plus notes on the clips he chose. Quality was not graded.

## Scorecard

| Measure | Strict rule | By his taps alone |
|---|---|---|
| Whole ads that work | 11 of 15 (73%) | 11 of 14 made |
| Good ad (quality) | not graded | — |
| B-roll clips in the final ads that work | 18 of 21 (86%) | — |
| Talking clips in the final ads that work | 27 of 27 | — |

- The 4 failed ads: Crocs (scene 3), Dash (scene 2), Bondi (scene 2, its last failed try is in the ad) and KONG (stopped, counts as an ad that gave up).
- By taps: he tapped 10 of 12 whole ads pass. Bondi and Hypervolt got scene taps only: Bondi's scene 2 fail, Hypervolt's clip 5076 pass.
- Clip counts cover the 13 ads with page data; Hypervolt is left out (see Caveats).

How these are counted (any bad word is a fail; "works" vs "good ad"): see [the summary](README.md#how-it-was-graded).

## Every failure, and what happened to it

Status words are explained in [the summary](README.md#status-words). Clip numbers are the app's id for one filmed take.

| # | Failure | How often | Bar | Status |
|---|---|---|---|---|
| 1 | Foam applied on skin breaks: it stretches into strands, and in clip 5020 it turned white "like a soap" (Bondi scene 2) | 1 ad, all 3 tries of scene 2 (5000, 5020, 5042); 5042 is in the ad | Works | Fixed, tested Nov 10 (2 of 3 test clips passed; he passed it overall) |
| 2 | Physics break: the heel strap passes through the ankle (Crocs scene 3, clip 4880) | 1 ad, 1 clip | Works | Not fixed |
| 3 | Planned still shot: "the drawer just flew above" (Dash scene 2, clip 4875) | 1 ad, 1 clip | Works | Not fixed |
| 4 | KONG stopped before filming: Azure's content filter blocked the script check as a false alarm | 1 ad | Works | Not fixed |
| 5 | Hair brush breaks physics (Revlon scene 2); round note: "we always fail" | 1 clip named (4862); scene 2 took 4 tries | Works | Known limit |
| 6 | Video model added pink buttons to the brush handle (Revlon clip 4928) | 3 tries failed by the product check for it (4862, 4928, 5003); none in the ad | Works | Not fixed |
| 7 | Hypervolt: the checks never got the photos of the product's back and misread which end has the holes, so a made-up grille passed and real holes failed | 1 ad, 5 clips noted (4934, 4998, 5052, 5062, 5069); none in the final ad | Works | Fixed, rebuilt test-first (rebuild not tested yet) |
| 8 | The clip judge fails details a viewer wouldn't notice | 4 clips Julian passed or called too strict (LifeStraw 5016, Revlon 4862, Hypervolt 4934 and 4998) | Works | Not fixed |
| 9 | A scene agent gives up without trying another action, and each give-up becomes a question to the owner | 6 give-ups in 4 of 14 ads (Bondi, CATAN, LifeStraw, Hypervolt 3 times) | Works | Not fixed |
| 10 | Paid work after the owner said "finish the ad with what you have": 3 more runs of Hypervolt scene 2 ($10.33), and a try 5 after a try the judge passed | 1 ad | Works | Not fixed |
| 11 | The grading page showed each redrawn picture with the previous picture's image | Every scene with more than one picture | Works | Fixed (not tested yet) |
| 12 | Bee's Wrap scene 2 shows the wrapping, not "an already wrapped one" | 1 ad, 1 clip | Good ad | Not fixed |

Details, in the same order:

### 1. Foam applied on skin breaks (Bondi scene 2)

**Fixed, tested Nov 10** · 1 ad, all 3 tries of scene 2; 5042 is in the ad

- **His words:** on clip 5020, "This suddenly become the form like like like a soap. I want you to figure out why this happened." On the scene: "Applying the cream or something on the person's skin always fail" and "why does the planner plans something like this? it cannot show the evidence."
- **Why it happened:** For a product spread on the body, the planner's rules close every other B-roll. The beauty guide allows "hands applying it" but bans a result on skin and before-and-after; a still texture shot isn't B-roll; and a result must be filmed happening. No rule asks whether a page photo shows the substance on the body while applied, and Bondi's page has none (its library labels said a mitt photo existed; the files show no mitt). The video model gets no product photos, so it makes up how the foam behaves. The 8 s clip held only a short sweep, so it lifted the mitt and came back, and each lift is where the judge saw strands. 5020 turned white because its retry prompt dropped "brown", its starting picture was edited until almost no foam showed, and its end picture showed none.
- **His question, "dropped because the agent gave up. Am I correct?":** No. Scene 2 is in the final ad with try 3 (clip 5042), which the judge failed. The scene that was removed was a still foam close-up, cut in planning.
- **What was changed:** With his OK (Nov 9 22:54, talking scenes Nov 10 00:13), any scene, talking or B-roll, films a cream, foam or makeup being applied only when a page photo or video shows it. Otherwise B-roll films the result where it touched, as a page photo shows it, then a dab on a fingertip or applicator, then a talking scene; a talking scene shows that result or dab instead (`planning.py:SPREAD_ON_BODY_RULE`, `plan_rules.py:SPREAD_WHILE_TALKING`). The proven "B-roll must sell" rule and his round 2 "result filmed happening" rule each gained an exception. Test (Nov 10, Bondi, Krave, Glow Recipe): Krave and Glow Recipe fingertip clips passed; the Bondi result clip failed only on a third hand (both her hands in a looking-down shot), which he chose to ignore.
- **What happened next:** Tested Nov 10 on a prototype (Bondi, Krave, Glow Recipe, about $10): he passed it overall; the Krave and Glow Recipe fingertip clips passed, and the Bondi result clip failed only on a third hand, which he chose to ignore.

### 2. Physics break: the heel strap passes through the ankle (Crocs scene 3)

**Not fixed** · 1 ad, 1 clip

- **His words:** "only the 3rd scene which is the b roll scene fails. it is how the clog is set up for heel strap."
- **Why it happened:** The starting picture had the foot in the clog with the strap forward; the end picture had the strap behind the heel. With the foot inside, the strap can only get there through the ankle. Nothing asks "can this move happen from this start?", and the clip judge's motion description can't fail a clip. Same as [round 12 #12](round-12.md#12-physics-break-the-heel-strap-passes-through-the-leg-crocs-scenes-3-and-4).
- **What was changed:** Not on main yet. Another thread tried a move check before filming that refuses an impossible path and tells the agent to re-plan the action the way a real person does it (for example, pivot the strap on the empty clog, then put it on).
- **What happened next:** In the second try, all 6 strap takes were re-planned after one refusal. Julian: "ok. it works." He approved the build on Nov 9; it is being built.

### 3. Planned still shot: the drawer flew (Dash scene 2)

**Not fixed** · 1 ad, 1 clip

- **His words:** "i am pretty sure that this failed in the prompts to the video gen api. the drawer just flew above."
- **Why it happened:** The scene was a still shot where nothing should move. Code's fixed lines told the video model "Movement starts from the very first frame, never a still opening" and opened with "handheld UGC", while the scene asked for a locked-off camera and "No handling, turning, drawer movement" for 7 s. The video model moves what looks movable. In rounds 12 and 13, 4 of 6 planned still shots had something move or appear on its own (round 12's Bissell sprayer too).
- **What was changed:** Not on main yet. The same thread tried a still shot as a camera move pinned at both ends (the end frame is a crop of the starting picture), and no "handheld" in the B-roll opener.
- **What happened next:** Julian passed the re-made Dash still shot in 2 of 2 takes. Build approved Nov 9, in progress.

### 4. KONG stopped before filming

**Not fixed** · 1 ad

- **Why it happened:** Azure's text filter blocked the plan check and the script check as "sexual: medium", a false alarm, and blocked the retry too. The script check doesn't handle a block and filming waits for it, so the ad stopped before any clip ($0.45). The owner saw only "I can't proceed because the required script check won't complete.", with no notice saying why.
- **What was changed:** Nothing. #497 is a reminder for Julian about failing safely when an AI call is blocked; no work is planned.
- **What happened next:** Not tested.

### 5. Hair brush breaks physics (Revlon scene 2)

**Known limit** · 1 clip named (4862); scene 2 took 4 tries

- **His words:** on 4862, "this demo breaks the physics as i said above. not because of the judge's reason for failing." Round note: "for the products that curls the hair or brush the hair with a brush, we always fail." He suggested: "face the product to the camera and cover the hair", and as a fallback, "the person should hold the product and show the hair that is the after effect".
- **Why it happened:** Hair breaks are never caught: the clip verdict comes from the product check alone, and the motion description (for 4862: the brush "completely separated from the hair" while the crown kept "a conspicuous hollow rolled shape") can't fail a clip. In every hair failure where the framing could be seen, the worked hair faced the camera in front of the tool: B-roll hides her face, so a roots shot is filmed from behind, and the side rule turns the photographed bristles to the camera. Framing isn't the whole story: try 4 (5023) had the same rear framing with tiny rolls, the brush never leaving the hair, and it is in the passed ad. Across app rounds, about 5 of 18 graded hair-contact clips passed (28%).
- **What was changed:** Nothing yet. Proposals: (a) a framing rule: the product sits nearest the camera and covers the spot it touches; (b) after repeated fails, a talking scene where she holds the product and shows the finished hair; (c) one question to Creatify about how it stages hair tools. Framing test proposed (about $5 to $6).
- **What happened next:** Not tested. Revlon's ad passed with 5023.

### 6. Video model added pink buttons to the brush handle (Revlon clip 4928)

**Not fixed** · 3 tries failed by the product check for it; none in the ad

- **His words:** "this specific failure is correct - three pink buttons were added to the handle, which should be plain black with its lower setting collar."
- **Why it happened:** Rolling turns the brush and shows sides no photo shows, so the video model invents the handle's hidden side. The product check named pink buttons on the handle in 4862, 4928 and 5003. The agent kept the same framing for all 4 tries.
- **What was changed:** Nothing. The check caught it, so none of these clips reached the ad. The give-up ladder proposal (#9) adds "frame the shot so the part that keeps coming out wrong is small or out of sight".
- **What happened next:** Not tested.

### 7. Hypervolt: the checks never saw the product's back

**Fixed, rebuilt test-first (rebuild not tested yet)** · 1 ad, 5 clips noted; none in the final ad

- **His words:** on 5062 (try 4), "this is wrong. the clip judge doesn't even know how does the back of the product looks like and why did it pass?"; on 5069, "fail. the same as try 4"; on 5052, "i think there is a bit of mistake there but i think we can ship it"; on 4934 and 4998, "i don't see the mistake that the judge flagged."
- **Why it happened:** The library had photos of the gun's back (items 6 and 9: a plain black disc with a power button), but the agent picked only items 10 and 2, and the checks get only the agent's picks, labelled with the agent's own lines. The picture check first called photo 2's ring of holes (on the end of the short cylinder) the back grille. The agent copied that into its labels and drew a grille on the back, and the judge believed the labels. So it failed 4934 and 4998 for holes where photo 2 has them, and passed 5062 with a made-up grille. The image model, picture check and judge did get the same photos, as he asked; the gap is which photos. One ask per check also gave different answers on the same thing (5062 passed, 5069 failed, same starting picture). BlenderBottle 4958's "unsure" had the same limit: the judge got only the one photo the agent picked (his words: "why didn't the judge get more than one image?").
- **What was changed:** Round 13 root cause 1 (spec #514). Code adds every page photo of the product on its own that the agent didn't pick beside its picks, labelled from the page (library label and side). A view in another colour goes as shape only, and the checks judge colour only against the agent's photos. The image model and the picture check get them (#516, `agents/scene_agent/page_views.py:added`). The clip judge gets the same views the starting picture was drawn and checked with, read from the picture's sent record; each of the agent's picks is labelled with what its page photo shows, then "What the agent asked for:" and the agent's line, and the product check adds the colour rule only when views are sent (#520, `page_views.drawn_with`, `page_views.judge_labels`, `jobs/clip_judge.py:sent_references`). First built as #508, reverted for not being test-first, then rebuilt.
- **What happened next:** The first build was tested on Hypervolt 2: with the back views, the judge knew the back is a control panel, and scene 2's clip passed the judge. The rebuild is not tested in a real run yet.

### 8. The clip judge fails details a viewer wouldn't notice

**Not fixed** · 4 clips Julian passed or called too strict

- **His words:** on LifeStraw 5016, "the judge is wrong. i mean i want you to check as well. i dont' see what the judge is saying."; on Revlon 4862, "i think the judge was too strict here. it is correct but a normal human eye wouldn't noticce this. so, i was thinking to add a functionality to let this kind of failures go."
- **Why it happened:** The product check asks only "is this the real product?" and fails a clip on any difference it finds in 66 to 73 stills at full size. It has no way to say how visible a fault is. By the judge's own words, 4 of the round's 9 product fails were fine details of one part (Revlon 4862 and 4836, LifeStraw 5016, CATAN 5014). The LifeStraw fail made its agent give up on a clip Julian passed. Hypervolt 4934 and 4998 also had wrong labels (#7).
- **What was changed:** Nothing yet. Proposal (pick): for a fail, the judge also says whether someone watching once on a phone would notice it; only "anyone would see it" fails, and a fault let go posts a developer notice. The risk is misses: 4862 broke hair physics and was thrown out only by the strict product fail. Test first on his graded clips (about $3 for 30 cases).
- **What happened next:** Not tested.

### 9. A scene agent gives up without trying another action

**Not fixed** · 6 give-ups in 4 of 14 ads

- **His words (chat, Nov 9):** "btw, i see that a scene is deleted and removed if the agent gives up. that logic is quite weird. it should try something else. or we should give the agent instructions on what to try when things fail."
- **Why it happened:** No scene is deleted on a give-up: code keeps the last filmed try in the ad (Bondi kept 5042), and every scene removed in round 13 was cut in planning. He is right that nothing tries something else. 0 of 6 give-ups changed the action first, and 3 named the producer's instruction as the reason. The agent reads that instruction as a lock, B-roll has no fallback steps (talking clips do), no result says how many tries are left (LifeStraw gave up thinking it had used 6 of 6 starting pictures; it had used 5), and the producer's only order on a give-up is to ask the owner. A give-up also waits for every other scene before the producer acts (17.5 minutes in CATAN). The 3 re-runs that passed all changed the action.
- **His other questions:** CATAN scene 1's first run gave up with no clip (holding a loose-tile board up in one hand failed the picture check); its second run put the board on the table and passed (clip 5065, in the ad). LifeStraw scene 3 gave up on the judge's mouthpiece fail (#8), not on pictures; it became a talking scene, which passed (clip 5050).
- **What was changed:** Nothing yet. Proposal (pick): a "try something else" ladder before giving up (hide the part that keeps going wrong, the nearest possible version, a simpler action, show the result, ask the owner once), the producer's instruction treated as a starting point, and a "tries used" line from code on every picture and film result.
- **What happened next:** Not tested.

### 10. Paid work after "finish the ad with what you have" (Hypervolt)

**Not fixed** · 1 ad

- **His words:** on 5062, "and if this passes, why are we doing try 5? explain me." On 5076, "if this passed, why didn't this get shipped? a bug?"
- **Why it happened:** During its first run the agent asked the owner for a video. The owner answered "I don't have that video. Please finish the ad with what you have." Ad v1 was already done, but the producer read this as "remake it from the photos". Each producer note starts a run with a fresh budget of 6 starting pictures, 6 end pictures and 6 clips, so 3 more runs cost $10.33 (the whole chat $17.28). In those runs a check of the producer's instruction, labelled "the owner's change", can fail a try the judge passed: try 4 (5062) failed it for still gliding, so the agent filmed try 5.
- **On 5076:** by the code and the trace it is very likely the clip ad v2 plays, so the page's "in the ad" mark is the likely error. One free read of ad v2 on the box settles it.
- **What was changed:** Nothing yet. Proposal (pick): an answer with no photo or video keeps the current ad, and a new paid run starts only after the owner says yes to its cost; the instruction check fails a try only when the owner asked for the change.
- **What happened next:** Not tested.

### 11. The grading page showed each redrawn picture with the previous picture's image

**Fixed (not tested yet)** · every scene with more than one picture

- **Why it happened:** The page builder took the first picture listed for each draw call, which is the previous picture. So "Drawn picture 4977" showed 4957's image.
- **What was changed:** #499, merged after the round's page was built, picks the picture each call made and shows the image model's and the video model's inputs apart.
- **What happened next:** No grading page has been built since. Separate and still open: the page keeps "agent gave up" on a scene whose later run passed (CATAN scene 1, LifeStraw scene 3), which led to his "is this a bug for creating grading page?".

### 12. Bee's Wrap scene 2 shows the wrapping, not "an already wrapped one"

**Not fixed** · 1 ad, 1 clip · Good ad

- **His words:** "in the second demo scene, it would be better to show an already wrapped one."
- **Why it happened:** The planner did plan an already wrapped shot (carrying the wrapped cheese), but the producer cut it as "does not demonstrate the wrap doing its job", applying the B-roll-sells rule. Scene 2 then filmed the shaping, as the page's how-to words describe; no page photo shows a closed bundle.
- **What was changed:** Nothing. Options: the producer stops cutting a planned showcase of the product in use, or the result rule in #1 is widened to objects. To ask after #1.
- **What happened next:** Not tested.

## Left open

- Foam applied on skin breaks (Bondi scene 2)
- Heel strap passes through the ankle (Crocs scene 3); build in progress
- Still shot: the drawer flew (Dash scene 2); build in progress
- KONG stopped by a content filter false alarm (#497, a reminder only)
- Hair brush breaks physics (Revlon scene 2); known limit, framing test proposed
- Pink buttons added to the brush handle (Revlon)
- Checks never got the product's back photos (Hypervolt)
- The clip judge fails details a viewer wouldn't notice
- Give-ups never try another action
- Paid remakes after "finish the ad with what you have", and a try after a passed try (Hypervolt)
- The grading page keeps "agent gave up" on a scene whose later run passed
- Bee's Wrap should show an already wrapped one

## Caveats

- Hypervolt has no page data, so its clips are left out of the clip counts. The ad counts as working: his fault notes fall on clips not in the final ad, and the one he passed, 5076, is very likely in ad v2 (not proven; #10).
- Quality was not graded. Bee's Wrap's note is the only quality note.
- He graded whole ads and noted only some clips; a clip shown with no note counts as a pass.
- Three clip notes were questions, not grades (CATAN scene 1, LifeStraw scene 3, Hypervolt 5052); the answers are in #7 and #9.
- KONG counts as a failed ad (it stopped before any clip), as round 11's give-ups did.
- Hooks are not listed: #500 changed them after this round, and he named no hook fault.
- His taps and the strict rule failed the same ads this round.

---

← [Round 12](round-12.md) · [All rounds](README.md)
