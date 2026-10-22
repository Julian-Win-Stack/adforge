# The clip judge asks each check once

The clip judge used to ask SIDES and MOTION 3 times each and fail a check on 2 failing answers;
since round 5 a small text call (the same-fault vote, PR #186) also had to agree that two failing
MOTION answers named the same fault. On round 6's stored answers ($0, no new calls) one ask alone
matched Julian's grades as often as the vote did:

| Judge | Clips | Matches Julian | Wrong fails | Missed fails |
|---|---|---|---|---|
| 3 asks + vote | 43 | 29 | 9 | 5 |
| any single ask (average) | 42.3 | 28.0 | 9.0 | 5.3 |

On 35 of 43 clips every single ask gave the vote's verdict, and 10 of the vote's 14 errors were made
by all 3 asks alike. The judge's errors are its bar (what counts as a fault), not noise a vote evens
out, so the vote paid 3 times for the same answer. Julian, 2026-10-22 21:09: "drop the vote"
(`runs/gemini-judge-test/one-ask-vs-vote.md`).

Now each check is asked once (`clip_judge.ASKS = 1`) and fails when its answer fails it. The
same-fault call is gone. A clip fix's check, which is the clip judge's MOTION ask plus one question
about the owner's change, is settled by that one answer.

## Consequences

- The judge costs a third of what it did: 2 asks per clip instead of 6, plus no same-fault call.
- An ask the safety filter blocks or that can't be read leaves its check unchecked (no usable
  answer); the clip is kept and the owner is told, as before.
- Rulings made while each check was asked 3 times stay on old clips and are still read the way
  they were made: an answer the vote outvoted is never handed to the fixing agent or shown.
- The start-picture check (`picture_check`, 3 asks failing on 2) and a picture fix's check are
  unchanged: no evidence was measured for them.
