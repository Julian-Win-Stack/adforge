# Improve loop: adding a new quality to the ads

Use this when the goal is something the ads don't do yet: a new kind of quality, like more B-roll with a purpose or a better talking/B-roll ratio. When ads fail in ways Julian graded, use the [fix loop](fix-loop.md) instead.

At every step, plan it with Julian and tell him where we are: which step, and what it is for. He is learning this loop.

1. **Goal.** Write each goal as what a viewer sees in the ad, in Julian's words. Research (Creatify, ad guides) can come first, to learn what good looks like. Done when each goal is one sentence he approved.
2. **Checks, before building anything.** Each goal gets a check:
   - A countable goal (share of ad time that is B-roll, words per second, number of B-roll scenes) gets a code check. It is free and needs no labels.
   - A taste goal ("is the B-roll good?") gets one yes/no question, with one pass and one fail example in his words. He labels 20-50 cases, yes/no only. When today's ads have no "yes" case, take "yes" examples from reference ads, such as Creatify's.
   - An AI grader for a taste question counts only once it agrees with his labels: tune it on the `tune` half, score it on the `score` half (`backend/evals/README.md`).

   Done when every goal has a check and its cases are in `backend/evals/cases/`.
3. **Before score.** Run the checks on today's agent and write the numbers down. A low score is expected: that is the gap the improvement closes.
4. **Build.** Pick actions from the research, one change at a time, so each score change has one cause.
5. **After score.** Make ads on the same products, then run the same checks plus the regression checks (what already works). Keep the change only when its goal's score goes up and the regression checks still pass.
6. **Watch.** Julian watches the new ads. When the score went up but the ads don't look better to him, the check is wrong: fix the check and go back to step 3.
7. **Lock.** Once the goal passes, its cases become `regression` cases that must keep passing.

Sources: Anthropic, [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) ("eval-driven development: build evals to define planned capabilities before agents can fulfill them"; a new capability eval "should start at a low pass rate"). Hamel Husain and Shreya Shankar, [AI Evals FAQ](https://hamel.dev/blog/posts/evals-faq/) (evals before building only "where you know exactly what success looks like"; yes/no labels, not 1-5 scores). Step 6 is our own guard against a goal that was written wrong.
