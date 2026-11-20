# Why an AI eval judge doesn't pay off for me

The idea was an AI judge that grades every ad after I change the app, so I only have to look at what it flags. I kept working on it on the side since I started building. I went with a strict judge: it flags every flaw and I overrule the small ones. In the end the answer was no, for my case. Why:

1. **It misses the failures that matter.** On the Nov 17 run it caught 3 of my 11 failed clips. 6 of the 8 it missed were hands or bodies breaking physics, and it already had questions for exactly that. Even when told where to look, it describes the hand and still calls it natural.
2. **A judge I can't trust saves me no time.** At 3 of 11, I still have to watch every clip. And most of its flags were on clips I passed, so I spend time overruling it too.
3. **My volume is too small to need it.** A run is 15 to 30 ads, about 15 minutes of watching. My eyes are faster and better. A judge pays off when there are more ads than people can watch.
4. **Too few failures to teach it.** Each run gives only 5 to 10 failed clips, and new runs bring faults it wasn't tuned on. Questions written from old failures caught them on those ads, then missed most on fresh ones.

So right now, my eyes plus my saved grades are the eval. The place an AI check actually matters is inside the app, when nobody is watching: [the clip judge](clip-judge.md).

How I test a change now: a regression run replays 30 saved product pages and puts the old and new ads side by side, and I grade them (`backend/evals/regression/`).
