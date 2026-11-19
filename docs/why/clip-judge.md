# Getting the clip judge to agree with me

After each clip is filmed, an AI judge watches it. If it fails the clip, the scene agent fixes it. The hard part was deciding what the judge should fail.

## Three paths

- **Strict:** fail every real flaw, even the ones a viewer won't notice in a short, fast clip.
- **Lenient:** pass most clips, and fail only the few clear faults it can catch reliably, even if some flaws ship.
- **The middle:** fail only what a viewer would notice. This is what I really want, but it's mostly about taste.

The two ends can be built. The middle is the hard one, and right now I still can't align the judge with me there. Every shortcut I tried toward it failed: asking the AI "would a viewer notice", showing it fewer frames, a limit on how long a fault lasts, and trimming the clip instead of filming it again.

## What I chose

- **In the app, lenient.** The judge only checks what it can catch reliably: did the product's shape or colour change, did an extra piece appear, and did something pass through a body or a solid object. It's not too strict, so it doesn't over fail. It might wrongly fail sometimes, but mostly it doesn't.
- **The rest, the user fixes with a note.** AI video still breaks sometimes, so the app is built around fixing a scene by writing a note in the chat. In my test, a note fixed 6 of 11 bad scenes.
- **What it costs.** On Nov 17, 11 of 116 clips in the final ads had a fault the judge didn't catch. 7 of them were hands or bodies breaking physics.
- **For testing my own changes, strict.** Why that didn't pay off either: [evals](evals.md).

Code: `backend/jobs/clip_judge.py`.
