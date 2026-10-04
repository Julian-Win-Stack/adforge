# What Creatify publishes about making a good ad, and what AdForge can use

- Date: 2026-10-03
- Read: four of Creatify's public skills (video-ad-generator, static-ad-concept-generator,
  ai-ad-prompt-guide, ad-creative-evaluator), their agent report and their benchmark's
  README. Links are at the end.
- We don't use the skills. We read them to learn what Creatify thinks a good ad is.
- Nothing here is built or decided. The ideas are for later tickets.

**How much to trust it.** The skills are practitioner advice: they give no numbers and no
studies. The platform studies in [what-makes-a-good-ad.md](what-makes-a-good-ad.md) are
stronger evidence. Use this page as checklists, not as proof. Some of it is also dated: the
model advice names older models (Kling 2.1, Sora 2, Veo 3.1).

## Words used on this page

| Term | What it means |
|---|---|
| **Hook** | The first 1 to 3 seconds, which decide whether the viewer keeps watching. |
| **Angle** | The reason a viewer should care, such as "it solves this problem" or "it costs less". |
| **Body** | The part of the ad between the hook and the ending. |
| **CTA** (call to action) | The part that tells the viewer what to do, such as "Shop now". |
| **Social proof** | Other people vouching for the product: reviews, star ratings, customer counts. |
| **A/B test** | Running two versions of an ad that differ in one thing, to see which does better. |
| **Hallucination** | Something the model made up: an extra finger, a garbled label, an object that wasn't asked for. |
| **Rubric** | A fixed list of things to judge, each with a score. |

## 1. Planning the ad

From video-ad-generator and static-ad-concept-generator.

### Think before writing: the creative brief

Before any script, they fill in a short brief:

- Who the ad is for, and their main pain point.
- The one core benefit, in one sentence, with at most three supporting ones.
- The proof: reviews, numbers, awards.
- What stops people buying (their objections).
- The platform, the length, the tone and the CTA.

**AdForge today:** the planner goes straight from the page text to a script. Nothing makes
it decide who the ad is for or what its one message is.

### The hook: five kinds

| Kind | What it does | Example shape |
|---|---|---|
| Pattern interrupt | Something unexpected that breaks the scroll | Starting in the middle of an action |
| Question | Opens a question the viewer wants answered | "Why are people switching to …?" |
| Bold claim | Leads with the strongest benefit | "This replaced my …" |
| POV | Puts the viewer inside a familiar moment | "POV: you finally found …" |
| Stat or authority | A number or an expert, for instant trust | "After testing 10 of these, this one won" |

Their evaluator also says: the hook should land in the first 1 to 2 seconds, and a brand
logo in the first 3 seconds hurts.

**AdForge today:** the first scene must be the person talking to camera. Nothing says what
kind of opening line it should be.

### The body: five structures

| Structure | The steps | Suits |
|---|---|---|
| Problem, agitate, solve | Name the problem, make it feel urgent, bring in the product | Products that fix a pain |
| Feature cascade | The best feature, two supporting ones, then proof | Gadgets, feature-rich products |
| Social proof stack | A review, visual proof, how many customers, then urgency | Shops with many reviews |
| Before and after | The problem, the product in action, the result | Cleaning, home improvement |
| Day in the life | The product in a daily routine, the key moment, the feeling | Lifestyle products |

Each step is short: 2 to 5 seconds.

**AdForge today:** the planner writes freely. It isn't asked to pick a structure.

### The ending: six kinds of CTA

Direct ("Shop now"), curiosity, urgency, risk reversal ("Try it free for 30 days"), social
("Join X customers") and benefit ("Start … today"). One CTA only: several confuse the viewer.

**AdForge today:** no end card (#13).

### Sixteen angles

The same product can be sold from many angles: the problem it solves, the result, social
proof, comparison with the old way, urgency, an expert, price, curiosity, fear of missing
out, lifestyle, how-to, a story, the season, risk reversal, an ingredient or feature, and a
customer's review. They say problem, social proof and result work for almost everything.

### Testing versions

- Test the **hook first**: 3 to 5 hooks with the same body and CTA. Then the CTA, the
  length, the presenter, the music.
- Change **one thing** per test.
- The first number to look at is the hook rate: how many viewers watched 3 seconds.

**AdForge today:** variants are planned (#36). This says what a variant should change first.

### Where this breaks our rules

Much of this advice needs facts that the page may not give. Under ADR 0002, AdForge can
only use them when the page or the shop owner states them:

- Customer counts, star ratings and review quotes.
- "Only 3 left", "ends Friday" and other urgency.
- "Dermatologists say", "doctor recommended".
- "Replaced my $200 …" and other comparisons.
- Before and after. Our planner refuses before/after of bodies and skin on every ad.
  **To verify:** our docs say the platforms restrict or ban this. Third-party blogs say Meta
  loosened its rule in July 2026 for cosmetic products shown to adults, and that TikTok
  still prohibits it in paid ads. Neither was checked against the platforms' own pages.

## 2. Prompting pictures and clips

From ai-ad-prompt-guide. The parts about B-roll are also used in
[broll-picture-logic.md](broll-picture-logic.md).

### Four parts in every prompt

Subject (what is in the shot), lighting and look, camera (angle, movement, framing), and
technical (shape, length, style).

**AdForge today:** the B-roll instructions ask for the subject and the setting. They don't
ask for the camera or the lighting.

### Five rules against made-up details

1. **Say exactly where things are:** "the bottle on the left, the plant on the right".
2. **At most 3 main things** in a scene. More get merged or bent.
3. **Describe what is there, not what isn't:** "an empty cafe with wooden chairs", not "no
   people".
4. **Use real-world references** for the look, not vague words like "beautiful lighting".
5. **Give counts:** "two cups", "a single person".

### What models get wrong, and their way round it

| What goes wrong | Their fix |
|---|---|
| A person holding a product: bent hands and fingers | Start from a real photo, or keep hands out of the shot |
| Text on a label: garbled | Don't ask the model to write text; add it afterwards |
| A brand logo: distorted | Add it afterwards as a layer |
| A complex physical action: physics breaks | Split it into simpler shots |
| Several people: faces merge | One person per scene |

### B-roll tips

- Say "slow" for every camera move. Models move too fast by default.
- Make the still picture first, then animate it. AdForge already does this.
- Show hands only, without faces, where possible.
- Always state the length.
- Name camera moves with film words: push-in, pull-back, pan, orbit, static, handheld.

### The three-pass check on anything a model made

1. **Physics:** gravity, reflections, shadows, sizes.
2. **Details:** hands and fingers, text, faces, clean edges between objects.
3. **Brand:** does the look fit, and can a viewer tell it is AI-made?

If it fails, make it again with a changed prompt. Don't try to repair it.

### Counting the cost

Count the cost per **usable** clip, not per clip. A model at 8 credits that works half the
time costs 16 per usable clip. To compare models, run each prompt 3 times, because results
vary.

## 3. Checking the ad: the critic

From ad-creative-evaluator, the agent report and the benchmark's README.

### Two levels of checking

**Each scene, while the ad is being made** (agent report). Their critic judges every clip
and either approves it or sends it back with a reason. It looks at:

- product fidelity: is it still the real product?
- invented objects
- text quality
- physics
- anatomy
- overall quality

They score made-up details from 0 to 10, lower is better, and treat 3 as the line an ad
must stay under. Every defect found in a shipped ad becomes a test case for the next ones.

**The whole ad** (evaluator skill). Eight scores from 0 to 10:

| What is scored | Weight |
|---|---|
| Hook: do the first 3 seconds stop the scroll? | 1.5 |
| Message: can the viewer say what the product does, in one sentence? | 1.3 |
| CTA: does the viewer know what to do, and why now? | 1.3 |
| Audience: does a specific viewer feel "this is for me"? | 1.2 |
| Look: right for the platform? (lo-fi is fine on TikTok) | 1.0 |
| Pacing: does every second earn the next? | 1.0 |
| Emotion: does the viewer feel something? | 1.0 |
| Sound off: does it work with no audio? | 0.7 |

Three made-up people each give scores: a performance marketer (will it sell?), a creative
director (is it well made?) and a target customer (would I stop, watch, tap, trust?). Where
they disagree is treated as the most useful finding.

It can also judge a **script before anything is rendered**, which is the cheapest moment to
fix an ad. It looks at a finished video through 8 frames spread evenly, including the first
and the last.

### From their benchmark

- Plain code checks, with no model: length, shape, sound, and on-screen text read back.
- A judge that is shown two ads and picks the better one, using two different models and
  swapping the order, so that neither a model nor the position decides the result.
- Made-up details count three times as much as structure.

### How AdForge could use this

- The per-scene list matches what went wrong in the second run: the spoon that refilled
  itself, the purse that came off by itself, the button pressed three times instead of
  twice, the shirt that was too small.
- Scores from 0 to 10 by made-up people are vague. A critic is easier to act on when each
  check is a yes or no question with a reason, taken from failures we have really seen.
- Their list of what to check is the useful part, more than their scoring.

## Ideas for AdForge

Not decided, in the order they seem worth doing.

1. **Scene critic:** yes/no checks on each clip, from the per-scene list and the second
   run's grades.
2. **Script check before paying:** is there a hook, one clear message, and an offer at the
   end? It only reads text, so it is cheap.
3. **The planner picks an angle, a hook kind and a body structure,** and says why.
4. **The end card,** with one CTA (#13).
5. **A variant changes the hook first** (#36).
6. **B-roll prompts** name the camera and lighting, say "slow", and keep to 3 things.
7. **Record how many clips were usable** per model, to know the real cost.

## Sources

- video-ad-generator: https://github.com/Creatify-AI/video-ad-generator
- static-ad-concept-generator: https://github.com/Creatify-AI/static-ad-concept-generator
- ai-ad-prompt-guide: https://github.com/Creatify-AI/ai-ad-prompt-guide
- ad-creative-evaluator: https://github.com/Creatify-AI/ad-creative-evaluator
- Creatify's agent report: https://creatify.ai/research/agent
- VABench (the benchmark; only a README so far): https://github.com/creatify-ai/VABench
