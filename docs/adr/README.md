# Decisions

The big decisions in AdForge's design, in order. Each one has a page with what we saw, what we
decided, what we turned down and why. When a later decision replaces an earlier one, both stay,
so you can follow how the design changed.

| # | Since | Decision | Replaced by |
|---|---|---|---|
| [0001](0001-agent-loop-with-safety-in-the-tools.md) | Oct 19 or earlier | An agent loop instead of a fixed pipeline, with the safety rules inside the tools | |
| [0002](0002-facts-come-only-from-the-page-and-the-user.md) | Oct 19 or earlier | Facts come only from the product page and the shop owner | |
| [0003](0003-checkpoints-not-conversation-replay.md) | Oct 19 or earlier | Resume from checkpoints, not by replaying the conversation | |
| [0004](0004-a-producer-and-a-director-per-scene.md) | Oct 19 or earlier | A producer and a director per scene, following Creatify's published producer, director and critic design | 0005 |
| [0005](0005-one-agent-first.md) | Oct 19 or earlier | One agent first; directors only if measured | (extended by 0010) |
| [0006](0006-owner-pasted-page-text-is-the-page.md) | Oct 20 | Text the shop owner pastes counts as the page | |
| [0007](0007-videos-read-with-the-planners-own-prompt.md) | Oct 20 | Page videos are read by a separate call with the planner's own prompt | 0009 |
| [0008](0008-clip-judge-asks-once.md) | Oct 22 | The clip judge asks each check once | |
| [0009](0009-videos-watched-whole-by-the-watcher.md) | Oct 29 | Page videos are watched whole, not read as thinned frames | |
| [0010](0010-one-agent-after-the-first-clip.md) | Oct 29 | One agent after the first clip too; looking at pictures is a sub-call that answers in words. It replaced the Oct 16 decider, which had no page of its own | |

All dates are 2026. "Since" is when the page first appears in this repository. The repository's
history starts on Oct 19, so a decision made before then shows "Oct 19 or earlier".

## Adding one

Write a decision page only when the decision is hard to undo, would puzzle a later reader, and came
from a real choice between options. Give it the next number and add its line here. On the page,
say what we saw, what we decided, what we turned down and why, what would change our mind, and what
it changes in the app. When it replaces an earlier page, say so at the top of both.
