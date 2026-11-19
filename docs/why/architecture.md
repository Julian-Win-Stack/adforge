# How the architecture got here

Every change here fixed a failure I graded. I didn't change things because it felt better. Two of the steps are removed now, but you need them to see why today's setup looks like this.

- **Main agent:** the one agent the user chats with. The code calls it the producer.
- **Scene agent:** one agent per scene of the ad. It makes and fixes that scene.

## 1. Decider (Oct 16). Removed now.

- **Before:** when the user asked for a change, the main agent fixed it from the words alone.
- **Went wrong:** "scrub left to right" got done, but the brush still scrubbed the toilet seat ([note test](../test-rounds/scene-tests.md#fix-from-a-note-test)).
- **Why:** fixing the right thing needs eyes on the clip, but pictures would flood the main agent's memory.
- **Helps:** a second agent looks at the clip first, then proposes the fix.

## 2. Fixing agent (Oct 19). Removed now.

- **Before:** the clip judge (an AI that watches each clip) failed bad clips, and the app filmed them again with "don't do X" added.
- **Went wrong:** the same mistake came back, on 4 scenes in round 5. It even kept a clip I failed over one I passed ([round 5](../test-rounds/round-05.md)).
- **Why:** the video model ignores "don't". The real fix was often a different action or picture, which needs something that sees the clip.
- **Helps:** an agent looks at the failed clip and changes the action or picture. It fixed 7 of 18 failed scenes in round 6 ([round 6](../test-rounds/round-06.md)).

## 3. Today: one main agent, plus one agent per scene

Two changes, for two different failures.

### One main agent for the whole chat (Oct 29)

- **Before:** after the first clip, the main agent was locked out: the decider picked a fix plan and code ran it. The app had about 180 checks in code that could stop a step.
- **Went wrong:** most round 6 ads that died, died at one of about 15 checks with no way out, like a step stuck "running" after a crash, blocking its scene forever. And the user couldn't steer in words: when I asked to show the dog's paws being cleaned instead of the presenter, it refused twice ([round 6](../test-rounds/round-06.md)).
- **Why:** rules in code can't cover everything a user asks. An agent with general tools can.
- **Helps:** one agent decides and acts on anything, so the decider went. A rule is added only after a failure I graded. A side call looks at pictures for it and answers in words, so its memory stays small ([ADR 10](../adr/0010-one-agent-after-the-first-clip.md)).

### One agent per scene (Nov 3)

- **Before:** an AI that never saw the pictures picked each clip's photos and wrote its first prompt.
- **Went wrong:** round 7, only 4 of 15 ads worked, mostly from bad pictures sent to the video model ([round 7](../test-rounds/round-07.md)).
- **Why:** whoever picks a clip's pictures has to see them. One agent seeing every scene's pictures would flood its memory, so the seeing is split by scene.
- **Helps:** each scene's agent picks photos, draws a checked starting picture, films and fixes. It took over the fixing agent's job. Round 8: 26 of 27 product shots worked ([round 8](../test-rounds/round-08.md), [ADR 11](../adr/0011-one-agent-per-broll-scene.md)).
