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
  fixes after a failed clip. Caps per run: 6 pictures, 6 clips.
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
