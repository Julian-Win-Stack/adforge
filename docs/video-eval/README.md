# Video eval: do prompt changes fix the talking and B-roll clips, or do we switch model?

Started 2026-09-29. Ticket: Julian-Win-Stack/adforge#87. Problems 3
(talking clips: slow motion, lips out of step, over-expressive faces) and 4 (B-roll breaks
physics) from the first run, `docs/runs/first-run-trace-findings.md` and the user's watch notes.

**The one question this answers:** is a prompt change enough, or do talking clips (and maybe
B-roll) need a different video model?

Everything below was fixed **before any clip was watched**. Changing it after grading starts
would make the result worthless.

## The story, in short

**The problem.** After the first run of 24 test ads, the talking clips were bad in 13 of 21: the
person moved in slow motion, the lips didn't match the words, and the face was over-expressive,
"cringy". The B-roll broke physics: a chair floated instead of rising, the whole chair spun
wheels and all, a sunscreen tube bent. We didn't know whether the prompts were wrong or the
video model (Boreal) couldn't do it.

**Why an eval.** Watching a few clips and deciding by feel is how the first guesses went wrong.
So we built the smallest fair test that could settle one question: prompt or model?

**How.** Ten scenes from the first run, always with the same starting picture and audio, so only
the video changed. Pass rules written before any clip was watched, yes or no per rule, no
scores. Every arm run twice, because Boreal has no seed and every clip is a fresh draw. Arms:
today's prompts (baseline), tuned B-roll prompts written by the planner under new rules, two
opposite talking prompts (lively and calm) plus a negative prompt, and HeyGen for the talking
scenes. 56 clips, about $4.70 in total. The user graded blind: random names, the key kept in a
separate file, the agent never graded.

**What it found.** Talking: Boreal failed lips and speed in every prompt variant (2 and 3 of 8);
HeyGen passed 8 of 8. So it's the model, not the prompt: talking clips move to HeyGen. B-roll:
the tuned rule ("one continuous movement, the product stays rigid") passed the mark (10 or 11
of 12), fixing the floating and the bending, but only by asking for less: a swivel still spins
the whole chair, and a lever scene passes because nothing moves. Scenes where a part of the
product must move are still beyond Boreal.

**What the grades are for next.** The 44 graded clips, with the user's marks and notes, are the
first answer key for the critic: the model call that will grade clips automatically inside each
job. Before it's trusted it must agree with these human grades on most of them. They're kept in
`graded.json` (grades joined to arm, scene and run), the two grading sheets, `key.json` and
`runs.jsonl` here, and the clips themselves outside git in `backend/media/video-eval/`
(415 MB; back them up, the Docker volume isn't a safe home).

## Test set

Ten scenes from the first run. Each reuses its existing starting picture and audio, so only the
video is paid for. Defined in `backend/video-eval/set.json`.

| Id | Job | Scene | Kind | Why it's in |
|---|---|---|---|---|
| B1 | chair `eb52304c` | 3 | B-roll | whole chair spun, wheels too (broken: wrong part moves) |
| B2 | chair `eb52304c` | 4 | B-roll | chair floated instead of the seat rising (broken: floating) |
| B3 | sunscreen `98473621` | 5 | B-roll | tube pushed down and changed shape (broken: deformation) |
| G1 | treadmill `b2e75d04` | 2 | B-roll | guard: the walk worked |
| G2 | mat `f507ca42` | 2 | B-roll | guard: the stretch worked |
| G3 | bag `8293cced` | 2 | B-roll | guard: the camera glide over the clasp worked |
| T1 | cleaner `6f334378` | 1 | talking | slow motion and lips |
| T2 | sunscreen `98473621` | 6 | talking | unrealistic expression |
| T3 | treadmill `b2e75d04` | 5 | talking | "cringy" last scene |
| T4 | mat `f507ca42` | 3 | talking | ends on a shocked face |

No talking guard: every talking clip the user watched was flagged.

## Pass rules

Each clip gets pass or fail on each rule. A clip passes only if every rule passes. No scores.

- **Talking:** lips move in step with the words · the person moves at normal speed · the face
  stays calm and fits the line, including the last second · no extra hands or objects.
- **B-roll (graded silent):** the product keeps its size and shape throughout · no extra hands,
  limbs or objects · the motion could happen in real life: only parts that really move move,
  nothing floats.

Out of scope, per the user: zoom-ins and camera drift, clip length versus audio length, several
actions in one planned `shows`.

## Pass mark (decision rule)

- **B-roll:** keep Boreal with the tuned prompt if at least 4 of the 6 tuned clips of B1–B3 pass
  and every tuned clip of G1–G3 passes. Otherwise, benchmark other models (Kling 3.0 Pro, Veo
  3.1 Lite on fal) on the same set.
- **Talking:** keep Boreal (with whichever variant and resolution did it) if lips and speed pass
  on at least 6 of the 8 clips of T1–T4 in that arm. Otherwise HeyGen if it reaches that mark;
  otherwise Aurora 2 (Creatify's API, subscription) is tested, on the user's say-so.

## Arms, in the order they run (each paid round needs the user's go-ahead)

Boreal has no seed, so every clip is a fresh draw. Each arm runs the set **twice** to see the
model's own variance. If the guards fail at random in the baseline, runs go to three.

| Arm | What changes | Cost, about |
|---|---|---|
| text check | tuned B-roll instructions re-run on 20 saved planner inputs; read the motion prompts, no video | $0.30 |
| smoke | one clip with a negative prompt, to prove the script and the field work | $0.06 |
| `baseline` | today's prompts, 720p | $1.10 |
| `tuned` | B-roll: motion prompts from the tuned instructions + negative prompt. Talking: variant A (lively) and B (calm) + negative prompt + "speech starts at once" | $1.50 |
| `1080p` | the better talking variant at 1080p ($0.03/s) | $1.20 |
| `heygen` | the 4 talking scenes on HeyGen Avatar IV, expressiveness low (~$0.035/s) | $1.40 |
| `aurora2` | only if all of the above fail on lips; needs a Creatify API plan ($99/month) | ~30 credits |

The old first-run clips count as a third baseline sample for B-roll (same request). Not for
talking: those used an older motion prompt.

The prompt variants are in `backend/video-eval/tuned_prompts.py`; the arms in
`backend/video-eval/arms.json`.

## How it runs

Scripts in `backend/video-eval/` (only `backend/` is mounted in the container). Clips and
grading files go to `backend/media/video-eval/`, which git ignores.

```
docker compose exec -T backend python video-eval/tuned_broll_prompts.py      # text check
docker compose exec -T backend python video-eval/heygen_clips.py --arm heygen --runs 2
docker compose exec -T backend python video-eval/blind.py --arms baseline,tuned,1080p,heygen
```

`make_clips.py`, which made the Boreal-on-fal arms, was removed with the old Boreal when
Boreal-H3 replaced it for B-roll (#97); it is in git history.

Every clip is recorded in `backend/media/video-eval/runs.jsonl`: seconds asked, seconds got,
frames, time to make, cost. Clips are made straight through the providers, never through a job.

## Blind grading

`blind.py` copies every clip to one folder under a random name, next to its starting picture
under the same name, with `grading.csv` (one row per clip, one column per rule) and an
`index.html` to watch them in. `KEY.json` maps names back to arm, scene and run: **the user
doesn't open it until grading is done.** The user grades; the agent doesn't grade or pre-fill.
HeyGen clips will look different from Boreal's, so that arm is only partly blind.

## Results (graded 2026-09-29)

Graded blind by the user; failures marked, blanks read as passes (9 blank rows, all in the tuned, tuned_B and HeyGen arms). Baseline was only partly graded: the user had already watched those failures in the first run. Talking clips were graded on the old starting pictures, so notes about a tiny mat or treadmill are problem 2 (fixed since in `d5084b0`), not counted.

| Arm | Scene | Run | Rules | Clip | Got s | Made in s | Cost $ | Note |
|---|---|---|---|---|---|---|---|---|
| baseline | B2 | 1 | shape:P hands:P motion:F | fail | 6.375 | 70.8 | 0.065 |  |
| baseline | G1 | 1 | shape:P hands:P motion:P | pass | 5.708 | 292.9 | 0.0583 |  |
| baseline | G2 | 2 | shape:P hands:P motion:P | pass | 5.042 | 257.1 | 0.0517 |  |
| baseline | G3 | 2 | shape:P hands:P motion:P | pass | 6.042 | 347.9 | 0.0617 |  |
| baseline | T1 | 2 | lips:F speed:F face:P hands:P | fail | 5.375 | 345.7 | 0.0542 |  |
| baseline | T2 | 1 | lips:F speed:P face:F hands:P | fail | 3.708 | 344.6 | 0.0356 |  |
| baseline | T3 | 2 | lips:P speed:P face:P hands:P | pass | 5.375 | 151.0 | 0.0542 |  |
| baseline | T4 | 2 | lips:P speed:P face:P hands:P | pass | 7.042 | 265.6 | 0.0688 | I mean the mat, one laying down. I think it is huge, but I haven't seen the laying down pi |
| tuned | B1 | 1 | shape:P hands:P motion:P | pass | 6.708 | 22.2 | 0.0683 | The whole shit, including the wheel, is spinning. I don't think it should be like that  |
| tuned | B1 | 2 | shape:P hands:P motion:F | fail | 6.708 | 33.0 | 0.0683 | The whole shit, including the wheel, is spinning. I don't think it should be like that  |
| tuned | B2 | 1 | shape:P hands:P motion:P | pass | 6.375 | 54.6 | 0.065 | This is all okay, but one that handle is lifted, the share should the share should become  |
| tuned | B2 | 2 | shape:P hands:P motion:P | pass | 6.375 | 270.3 | 0.065 | The handle is lifted, but the shade is not. The shade height is not going up  |
| tuned | B3 | 1 | shape:P hands:P motion:P | pass | 5.708 | 280.0 | 0.0583 | blank |
| tuned | B3 | 2 | shape:P hands:P motion:P | pass | 5.708 | 279.5 | 0.0583 | blank |
| tuned | G1 | 1 | shape:P hands:P motion:P | pass | 5.708 | 299.7 | 0.0583 |  |
| tuned | G1 | 2 | shape:P hands:P motion:P | pass | 5.708 | 351.7 | 0.0583 | blank |
| tuned | G2 | 1 | shape:P hands:P motion:P | pass | 5.042 | 31.3 | 0.0517 | blank |
| tuned | G2 | 2 | shape:P hands:P motion:P | pass | 5.042 | 194.7 | 0.0517 | blank |
| tuned | G3 | 1 | shape:P hands:P motion:P | pass | 6.042 | 159.9 | 0.0617 | I think we are like the camera angle is getting too close to the purse, and it becomes so  |
| tuned | G3 | 2 | shape:P hands:P motion:P | pass | 6.042 | 348.6 | 0.0617 | I think we are like the camera angle is getting too close to the purse, and it becomes so  |
| tuned_A | T1 | 1 | lips:F speed:F face:F hands:P | fail | 5.375 | 94.4 | 0.0542 |  |
| tuned_A | T1 | 2 | lips:F speed:F face:F hands:P | fail | 5.375 | 93.4 | 0.0542 | The person is not speaking all the words  |
| tuned_A | T2 | 1 | lips:F speed:P face:F hands:P | fail | 3.708 | 151.8 | 0.0356 |  |
| tuned_A | T2 | 2 | lips:F speed:P face:F hands:P | fail | 3.708 | 258.1 | 0.0356 |  |
| tuned_A | T3 | 1 | lips:P speed:P face:P hands:P | pass | 5.375 | 250.7 | 0.0542 | The walking machine becomes so small that the guy is holding it in his hands  |
| tuned_A | T3 | 2 | lips:P speed:P face:P hands:P | pass | 5.375 | 286.6 | 0.0542 | The walking machine becomes so small that the guy is holding it in his hands  |
| tuned_A | T4 | 1 | lips:F speed:F face:F hands:P | fail | 7.042 | 295.4 | 0.0688 | I think the mat is so small  |
| tuned_A | T4 | 2 | lips:F speed:F face:F hands:P | fail | 7.042 | 139.8 | 0.0688 | I think the mat is so small that the guy is holding everything in his hands  |
| tuned_B | T1 | 1 | lips:P speed:P face:P hands:P | pass | 5.375 | 83.2 | 0.0542 | blank |
| tuned_B | T1 | 2 | lips:F speed:F face:F hands:P | fail | 5.375 | 104.4 | 0.0542 | The person is not even speaking the words in the picture. Only the voice is coming. The th |
| tuned_B | T2 | 1 | lips:F speed:F face:F hands:P | fail | 3.708 | 194.9 | 0.0356 |  |
| tuned_B | T2 | 2 | lips:F speed:P face:F hands:P | fail | 3.708 | 263.1 | 0.0356 | The person is not speaking other words  |
| tuned_B | T3 | 1 | lips:P speed:P face:P hands:P | pass | 5.375 | 218.8 | 0.0542 | The walking machine becomes so small that the guy is holding it in his hands  |
| tuned_B | T3 | 2 | lips:P speed:P face:P hands:P | pass | 5.375 | 131.2 | 0.0542 | The walking machine becomes so small that the guy is holding it in his hands  |
| tuned_B | T4 | 1 | lips:F speed:P face:P hands:P | fail | 7.042 | 289.6 | 0.0688 | The president is not speaking all the words  |
| tuned_B | T4 | 2 | lips:F speed:P face:P hands:P | fail | 7.042 | 225.9 | 0.0688 | I mean, the guy is not even speaking at all  |
| heygen | T1 | 1 | lips:P speed:P face:P hands:P | pass | 5.44 | 94.0 | 0.1904 | blank |
| heygen | T1 | 2 | lips:P speed:P face:P hands:P | pass | 5.44 | 69.9 | 0.1904 | blank |
| heygen | T2 | 1 | lips:P speed:P face:P hands:P | pass | 3.56 | 80.5 | 0.1246 | blank |
| heygen | T2 | 2 | lips:P speed:P face:P hands:P | pass | 3.56 | 50.3 | 0.1246 |  |
| heygen | T3 | 1 | lips:P speed:P face:P hands:P | pass | 5.44 | 86.6 | 0.1904 | The walking machine becomes so small that the guy is holding it in his hands  |
| heygen | T3 | 2 | lips:P speed:P face:P hands:P | pass | 5.44 | 105.0 | 0.1904 | The walking machine becomes so small that the guy is holding it in his hands  |
| heygen | T4 | 1 | lips:P speed:P face:P hands:P | pass | 6.88 | 49.4 | 0.2408 |  |
| heygen | T4 | 2 | lips:P speed:P face:P hands:P | pass | 6.88 | 105.3 | 0.2408 | I mean the mat, one laying down. I think it is huge, but I haven't seen the laying down pi |

### Against the pass mark

- **B-roll, tuned Boreal:** broken scenes 5/6 pass (needed 4), guards 6/6 (needed 6). **Meets the mark.** But B1 (chair swivel) spun the whole chair including the wheels in both runs by the user's note, and B2 (lever) passes only because nothing happens: the hand holds the lever and the seat never moves.
- **Talking, tuned_A:** lips and speed pass on 2/8 (needed 6); full pass 2/8. Fails.
- **Talking, tuned_B:** lips and speed pass on 3/8 (needed 6); full pass 3/8. Fails.
- **Talking, heygen:** lips and speed pass on 8/8 (needed 6); full pass 8/8. **Meets the mark.**

Video cost of the rounds: $4.22 (HeyGen at the $0.035/s estimate) plus $0.49 for the planner text check.

### Decision

- **B-roll stays on Boreal** with the tuned motion-prompt rules and negative prompt. Open point: a swivel or any motion of one product part is still beyond Boreal; the rule turns such scenes into camera moves that show nothing happening.
- **Talking clips move to HeyGen Avatar IV.** Boreal failed lips in every variant, lively or calm, so it isn't the prompt. HeyGen passed every rule on all 8 clips.
- Not run: Boreal 1080p (no point: the calm variant, the best, passed lips on 3 of 8) and Aurora 2.
