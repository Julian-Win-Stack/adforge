# B-roll build 1: one real ad

- Date: 2026-10-05
- Commit: `82a003e` (branch `89-firecrawl-page-read`), with every build 1 ticket (#97 to #107)
  in. Run locally in a cloud session: Django, one Celery worker and beat, Postgres, Redis.
- Why: to check that a whole ad runs end to end on the new B-roll (Boreal-H3 on Creatify),
  not to judge the clips' quality (#107; `docs/broll-picture-logic.md` item 33). Quality
  checks are off: none is built. `CREATIFY_PROMPT_ENHANCEMENT` was `auto`.
- Product: #8 of [docs/test-products.md](../test-products.md), Molly's Suds Toilet Bowl
  Cleaner. First message: `Make a 15-second ad for https://mollyssuds.com/products/toilet-bowl-cleaner`.
- Session `8511fce1-5352-4182-ad30-3e8537a12d8b`, named `B1 08 · Cleaning · Molly's Suds Toilet
  Bowl Cleaner`. Cost: sum of `gateway_modelcall.cost_usd` for the session; "without produce"
  leaves out `purpose='produce'`.

## Summary

| # | Name | Session | Result | Cost | Cost without produce |
|---|------|---------|--------|------|----------------------|
| 8 | B1 08 · Cleaning · Molly's Suds Toilet Bowl Cleaner | `8511fce1` | Finished ad (16.2 s), 3 scenes, 1 B-roll (way 1), after a photo question and a length question | $2.008 | $1.596 |

51 model calls, all succeeded. No notice, no warning on the job, nothing paid for twice. From
the first message to the finished ad: 7 min 13 s (22:30:49 to 22:38:02 UTC).

## Cost per service

| Service | Cost | Note |
|---------|------|------|
| OpenAI | $0.917 | of which the producer's turns $0.412; 3 starting pictures $0.172 |
| HeyGen | $0.623 | 2 talking clips |
| Creatify | $0.396 | 1 B-roll clip, 5 s at $0.0792/s |
| fal | $0.058 | music |
| Inworld | $0.013 | |
| ElevenLabs | $0.001 | |

The second run's ad of the same product cost $1.381; the B-roll clip is now $0.396 instead of
the old Boreal's $0.058.

## Questions the producer asked, and the answers given

- "Could you attach a photo showing the gel, or should I make the ad without showing the
  cleaner being squeezed under the toilet rim?" Answered "Use without proven result" (#107's
  notes). The planner's ask (item 20) worked: no photo shows the gel. The producer then
  planned around it ("without depicting an unverified result or the gel itself") instead of
  proposing how it would show the scene, so there was nothing to describe and no gel colour
  was given.
- "The script currently runs about 17 seconds. Would you like me to shorten it to fit 15
  seconds, or keep the slightly longer version?" Answered "Keep the longer version". (17 s is
  the most a 15-second target allows.)
- The price ($9.99 one-time) was not asked: the planner picked it.

## The scenes

| Scene | Kind | Line | Clip |
|-------|------|------|------|
| 1 | Talking | "Meet Molly's Suds Toilet Bowl Cleaner, made with 98% plant-based ingredients." | HeyGen, 5.86 s |
| 2 | B-roll, showcase, no face, way 1 (no needs) | "Its extended-reach neck provides 360-degree coverage under the rim." Shows: "A close-up circles the bottle and its extended-reach neck." | Boreal-H3, 5 s asked, 5.21 s made |
| 3 | Talking | "It fights stains and odors without ammonia or chlorine bleach—just $9.99." | HeyGen, 6.6 s |

Scene 2's video prompt: "In one continuous shot, make a slow, smooth 360-degree camera orbit
around the stationary bottle while keeping the bottle and extended-reach neck centered and
sharply focused; keep the cap sealed, preserve the product and label exactly, and show no gel
or cleaning action. Complete the orbit by easing into a flattering front three-quarter close-up
where the label and neck catch the natural bathroom light. Upright 9:16 with subtle handheld
phone realism."

No scene was made way 3: the planner named no needs once the gel was left out. Way 1 alone is
fine for this run (#107's notes); way 3 is covered by the faked end-to-end test
(`backend/tests/test_end_to_end.py`) and waits for the graded run.

## The early cut, as assembled

| Scene | Picture plays | Voice plays |
|-------|---------------|-------------|
| 1 | 0.00 to 5.34 | 0.00 to 5.34 |
| 2 | 5.34 to 10.55 (the whole 5.21 s clip) | 5.34 to 10.02 (4.68 s line) |
| 3 | 10.55 to 16.20, its first 0.55 s skipped | 10.02 to 16.20 |

Scene 3's line starts over the last 0.53 s of the B-roll clip, and its picture skips that much
so the lips match. The voice has no gap.

## Files

- The finished ad and scene 2's B-roll clip are kept with the project, not in the repo:
  `runs/broll-build1-one-ad/toilet-cleaner-ad.mp4` and `scene-2-broll-clip.mp4`.
- The B-roll clip is for the user to watch and grade; no judge exists in build 1 (#109).

## What this run found

- Everything ran end to end on the new B-roll with no failure or retry.
- The planner's ask fires when no photo shows what a scene needs (the gel), and the producer handled "use
  without proven result" by dropping the gel from the plan rather than proposing a version.
  Whether that is what item 42 meant ("the producer says how it would show the scene and the
  shop owner approves or changes it") is a question for the user.
