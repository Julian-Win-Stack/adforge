# AdForge

A chat agent that turns a product page into a short video ad. You just send the product link and it makes the whole ad: a presenter talks about the product, and product shots show it being used. If a scene comes out wrong, you write a note in the chat and the agent fixes it.

- **The ads, a month ago and now:** [julian-win-stack.github.io/adforge-gallery/now.html](https://julian-win-stack.github.io/adforge-gallery/now.html)
- **Loom walkthrough:** [_link ](https://www.loom.com/share/e71fc00b292e4eda901238046fbb9be5)
- 

![Three frames from a finished ad: the presenter with the can, the pour over ice, the first sip](docs/readme/ad.png)

## Words I use

- **Video model:** the AI that films each clip. It's Boreal-H3, Creatify's model (H3 for short).
- **Product shot:** a clip of the product being used, with the presenter's voice over it.
- **Starting pic and end pic:** pictures drawn before filming. The clip starts on the starting pic and ends exactly on the end pic.
- **Clip judge:** an AI that watches each clip and passes or fails it.
- **Eval judge:** an AI that grades my test ads after I change the app, so I'd only watch what it flags.

## How it works

The main agent is the only one you chat with. It reads the page, plans the scenes, then starts one scene agent per scene at the same time, and each one films its clip and fixes it until the clip judge passes it. When every scene is done, the main agent puts the ad together, and your notes go through it to the right scene agent.

If you keep asking for the same change, the main agent sees what it already tried and stops filming that change on a scene after three tries.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/readme/how-it-works-dark.png">
  <img alt="The user talks to the main agent, one for the whole chat. The main agent reads the page and plans, makes the presenter, music and pictures, changes scenes, and looks at pictures and clips. It also starts the scene agents, one per scene, all at once, and each sends back its result: passed or gave up. Each scene agent draws the starting pic, films the clip and fixes it. Its pictures and clips go through checks, a picture check and the clip judge: a fail goes back to the scene agent. Once every scene is done, the main agent assembles the ad: clips, captions, overlays, music and the end card. The two agents are drawn as rounded boxes; their tools are square." src="docs/readme/how-it-works-light.png">
</picture>

<!-- The diagram is a picture so its labels show whole on every screen. Source: docs/readme/how-it-works.mmd; render it with mermaid-cli in light (-t default -b white) and dark (-t dark -b '#0d1117'), at -s 4 with the Inter font installed, and replace both PNGs. -->

Why each part is like this:

| Part | Why |
|---|---|
| Main agent | An agent, not a pipeline. For today's scope, making an ad and fixing it with a note, a pipeline would work too. I picked an agent because later I want the user to steer and customize the ads fully, and in a pipeline every new kind of ask needs its own case coded first. |
| Read and plan | [Reading the page](docs/why/read-the-page.md): an ad once said another product's facts |
| Scene agent | [Working around the video model](docs/why/video-model.md): starting pics, end pics, one movement per clip |
| Checks | [Getting the clip judge to agree with me](docs/why/clip-judge.md): what it should fail |

## The hard parts

### 1. Giving Boreal-H3 the right inputs

Every clip is filmed by Boreal-H3, through its API. One of the hardest things was learning what it needs and where it breaks, then building the agent so it gives it the right inputs and works around the limits. I found 21 quirks so far, each with a workaround.

The biggest one: whatever is in the starting pic leaks into the clip. So the agent draws a starting pic for every clip, and a check looks at it before filming. Product shots went from about half working to 26 of 27.

All of them: [docs/why/video-model.md](docs/why/video-model.md) and [docs/h3-limits.md](docs/h3-limits.md).

### 2. Getting the clip judge to agree with me

After each clip is filmed, an AI judge watches it and fails the broken ones. Judging video is mostly taste. What I really want is a judge that fails only what a viewer would notice, but I still can't get an AI to agree with me there.

So in the app the judge is lenient. Every wrong fail costs a re-film, so it fails only what it can catch reliably, and the user fixes the rest with a note. That works most of the time ([numbers](#numbers)). More: [docs/why/clip-judge.md](docs/why/clip-judge.md).

### 3. Why an eval judge doesn't pay off for me

Since I started building, I kept trying to build an eval judge that grades every test ad after I change the app, so I only watch what it flags. However, making to fail only the flaws that the human can see is really hard. If the judge is too strict, it fails even the small things that the real views wouldn't notice and would accept. But here in this eval judge, I accepted that strictness because I only compare before and after a change, so a judge that fails too much is fine.

However, it still didn't pay off. It misses what matters, even when it's taught and I'm still finding new kinds of faults on new products, so a judge built from my old ones is always a step behind my eyes. For now, my eyes plus my saved grades are the eval. More: [docs/why/evals.md](docs/why/evals.md).

### 4. Each clip needs an agent that can see its pictures

Before, there was one main agent, and it handed specific jobs to separate AI calls. One call wrote each clip’s first prompt for Boreal-H3, but it never saw a picture, only a short label for each photo and one sentence. Only the retries were written by an agent that could see pictures, after the first clip failed. So the first try was always the least informed one.

I could have given the main agent eyes, with context compaction to keep its memory small. But at that time, changing the main agent was more work than giving each scene its own agent, built from the one that already did the retries. So now each scene gets its own agent. It picks its photos, draws the starting pic, films the clip and fixes it. The main agent only gets a short result back.

The catch: scene agents can’t see each other, so scenes can drift, like a 2 oz bottle in one scene and the 8 oz in the next. This mostly happens when a scene agent changes its scene’s action, which I let it do when the clip judge (the AI that watches each clip and fails broken ones) keeps failing the clip. So before it changes the action, a quick check compares it with the other scenes: same product, same size, same shared objects. It can still drift in rare cases, when two scenes change at the same time.

## Numbers

I built it in 2 months: 1,748 commits, and [15 batches of test ads](docs/test-rounds/README.md) I graded myself, with every failure and its fix.

My last test was Nov 17: 30 ads, 15 on products I'd tested before and 15 on products the app had never seen.

- **Whole ads:** 21 of 30 worked. I grade strictly: if one clip has even a tiny physics slip, I count the whole ad as failed.
- **Clips:** 105 of 116 clips in the finished ads worked.
- **Fixing with a note:** one note fixed 16 of 22 bad scenes ([tests](docs/test-rounds/scene-tests.md#owner-note-eval-nov-17-ads)).

AI video still breaks sometimes, so the app is built around fixing it with a note.

"Worked" means I watched it and nothing broke: no wrong claim, no impossible motion, no made-up product part. It doesn't mean the ad would sell, I didn't grade that.

## What it still can't do

- **End pics.** They mostly work, but sometimes they create faults. The video model always tries to end exactly on the end pic, so if the end pic is wrong, or doesn't match what the prompt asks, the clip goes wrong. And even when the agent asks for the right thing, the picture model sometimes draws the wrong thing. About 3 to 6 of 24 failed clips across 4 runs trace back to them. I can definitely fix this, but I chose not to right now, because most of the time they're doing more good than harm.
- **Logos and labels.** Wrong letters are fine to me. But sometimes a logo comes and goes, in a different colour and place from the real one (a Stanley cup ad).
- **Still shots.** We still haven't figured out how to do still shots properly. When there's nothing to move, the video model moves something by itself, like a drawer or a sprayer.

## What I'd work on next

- **Make the ads pull.** Right now they work, but they need to sell too. This needs domain knowledge.
- **New products, new failures.** Test the agent on new products, find the new ways it fails and fix them. This is still quite manual.
- **How far the agents can go.** The app can do more than make an ad and fix it with notes. I didn't test the rest, because making ads and fixing them with notes is the scope right now ([#549](https://github.com/Julian-Win-Stack/adforge/issues/549)).

## How to run

You need Docker.

```sh
cp .env.example .env      # fill in the keys
docker compose up
```

The app opens at http://localhost:5173 (backend on port 8000).

Keys, by env var name (what each is for is in `.env.example`):

- `OPENAI_API_KEY`: pictures, and every model call when Azure isn't set.
- `AZUREE_OPENAI_KEY` and `AZUREE_OPENAI_BASE_URL` (optional): run the text and vision models on Azure.
- `AZURE_OPENAI_SECOND_KEY`, `AZURE_OPENAI_SECOND_BASE_URL` and `AZURE_OPENAI_SECOND_MODELS` (optional): a second Azure account, in another region, for the models it names (such as `gpt-6.2-sol`, which the main account's region can't deploy).
- `GEMINI_API_KEY`: watches page videos and videos the user sends. It isn't in `.env.example`; add it yourself.
- `FIRECRAWL_API_KEY` (page reading), `ELEVENLABS_API_KEY` (transcripts), `FAL_KEY` (music, picture hosting), `CREATIFY_API_ID` and `CREATIFY_API_KEY` (H3 clips).
- `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` (traces), `SENTRY_DSN` (optional, failure alerts).

Tests run on a scripted fake model and need no keys:

```sh
cd backend && uv run pytest -q -n auto --dist worksteal -m "not slow"   # fast run, ~30 s
cd backend && uv run pytest -q -n auto --dist worksteal                  # everything, as CI
cd frontend && npm test
```
