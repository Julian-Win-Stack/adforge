# One agent first, and directors only if measured

This replaces 0004. The producer makes every scene itself, with scene tools that start the work in the
background and return at once, so scenes still render at the same time and each step's result reaches
the producer when it finishes. There are no directors. 0004 split the work for context: the producer
would see a director's short report instead of every picture prompt and retry. That argument holds only
once the critic sends scenes through several rounds of check and redo, and until then a scene is four
steps, which one agent handles easily. Creatify's report uses the split but never tests it against one
agent, and in the Graza run two of its failures happened in the handoff between producer and director:
a scene length sent as a decimal, and the voice IDs left out so the director wrongly said the person
was not registered. One agent has no handoff to get wrong.

So the split is now a measured question. Every model call records how many tokens it sent, so each run
shows how the producer's context grows. Once the critic exists, a few ads are run; if the context gets
too big, or later scenes fail checks more often than earlier ones, directors are built and the two are
compared on cost, time and errors. The thresholds are set after that run.

## Consequences

- The scene tools belong to the producer and take a scene number. The rules 0004 put on sending a
  director move onto these tools: they refuse a line whose fact check hasn't passed, and refuse a step
  already running for that scene.
- A note about a scene in flight stops that scene's running step, and the producer redoes what the note
  affects. No record is passed on, because the producer never lost the context.
- Each scene step is kept separate so the critic can say which one to redo, and so the tools stay the
  same if directors are built: only who calls them changes.
- The producer's context grows with every scene. That is the cost being watched, not ignored.

## Considered Options

- **Keep 0004's director per scene.** Rejected until measured: more model calls, a handoff that can
  lose information, and more to stop, restart and fake in tests, for a benefit that doesn't exist before
  the critic.
- **One "make scene" tool that runs picture, audio, transcript and clip in code.** Less for the producer
  to track, but it brings back a fixed pipeline inside each scene, and the critic couldn't ask for one
  step to be redone.
- **Scene tools that wait for the work.** Simpler, but the producer would sit for minutes while clips
  render and the user's messages would wait with it.
