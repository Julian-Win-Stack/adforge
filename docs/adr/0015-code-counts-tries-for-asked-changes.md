# Code counts tries for asked changes

Approved by Julian on Nov 24, 2026. [#743](https://github.com/Julian-Win-Stack/adforge/issues/743)

Before the change, the producer still tried a fourth time in 33 of 60 sessions for a request it
couldn't deliver. In the prototype it tried a fourth time in 0 of 20. The scene clips were faked
and the producer's calls were real. [Prototype results in #743](https://github.com/Julian-Win-Stack/adforge/issues/743#further-notes)

The producer reads the owner's note and decides which requested result it belongs to. Code derives
the count from the recorded runs for each asked change and scene. It gives the producer the try
history before a repeat films and stops new runs for that change once the scene uses its tries.
The producer tells the owner how it went and offers alternatives when needed.
[`file_note` and `counted`](../../backend/agents/asked_changes.py)

A separate model to judge sameness would read the request again and add a paid call to a decision
the producer is already making. Matching words in code would miss rephrasings and could join
different results that use the same words. Revisit how the producer files notes if the repeated-ask
eval finds it regularly splits repeats or combines distinct changes.
