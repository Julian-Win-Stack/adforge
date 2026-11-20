# Why an AI judge for testing doesn't pay off for me

Since I started building, I kept working on the side on an AI judge that grades every test ad after I change the app, so I'd only watch what it flags. I couldn't get it to judge like me. Judging video is mostly taste, and the middle path, failing only what a viewer would notice, is really hard right now ([the three paths](clip-judge.md#three-paths)).

## For testing I went strict

Here I went the other way from the app. The judge fails every flaw it can find, even ones I'd ship. That's fine for testing, because I only compare before and after a change. A strict judge over-fails about the same in both runs, so when a kind of fault goes up or down, that's my change. In the app a wrong fail costs a re-film. Here it doesn't matter.

## Why it still didn't pay off, for my case

1. **It misses what matters, even when it's taught.** On the Nov 17 run it caught 3 of my 11 failed clips. It had questions for hands and bodies breaking physics, and still missed 6 of the 7 I failed. Even when told where to look, it describes the hand and still calls it natural. And a before and after compare only sees what the judge sees, so it would miss those getting worse too.
2. **It's always a step behind.** I'm still running a small set of products and still finding new kinds of faults on new ones. Each run gives only 5 to 10 failed clips. Questions written from my old faults caught them on those ads, then missed most on fresh ones. My eyes catch a new kind the first time I see it.
3. **A judge I can't trust saves me no time.** At 3 of 11, I still have to watch every clip anyway.
4. **My volume is too small to need it.** A run is 15 to 30 ads, about 15 minutes of watching. My eyes are faster and better.

## When it becomes useful

When new products stop bringing new kinds of faults, real users make more ads than people can watch, and it catches about 9 in 10 failed clips on fresh ads. Until then my eyes plus my saved grades are the eval. The place an AI check matters now is inside the app, when nobody is watching: [the clip judge](clip-judge.md).

How I test a change now: a regression run replays 30 saved product pages and puts the old and new ads side by side, and I grade them (`backend/evals/regression/`).
