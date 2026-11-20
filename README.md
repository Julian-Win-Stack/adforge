# AdForge

A chat agent that turns a product page into a short video ad. You send a product link and it makes the whole ad: a presenter talks about the product, and product shots show it being used. If a scene comes out wrong, you write a note in the chat and the agent fixes it.

- **The ads, a month ago and now:** _link coming_
- **Loom walkthrough:** [_link ](https://www.loom.com/share/e71fc00b292e4eda901238046fbb9be5)
- 

![Three frames from a finished ad: the presenter with the can, the pour over ice, the first sip](docs/readme/ad.png)

## Words I use

- **Video model:** the AI that films each clip. It's Boreal-H3, Creatify's model (H3 for short).
- **Product shot:** a clip of the product being used, with the presenter's voice over it.
- **Main agent:** the one agent the user chats with.
- **Scene agent:** one agent per scene. It draws the clip's starting picture, films the clip and fixes it.
- **Starting and end pictures:** pictures drawn before filming. The clip starts on the starting picture and ends exactly on the end picture.
- **Clip judge:** an AI that watches each clip and passes or fails it.

## How it works

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/readme/how-it-works-dark.png">
  <img alt="The user talks to the main agent. The main agent reads the page and plans, makes the presenter, music and pictures, changes scenes, looks at pictures and clips, and starts one scene agent per scene. Each scene agent draws the starting picture, films the clip and fixes it. Its pictures and clips go through checks, a picture check and the clip judge: a fail goes back to the scene agent, a pass goes to assembly, which joins clips, captions, overlays, music and the end card." src="docs/readme/how-it-works-light.png">
</picture>

<!-- The diagram is a picture so its labels show whole on every screen. Source: docs/readme/how-it-works.mmd; render it with Mermaid in light and dark and replace both PNGs. -->

Why the main parts are like this:

| Part | Why it's like this |
|---|---|
| Main agent | [Three calls that shaped the agents](docs/why/architecture.md): what forced each one, and what it cost |
| Read and plan | [Reading the page](docs/why/read-the-page.md): an ad once said another product's facts |
| Scene agent | [Working around the video model](docs/why/video-model.md): starting pictures, end pictures, one movement per clip |
| Checks | [Getting the clip judge to agree with me](docs/why/clip-judge.md): what the judge should fail |

Every part, with its code: [docs/how-it-works.md](docs/how-it-works.md).

## The hard parts

### 1. Working around the video model

AI video still breaks physics a lot. That's normal right now, so most of my work went into finding what it does wrong and building around it.

- **The biggest one:** a drawn and checked starting picture for every clip. When I sent the real product photos instead, the video model copied everything in them: backgrounds, clothes, other people. Only half the product shots worked. With a drawn and checked picture, 26 of 27 did.
- **The rest:** end pictures, one movement per clip, contents never changing on screen, and the whole product in the starting picture: [docs/why/video-model.md](docs/why/video-model.md). All 21 quirks I found: [docs/h3-limits.md](docs/h3-limits.md).


### 2. Getting the clip judge to agree with me

- **Three ways to set it:** strict, fail every flaw. Lenient, fail only what it can catch reliably. Or the middle, fail only what a viewer would notice.
- The middle is what I really want. But judging video is mostly taste, and I still can't get the judge to agree with me there.
- **What I chose:** lenient in the app, and the user fixes the rest by writing a note. That works most of the time ([numbers](#numbers)). More: [docs/why/clip-judge.md](docs/why/clip-judge.md).


### 3. Why an AI grader for my test ads didn't pay off

- From the start, I kept building an AI that grades every test ad after I change the app, so I'd only have to watch the ones it flags.
- In my last test it caught only 3 of the 11 broken clips I found. Most of the ones it missed were hands or bodies breaking physics.
- With 15 to 30 test ads at a time, my own eyes plus my saved grades are faster and better. So for me, it didn't pay off. More: [docs/why/evals.md](docs/why/evals.md).


### 4. Letting the agent decide, instead of coding every case

- By late October the app was really a pipeline with about 180 checks in code. About 15 of them were dead ends: when one fired, the ad got stuck for good, and that's where most failed ads died. The user couldn't steer either. Asked twice to show a dog's paws being cleaned, it refused twice.
- **What I chose:** let the agent decide, instead of coding every case a user might ask for. One main agent with general tools. Every check now tells it why instead of stopping it, and only 3 hard refusals stay in code.
- **What it cost:** 18,601 lines I'd built in the weeks before, and some predictability. More: [docs/why/architecture.md](docs/why/architecture.md#2-letting-the-agent-decide-instead-of-coding-every-case).


### 5. One agent per scene, not one agent for everything

- The first prompt for every product shot was written by a model that never saw a picture, just a label and one sentence. But one agent seeing every scene's pictures would flood its memory.
- **What I chose:** one agent per scene. It picks its own photos, draws the starting picture, films the clip and fixes it, and the main agent only gets words back.
- **What it costs:** scenes can drift, like a 2 oz bottle in one scene and the 8 oz in the next. A single agent like Creatify's keeps scenes consistent. I lock the product, its size and shared props in the plan. More: [docs/why/architecture.md](docs/why/architecture.md#3-one-agent-per-scene-instead-of-one-agent-for-everything).

## Numbers

Built in 2 months: 1,748 commits, and 15 batches of test ads I graded myself.

My last test, Nov 17: 30 ads, 15 on products I'd tested before and 15 on products the app had never seen.

- **Whole ads:** 21 of 30 worked. One bad clip fails the whole ad.
- **Clips:** 105 of 116 clips in the finished ads worked.
- **Fixing with a note:** one note fixed 16 of 22 bad scenes ([tests](docs/test-rounds/scene-tests.md#owner-note-eval-nov-17-ads)).

AI video still breaks sometimes, so the app is built around fixing it with a note.

"Worked" means I watched it and nothing broke: no wrong claim, no impossible motion, no made-up product part. It doesn't mean the ad would sell. I didn't grade that. Every test batch, with every failure and its fix: [docs/test-rounds/README.md](docs/test-rounds/README.md).

## What it still can't do

- **End pictures.** They mostly work, but sometimes they make faults. The video model always tries to end exactly on the end picture, so if the end picture is wrong, or doesn't match what the prompt asks, the clip goes wrong. And even when the agent asks for the right thing, the picture model sometimes draws the wrong thing. About 3 to 6 of 24 failed clips across 4 runs trace back to them. I can definitely fix this, but I chose not to right now, because most of the time they do more good than harm.
- **Logos and labels.** Wrong letters are fine to me. But sometimes a logo comes and goes, in a different colour and place from the real one (a Stanley cup ad).
- **Still shots.** We still haven't figured out how to do still shots properly. With nothing to move, the video model moves something by itself, like a drawer or a sprayer.

## What I'd work on next

- **Make the ads pull.** Make them sell, not just work. This needs domain knowledge.
- **New products, new failures.** Test the agent on new products, find the new ways it fails and fix them. This is quite manual.
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
- `GEMINI_API_KEY`: watches page videos and videos the user sends. It isn't in `.env.example`; add it yourself.
- `FIRECRAWL_API_KEY` (page reading), `ELEVENLABS_API_KEY` (transcripts), `FAL_KEY` (music, picture hosting), `CREATIFY_API_ID` and `CREATIFY_API_KEY` (H3 clips).
- `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` (traces), `SENTRY_DSN` (optional, failure alerts).

Tests run on a scripted fake model and need no keys:

```sh
cd backend && uv run pytest -q -n auto --dist worksteal -m "not slow"   # fast run, ~30 s
cd backend && uv run pytest -q -n auto --dist worksteal                  # everything, as CI
cd frontend && npm test
```
