# A producer and a director per scene, instead of one agent

Replaced by 0005: one agent first, and directors only if measured.

One agent with a render tool could make the ad: scenes already rendered in parallel as fixed
background steps. We split it anyway. The producer holds the conversation and the whole script and
sends a director per scene; a director sees only its own scene and reports back. The reason is
context: the producer sees each director's short report rather than every picture prompt and retry,
so its context stays small over a long session, and each scene's decisions are made with only that
scene in view. The payoff is small in v1, where a director only picks a photo, writes the picture
prompt and works out what a note means for its scene. It grows once the critic exists and a scene can
go through several rounds of check and redo, so we are building the frame the critic plugs into. This
follows Creatify's producer, director and critic design, but only where these reasons hold.

## Consequences

- A director can't change its line. Lines belong to the whole script (the story, the length fit, the
  price), which a director can't see. It proposes; the producer decides and the planning checks run again.
- Instructions reach a director only when it starts. A note about a running scene stops that director
  and starts a new one carrying the note and the old director's record from the checkpoints, so its
  choices and reasons are kept and finished work isn't paid for twice.
- Only the producer talks to the user.
- More model calls, and more parts to stop, restart and fake in tests.

## Considered Options

- **One agent with a render tool.** The design before this. Cheaper and simpler, but every scene's
  retries and critic verdicts would pile into one context.
- **Passing a note to a running director.** Keeps its unwritten thinking and a half-finished step, but
  notes could arrive at any moment and would have to be saved and replayed on restart, and editing a
  finished ad would still need a new director: two mechanisms instead of one. Rejected.
