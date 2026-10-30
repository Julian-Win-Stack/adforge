# One agent after the first clip; looking is a sub-call that reports

Extends [0005](0005-one-agent-first.md) past the first clip. Supersedes the decider of Oct 16
(`decisions/fix-redesign.md`), which had no ADR of its own. Spec #247.

Since Oct 16 the producer made nothing once the ad had a clip: a second agent, the decider,
read the owner's notes, looked at pictures and clip frames, and proposed a plan with a cost and
a yes button that code then ran. It was built because the old producer decided fixes blind and
because its context would otherwise fill with pictures. Round 6 and the Oct 21 guard audit
showed the cost: a plan runner and a routing layer with about fifteen dead ends, a yes button
nobody wanted, and every owner wish needing its own scenario in code.

We now keep one agent for the whole session with general tools on the ad's parts, and we keep
the producer's context small another way: looking is a sub-call on the producer's own model
(`agents/look.py`, purpose `look`) that is shown the pictures, frames and sent list and answers
in words (root cause and direction), never deciding. The fixing agent stays a tool the producer
calls for an owner note about what a clip shows (`fix_scene`); a judge fail reaches it by code.
No yes button before paid changes: the agent acts and says the cost; a pause button is the
owner's brake.

## Considered Options

- **Keep the decider and fold the dead ends out.** Rejected: the decider cannot act, so every
  new owner wish still needs a plan row type and a runner case; and two decision makers lose
  the conversation between them.
- **One agent that sees frames itself.** Rejected for context: a session with many looks would
  fill the producer's window with pictures (Julian, Oct 29 03:12-03:15). The sub-call returns
  words; the producer asks again when it must see.
- **A cheaper model for the look.** Rejected: "cheap" was about the producer's context, not
  money; the look runs on the same model so its reading is as good as the producer's
  (Julian 03:15).

## Consequences

- Routing, the plan runner, fix plans, the plan card and both yes buttons are deleted. The
  owner's only controls are the chat box, the pause button, the version taps on scene cards and
  the owner-part card's yes.
- Only three refusals live in code (`agents/hard_list.py`: a new product mid-ad, a clip of an
  invented part without the owner's yes, the video API's limits). Every other refusal is a tool
  answer with a reason. A new guard is added only after a graded failure.
- The producer's conversation folds its oldest tool calls once it passes half the model's
  window; the ad's state is always present as a snapshot. ADR 0003's checkpoints are unchanged.
