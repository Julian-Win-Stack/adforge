# Getting the clip judge to agree with me

After each clip is filmed, an AI judge watches it, and if it fails the clip the scene agent fixes it and films it again. The hard part was deciding what the judge should fail.

## Three paths

- **Strict:** fail every real flaw, even ones a viewer won't notice in a short fast clip.
- **Lenient:** pass most clips and only fail the few clear faults it can catch reliably, even if some flaws ship.
- **The middle:** fail only what a viewer would notice. This is what I actually want, but it's mostly about taste.

The two ends can be built. The middle is the hard one and right now I still can't align the judge with me there. I tried a bunch of shortcuts toward it and all of them failed: asking the AI "would a viewer notice", showing it fewer frames, a limit on how long a fault lasts, and trimming the clip instead of filming it again.

## In the app I went lenient

In the app every wrong fail costs a re-film, money and time. So the judge only checks what it can catch reliably: did the product's shape or colour change, did an extra piece show up, did something pass through a body or a solid object. It's not too strict so it doesn't over fail. It can wrongly fail sometimes but mostly it doesn't.

The rest, the user fixes with a note. AI video still breaks sometimes, so the app is built around fixing a scene by writing a note in the chat. In my two tests, one note fixed 16 of 22 bad scenes.

The cost: on Nov 17, 11 of 116 clips in the final ads had a fault the judge didn't catch. 7 of them were hands or bodies breaking physics.

For testing my own changes I went the other way, strict, because there a wrong fail doesn't cost a re-film. Why that didn't pay off either: [the test judge](evals.md).

Code: `backend/jobs/clip_judge.py`.
