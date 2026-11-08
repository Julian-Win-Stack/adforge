# One agent per B-roll scene, with eyes, that draws, checks and films it

Extends [0010](0010-one-agent-after-the-first-clip.md): the producer stays the one main agent.
For B-roll only, it brings back a per-scene agent like
[0004](0004-a-producer-and-a-director-per-scene.md)'s director, for a different reason than 0004
gave. Talking scenes are unchanged. Design record: `handoffs/start-frame-design/spec.md` in the
project files; issues #298 and #299.

Extended by [0012](0012-every-scene-made-with-h3.md), approved Nov 3 2026: the same scene agent
now owns talking scenes too, with H3 making the voice. That switch is live; this page records
the earlier B-roll-only path.

**What was wrong.** A B-roll clip's first prompt was written by a model that saw no pictures:
code labels for each sent photo plus one sentence. The page photos went straight to the video
model as up to 9 references, with no starting picture. Only a retry, by the fixing agent, was
written by an agent that sees pictures, so the first try was the least informed. And an owner's
note was rewritten by another call; if the judge failed that clip, the fixing agent that took
over never saw the note.

**What we do now.**

- One scene agent per B-roll scene, all started together once the shared person-and-room
  picture exists. Built from the fixing agent. Its chat lasts the ad's whole life.
- It picks the photos it needs, draws the starting picture, gets a separate two-question picture
  check, writes the video prompt with the picture in view, films on H3 from that picture, and
  fixes after a failed clip. Caps per run: 6 pictures, 6 clips. Changed by #469: 6 starting-picture tries
  (draws and edits), 6 end-picture tries separately, and 6 clips.
- A scene with a page video is filmed in H3's reference mode: the starting picture as Image 1,
  plus the video.
- The owner's words reach a scene agent only through the producer: its instruction plus quotes
  of the owner's messages that code has checked, at the start and on every note, even while the
  agent is busy.

**Evidence.** Prototype round 1 (Nov 3 2026): 5 scenes from 5 products, every first starting
picture passed the picture check, and Julian graded all 15 takes PASS, for $7.66.

## Considered Options

- **The producer writes both prompts, with eyes** (one agent, as 0005). Rejected for the harness
  cost of giving one agent the right pictures at the right moment (Julian, Nov 2 19:31); 0010
  already kept pictures out of the producer's context by making looking a sub-call.
- **Copy Creatify's live agent, one agent for everything.** Julian, Nov 2 18:54: build our own
  design unless it fails badly, and know where Creatify's is stronger.
- **Send the picture model every library photo.** Rejected after round 1 sent gallery graphics
  (Julian, Nov 3 02:37). **Pick photos by code from the library labels.** Rejected: the labels
  miss contents and can't tell flavours apart (Nov 3 04:46).
- **How the owner's words reach the agent:** the whole chat (rejected, Nov 3 01:28); a producer
  brief alone (01:32, "so fragile"); the owner's messages picked by code (not taken, 02:02);
  whole messages pointed at by number (05:16: a long message carries changes for many scenes).
  Taken: quotes checked by code (Julian's idea, 05:19).
- **Page-video scenes on a separate path** (today's references, no starting picture). Rejected
  for the two-path code (Julian, Nov 3 02:24); his reference-mode idea keeps one path (04:52).

## Consequences

- Gone for B-roll: the first-send prompt writer, the note-rewrite path, the reference-picture
  tiers, and the camera rule (decided Nov 1).
- New: a B-roll picture check, and B-roll cards that show the starting picture, which goes stale
  when the line, action, place or photos change.
- 0004 rejected passing a note to a running director. This reverses that: notes reach a busy
  scene agent.
- More model calls per ad (one agent per B-roll scene), with each run's spend capped.
- Known weak spots: a wrong photo pick isn't caught, because the check sees the same photos; a
  non-look wish the producer forgets to pass (for example "no faces") has no backstop.
- **Scenes drift apart (added Nov 8 2026, round 12).** Each scene agent sees the plan but none
  of the other scenes' pictures or changes, so as agents fix and change their actions, scenes can
  stop matching: round 12's Gorilla Glue drew a 2 oz bottle in scene 1 and the 8 oz in the rest,
  and its wood was a big board, then two small pieces in a clamp, then a medium board. Julian:
  "this is our limitation to creatify's current agent which is the single agent. since it is the
  single agent it can make each scene consistent ... making each scenes cohesive is best effort."
  What we do about it (PR "Keep scenes matching each other"): the plan fixes the product photos
  (one version, one size) and the real size, which every scene agent is told and every starting
  picture must pick from; the planner words an object shared by several scenes the same in each;
  and a scene agent's changed action is checked against the other scenes (3 refusals per run,
  then recorded only). Still open: Julian's other idea, the producer reviewing the whole ad at
  the end and fixing what doesn't match, which could reach Creatify's level but is slow.
