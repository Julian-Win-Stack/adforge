# Design mentor: scene agents (already taught)

The scene-agent design had its own mentor run before this whole-repo pass:
[PR #324](https://github.com/Julian-Win-Stack/adforge/pull/324) and
[ADR 0011](../adr/0011-one-agent-per-broll-scene.md).

**Code it covered:** `backend/agents/scene_agent/`, `backend/agents/scene_tools.py`,
`backend/agents/scene_forms.py`, `backend/agents/owner_quotes.py`, `backend/jobs/scene_pictures.py`.

## The 10 concepts it taught

1. Stopping conditions in the agent loop (turn and spend caps per run and per ad)
2. Agent evals on a real model (outcomes graded, recorded clips replayed)
3. Calibrating an LLM judge against Julian's grades
4. A stable prompt prefix, so prompt caching works
5. Independent evidence for checks (the maker doesn't pick what the checker compares against)
6. Precision vs. recall when handing over owner notes
7. An explicit state machine
8. Leases instead of "one worker, always"
9. An app-side rate limiter (token bucket)
10. Atomic counters with F()

The area reports in this folder list each of these again under **Also seen here** when the same
concept shows up in their area. `summary.md` counts those to mark a concept a **habit**.
