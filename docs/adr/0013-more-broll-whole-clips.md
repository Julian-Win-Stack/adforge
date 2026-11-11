# More B-roll scenes, each a whole clip, and talking scenes that look different

Extends [0012](0012-every-scene-made-with-h3.md): every scene is still one whole H3 clip. Approved
by Julian on Nov 6 2026 (05:48 to 06:02, spec #401, ticket #403). His words and the reasons: project
files `handoffs/ad-quality/findings.md`.

**What we saw.** Round 11 (Nov 5): 28 of 32 scenes were talking, B-roll was 13% of ad time and
always scene 3, and 4 of 8 ads had no B-roll at all. 25 of the 28 talking scenes only held the
product to the lens. Julian's grades: too many boring talking scenes; "no b roll" on two ads;
talking scenes should look different from each other (a selfie with the phone in one hand, then
standing to camera) with the product in a different state each time, out of the box then in its
box, as in Creatify's floss ad; and at least one scene centred on the product even when a demo is
hard.

**What we decided.**

- Middle scenes (not the first, not the last) are B-roll by default. A middle scene may be
  talking only in the style "talking while doing it", with her using the product on herself.
- Every ad of two or more scenes has at least one B-roll scene, centred on the product. When a
  real demo is hard, it films the moment before, the feature up close or the feeling after (the
  floss pulled out of its box and drawn in and out).
- Only the first or the last scene may just show the product to the lens. This narrows the
  proven talking-scene exception (`docs/agents/proven-broll-rules.md`), with Julian's OK.
- Each talking scene names a talking style (selfie, propped waist-up, seated close-up, talking
  while doing it; no walking, no mirrors) and a product state (in its box, out of the box, in
  use, beside its box), each different from the talking scene before it, B-roll between them or
  not. A box state needs a page photo of the packaging. The scene agent is told both as scene
  facts and draws the starting picture in them.
- Each rule is written once in `jobs/plan_rules.py`: the planner's prompt quotes it, a plan
  breaking it is sent back in the same words, and a plan edit (edit, add, remove, reorder) or a
  scene agent's `change_scene` that newly breaks it is refused (the #390 pattern).
- Update, Nov 9 2026 (rules review): none of these rules sent a plan back in round 12, so a new
  plan breaking one goes ahead with a developer notice (`PLAN_RULE_NOT_KEPT`); only the last
  scene talking (`REFUSED_AT_PLANNING`) is still sent back. Julian's rule: a check that never
  fired is deleted and its rule stays told.
- Update, Nov 11 2026 (#556): the last scene may be talking or B-roll, from the first
  plan onwards. The last-scene-talking instruction and checks are removed; the ending
  may be a product shot. #550 keeps the other first-plan defaults subject to owner steering.

**What we turned down.** Silent B-roll shots cut over her continuous talking voice (inserts),
3 to 5 of them at 1.5 to 4 s, as all three of Creatify's agents recommended in their playbook.
Julian approved them at 05:27 and reversed it at 05:48: Creatify's own ads had 3 or 4 clips, two
of the three had no such shots, and the garlic ad had only 2 B-roll clips; the playbook is not
to be taken as given. Every scene stays one whole clip with its own sound, as 0012 decided.

**What would change our mind.** Graded ads where the extra B-roll scenes fail more often than the
talking scenes they replaced, or where plans keep breaking these rules after being sent back.

**What it changes in the app.** `PLAN_INSTRUCTIONS` and the plan validator (`jobs/planning.py`),
`jobs/plan_edits.py`, the scene agent's `change_scene`, its scene facts (`media.py`), its
instructions and the starting picture request (`draw_starting_picture.py`).
