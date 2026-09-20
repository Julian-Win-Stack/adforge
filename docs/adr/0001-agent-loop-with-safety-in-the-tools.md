# Agent loop instead of a fixed pipeline, with the safety moved into the tools

The system was built as a fixed state machine: `job.status` named the next step, each Celery task
checked it and skipped forward. That gave crash resume almost for free, but it can only offer the
edits someone thought to build, and the product needs the user to steer mid-run ("change the person",
"make scene 2 shorter", "try a different hook") the way they steer Claude Code. So a model now holds
the conversation and chooses which tool to call next, and no fixed order exists in code.

The cost is that the model can skip a step, repeat one, or loop. The answer is not longer instructions.
Every rule that matters is enforced by the tool that would break it: the render tool refuses a line
whose fact check has not passed, a tool asked to redo finished work hands back what exists instead of
paying again, and a session has a cap on tool calls and a scene a cap on renders. An instruction is a
suggestion; a tool that refuses is a rule. This is the same reasoning as the existing lint rule that
bans importing the model client outside the gateway.

## Considered Options

- **Fixed pipeline with a chat front end.** An LLM parses the opening message and narrates, but "make
  the ad" is one sealed tool. Cheapest, keeps the existing tests, but every edit the user might ask for
  has to be predicted and coded in advance. Rejected: mid-run steering is the point of the rewrite.
- **Agent at the edges, pipeline for the spine.** The model owns intake and interruption; the middle
  stays deterministic. Rejected once we decided the agent should judge whether to kill work in flight,
  which it cannot do if it cannot see inside the run.
