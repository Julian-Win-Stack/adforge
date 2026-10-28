# Lessons from round 5

Round 5 made 15 real ads for products the app had never seen. Julian graded them on Oct 18, 2026, and 7 ads failed. Each line below gives what went wrong, then what we learned.

1. **A one-moment action stretched over a 5 to 7 second clip** (dropping a Blueland tablet, scooping Native Pet Pumpkin). The clip lasts as long as the spoken line, so the video model invents extra action to fill the time. Lesson: pick one action that fills the clip on its own, or show its natural result, or have the line said to camera. Never add a second action.
2. **Retries that only add "don't" rules** ("do not refill, do not pour"). Video models follow "do" orders, not bans, and all four such remakes failed. Lesson (being tested): a fix rewrites the whole prompt to say what happens; it never just appends what must not happen.
3. **Scenes the plan should never have filmed**: storage shown as use (an Olipop can sliding off a fridge shelf), care inside a running machine (a Rothy's shoe in a washer), a result too small to see on a body (Cocofloss removing plaque). Lesson: the planner needs a rule for each.
4. **Actions the video model does not know** (flossing). It makes up the motion and the string. Lesson: show the product the way its page shows it, or have the line said to camera.
5. **Our own prompts give orders that contradict each other** (an ear seen from behind yet visible from the front; "copy the photo exactly" plus "the pump is removed"). A general rule against this (Oct 8) and a judge that reads prompts (Oct 10) both failed to stop it. Lesson: the owner's eye catches it, and the fix redraws the picture from scratch.
6. **The picture check passes what it was never told about.** It judges each object alone and only asks "is this physically impossible?", so it failed 0 of 40 pictures. A known limit, not a bug.
7. **The clip judge fails good clips by vote.** Two answers about different faults were counted as agreeing. Decided: a small AI call checks that two answers name the same fault. (Dropped Oct 22: one ask matched Julian as often as the vote, so the judge now asks once; `docs/adr/0008-clip-judge-asks-once.md`.)
8. **The clip judge ignores clothes and bodies**, because its own instructions tell it to (a shirt changed colour mid-clip). To be widened.
9. **In reference mode the clip does not start from our drawn picture** (19 of 37 clips). Decided: not a problem.
10. **The last scene gets cut short** because B-roll clips run longer than their lines. Decided: skip for now.
11. **No B-roll when the product page gives too little** (Glow Recipe), and the owner is not told. Being looked into separately.
12. **The fix paths cannot change what a scene does.** The retry and the decider keep the action and the picture and only reword the prompt. Lesson: a fixing agent should own the whole scene.
