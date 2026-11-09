# Fix loop: fixing failures Julian graded

Use this when ads break or fail in ways Julian graded: the wrong product, a made-up claim, broken physics, a check that sends back too much. When the goal is a new kind of quality the ads don't have yet, use the [improve loop](improve-loop.md) instead.

At every step, plan it with Julian and tell him where we are: which step, and what it is for. He is learning this loop.

**Rules and checks follow failures** (Julian, Nov 9 2026):

- No new rule, ban or check without a failure Julian graded behind it. A failure only imagined, or a model limit he hasn't seen fail, gets nothing.
- Fix a graded failure in the instructions first (the planner's or the agent's prompt).
- Build a check that sends work back only when the same failure comes back after that instruction fix.
- A check that never fired on real ads gets deleted; its instruction sentence stays, so the ads don't change.

1. **Look.** Julian watches the ads and writes down every failure in his own words. Done when his grades are saved (`runs/round-<N>/julian-grades/` in the project files).
2. **Group.** Lay out his notes; he groups them into kinds of failure and names them. Pick the kinds that matter most. Done when he approved the named list.
3. **Obvious gap?** Fix it right away only when the fix can be confirmed by reading the code or one output (a typo, a missing field). A fix that can only be judged by watching videos needs step 4 first.
4. **Evals on what still fails.** After the obvious fixes, one yes/no question per kind of failure that is left, with 20-50 real cases he labeled. Test the failure, not the rule (`backend/evals/README.md`). Run them on today's agent: they should mostly fail, and that is the before score. Include some of his passes too, so a grader that fails everything gets caught. An AI grader counts only once it agrees with his labels: tune it on the `tune` half, score it on the `score` half.
5. **Change.** One change at a time (a rule, a prompt, a check), so each score change has one cause.
6. **Raise the pass rate** of the new evals without breaking the old ones. Keep a change only when both hold.
7. **Lock.** Cases that now pass become `regression` cases. Repeat from step 1 with the next round.

Sources: Hamel Husain and Shreya Shankar, [AI Evals FAQ](https://hamel.dev/blog/posts/evals-faq/) (error analysis first; "write evaluators for errors you discover, not errors you imagine"; build evaluators for failures you will keep iterating on). Anthropic, [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) ("20-50 simple tasks drawn from real failures is a great start"; capability and regression evals).
