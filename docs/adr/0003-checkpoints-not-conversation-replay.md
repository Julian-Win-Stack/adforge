# Resume from checkpoints, not by replaying the conversation

Under the old fixed pipeline, a crash was survivable because `job.status` named the next step. With a
model choosing the order, there is no written-down next step. Every finished tool call is therefore
recorded as a checkpoint - which tool, its arguments, what it produced, what it cost - and a restart
hands the model the conversation and the checkpoints together.

The alternative was to replay the stored conversation alone and let the model work out what is left
from its own narration. Rejected because the model is not deterministic: reading the same history twice
it may re-plan, re-render a scene it is unsure about, or write a scene that does not match the plan the
earlier ones were built from. Checkpoints mean a tool asked to redo finished work hands back what
exists rather than producing it again, so resuming does not depend on the model remembering.

This extends machinery that already exists: `_paid_for_before` in `jobs/tasks.py` already looks up a
succeeded `ModelCall` to avoid paying twice for the portrait or the voice. A checkpoint is that idea
applied to every tool.

A checkpoint is written only once its tool has run, and an agent's turn only once its model call
has returned, so a worker can stop after paying and before writing. The gateway covers that gap:
an agent's turn, and a page check, handed exactly what a succeeded `ModelCall` was handed, is
answered from that record rather than paid for again.
