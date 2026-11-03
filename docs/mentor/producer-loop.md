# Design mentor: producer and agent loop

**Design read:** ADRs [0001](../adr/0001-agent-loop-with-safety-in-the-tools.md),
[0005](../adr/0005-one-agent-first.md), [0010](../adr/0010-one-agent-after-the-first-clip.md)
(0004 as history); `docs/decision-map.md`; `docs/plans/77-langfuse-tracing.md`; spec #247; #272
parts 2 and 3; #1's agent-loop sections; the CONTEXT.md entries for this area.

**Code read:** `backend/agents/loop.py`, `producer.py`, `hard_list.py`, `fold.py`, `look.py`,
`snapshot.py`, `pause.py`, `backend/adforge/tracing.py`, and what they call:
`agents/tasks.py` (only the nudge and filter-retry parts), `gateway/gateway.py:take_turn`,
`gateway/openai_adapter.py:take_turn`, `gateway/catalog.py`, `evals/agent_page.py`.

**How it's tested today:** through the chat API, with a scripted fake producer and every model
faked at the gateway (`tests/test_steering_rows.py`, `test_pause.py`, `test_producer_fold.py`,
`test_carries_on.py`, `test_look.py`, `test_traces.py`, `test_producer_messages.py`,
`test_producer_restarts.py`). These tests check the tools, the notes and the records. Nothing
measures what the producer or the look decides.

## Concepts, ranked by impact on this build

### 1. Context rot (set a context budget, not a share of the window): must apply now

**What it is**
- A model reads long inputs worse than short ones, well before its window is full.
- Each token kept in the history is also paid for again on every later turn.
- So the size of the context is a budget, set from measurements.

**Why it matters here**
- `fold.py:fold_point` folds only once a turn passes `FOLD_PAST_SHARE_OF_WINDOW` (0.5) of
  `catalog.max_input_tokens` for gpt-6.1-sol: 0.5 × 922,000 = 461,000 tokens (lower only if
  the Azure limit is set lower).
- At the catalog's $2.00 per million input tokens (`catalog.cost_usd`, which applies no cache
  discount), one producer turn at that size costs about $0.92. One message can use up to 100
  tool calls (`loop.py:settle`).
- In the first run, producer turns were already 35–43% of each ad's cost, mostly wake-ups
  (`docs/runs/first-run-trace-findings.md`, item 14).
- Only tool calls fold. Scene-step results, system notes and every message stay in full
  forever (`loop.py:_as_given`), and a session stays open for editing forever.
- Spec #247's Out of Scope says: "Measuring past sessions to set the fold point (Julian: half
  the window, don't measure)". ADR 0005 planned the opposite: record tokens per call so each
  run shows how the context grows.

**How to apply it**
- Measure first, for free: for each round-6 and round-7 session, plot
  `gateway_modelcall.input_tokens` where `purpose='produce'`, turn by turn.
- Set the fold point as a fixed number of tokens, chosen from that plot and from graded ads,
  not as a share of the window. Then a model with a bigger window can't quietly move it.
- Let old step results and system notes fold to one line too, not only tool calls.
- Test: copy the shape of `test_producer_fold.py`, with a step result and a note past the
  fold point.

**One real post**
- Chroma, "Context Rot: How Increasing Input Tokens Impacts LLM Performance" (18 models:
  "performance grows increasingly unreliable as input length grows"):
  https://www.trychroma.com/research/context-rot

### 2. Passing reasoning items back across tool calls: must apply now

**What it is**
- A reasoning model thinks before it answers. In the Responses API that thinking comes back as
  a "reasoning item".
- When you send the whole history yourself each turn (stateless), you send those items back
  with the tool results. The model then picks up its earlier reasoning instead of starting
  over.

**Why it matters here**
- `gateway/openai_adapter.py:take_turn` reads only each reasoning item's summary text, keeps it
  for traces (`gateway.py:take_turn`), and drops the item itself.
- `openai_adapter.py:_conversation` rebuilds each turn from the producer's words, its function
  calls and their outputs. So every producer turn works out its plan again from nothing: more
  reasoning paid as output ($10.00 per million) and worse continuity.
- OpenAI's function-calling guide says reasoning items "returned in model responses with tool
  calls must also be passed back with tool call outputs."
- The same `take_turn` serves every agent on the loop, so the scene agents are affected too.

**How to apply it**
- Ask for the item: `include=["reasoning.encrypted_content"]` and `store=False` on
  `responses.create`. Azure documents the same thing ("Encrypted reasoning",
  https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/responses).
- Keep each turn's reasoning items, in order, in that turn's `ModelCall.output` next to its
  words and calls. The rebuild from the database (ADR 0003) then still gives the same input
  every time.
- In `_conversation`, put each turn's items back just before that turn's function calls. Drop
  them for folded calls, and when the model changes (they only work with the model that made
  them).
- Test: a fake turn that returns a reasoning item; the next request carries it just before its
  call.

**One real post**
- OpenAI Cookbook, "Better performance from reasoning models using the Responses API": passing
  reasoning items back gave about a 3% gain on SWE-bench with the same prompt:
  https://developers.openai.com/cookbook/examples/responses_api/reasoning_items

### 3. Anchoring bias (a blind second opinion): must apply now

**What it is**
- A model that is shown an earlier verdict leans toward it, even when told to ignore it.
- A second opinion only counts if it is formed without seeing the first.

**Why it matters here**
- The producer calls the look when it needs eyes. The usual case is a shop owner disputing a
  clip.
- `look.py:_version_shown` and `look.py:_try_of` add `judge_words(...)` to what the look is
  told: "The judge's verdict: pass...", its checks, and "what it saw happen, second by second".
- The judge is known to be wrong. On 45 round-6 clips it passed 5 that Julian failed and failed
  9 that he passed (`docs/decision-map.md`). When the owner says "the brush is the wrong
  colour" about a clip the judge passed, the look starts from "pass".
- This is close to taught concept 5 (independent evidence), but not the same. In concept 5 the
  maker chose the checker's evidence. Here the checker is shown another checker's answer.

**How to apply it**
- Leave the judge's words out of the look's handoff. Give it only the frames, the sent list,
  the scene and the question.
- If the producer needs the judge's verdict, put it in `look`'s tool answer after the look's
  words, labelled as the judge's. A disagreement then shows to the producer and on the agent
  page.
- Check before deciding: on the round-6 clips where Julian and the judge disagreed, run the look
  with and without the verdict and count which one matches his grade. These are paid model
  calls, so Julian decides whether to run them.
- Test: `tests/test_look.py` checks that `looked.handoff` contains no "judge's verdict" text.

**One real post**
- Kapetanovic et al., "Anchoring Bias in LLM-as-a-Judge Systems: Prior Scores Compromise
  Evaluation Independence" (Aug 2026). 7 of 8 models were anchored. On labelled data the earlier
  label "blocked 48% of error corrections", and a "disregard it" warning didn't remove the
  effect: https://arxiv.org/abs/2608.25869

### 4. Append-only context: must apply now

**What it is**
- Once a model has been shown something or has done something, that part of its history never
  changes. New things go only at the end.
- If the history changes, the model reads a past it didn't live through, and the cached prefix
  breaks.

**Why it matters here**
- `loop.py:_conversation` puts a scene step or a system note "where the agent was first given
  it", but puts a message where its write time (`message.created_at`) falls.
- So a message the owner sends while a turn is being decided appears *before* that turn's
  reply and calls in every later rebuild.
- `tests/test_producer_messages.py:test_an_interrupt_is_taken_and_read_by_the_producer_already_working`
  locks this in. Turn 1 saw only "Make an ad for ..." and called `read_page` with
  `target_seconds: None`. Turn 2 is shown "Make it 10 seconds, please." *before* that call, so
  the producer seems to have ignored the owner.
- Steering mid-run is the reason ADR 0001 exists, so this happens on the feature's main path.
- The order also depends on clock times written by different tables and processes
  (`created_at`, `producer_read_at`, `read_at`).

**How to apply it**
- Treat a message the same way as a step: set a `given_at` in `_read` the first time a turn
  includes it, and put messages not yet given at the end.
- Better: give the session one sequence number, assigned when an item first goes into a turn's
  input or is written by a turn, and sort by that number alone. Messages already have `seq`;
  extend the same idea to the whole conversation.
- Change the test above to expect turn 2 =
  ["Make an ad ...", tool_use read_page, "Make it 10 seconds, please."].
- Add a test that rebuilds after every turn and checks that each turn's input begins with the
  previous turn's input plus its output (a fold is the only allowed exception).

**One real post**
- Manus, "Context Engineering for AI Agents: Lessons from Building Manus": "Make your context
  append-only. Avoid modifying previous actions or observations." and "Ensure your
  serialization is deterministic.":
  https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus

### 5. Recitation (the agent keeps its own task list): good to know

**What it is**
- The agent keeps a short checklist of what it still owes, and code puts it at the end of the
  context on every turn.
- The list keeps its goals from drifting over many tool calls, and it survives a fold.

**Why it matters here**
- `snapshot.py:ad_as_it_stands` repeats the ad's state (plan, versions, cost), not the owner's
  open wishes.
- `tasks.py:_whats_left` counts a scene as unfinished only when it has no clip. A note like
  "calmer music and make scene 2 shorter" that is half done gets no nudge.
- Once old calls fold, the owner's wishes survive only in old messages.
- ADR 0011 names the gap: "a non-look wish the producer forgets to pass (for example 'no
  faces') has no backstop."

**How to apply it**
- Add a free tool (`runs_while_paused`), for example `note_wishes`. The producer writes each
  open wish there in the owner's words and ticks it off when done. Code shows the open ones in
  the snapshot.
- `_nudge` also counts open wishes, so stopping with a wish undone gets the same carry-on
  note.
- What the app tracks and nudges on is a design decision, so it needs Julian's yes.

**One real post**
- Anthropic, "Effective context engineering for AI agents", the section on structured
  note-taking: "Like Claude Code creating a to-do list ... allows the agent to track progress
  across complex tasks":
  https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents

### 6. Tool masking (choosing which tools can be called each turn): good to know

**What it is**
- Keep every tool's definition in the prompt, so the prompt stays stable and caches well, but
  tell the API which tools may be called this turn. The model then can't pick a tool that would
  only be refused.

**Why it matters here**
- `producer.py:PRODUCER` offers 31 tools on every turn. OpenAI's guide says to "aim for fewer
  than 20 functions available at the start of a turn" (a soft suggestion).
- Some refusals only repeat facts code already knows: no plan yet, paused, a scene still
  rendering. Each comes back as a tool answer (`loop.py:_run`, `_held_by_the_pause`) and costs a
  whole extra turn with the full context. At the sizes in concept 1, that is real money.
- Code already holds one such mask: `Tool.runs_while_paused`.

**How to apply it**
- Measure first, for free: count the `ToolCall` rows with `answer='cant'` per ad. The agent page
  already marks them (`evals/agent_page.py:_chip`).
- If there are many: in `take_turn`, send
  `tool_choice={"type": "allowed_tools", "mode": "auto", "tools": [...]}`, built only from what
  code knows. While paused, that's the `runs_while_paused` set; before a job exists, the page
  and owner tools. This adds no order beyond what code already refuses, so ADR 0001 still holds.
- Test: a paused session sends a request whose allowed list holds only the free tools.

**One real post**
- OpenAI's function-calling guide. On `allowed_tools`: you "not modify the list of tools you
  pass in, so you can maximize savings from prompt caching". It also gives the fewer-than-20
  advice: https://developers.openai.com/api/docs/guides/function-calling

### 7. Double-texting strategy: good to know

**What it is**
- The rule for a user message that arrives while the agent is working. The named options are
  reject it; enqueue it (finish, then read it); interrupt (stop, keep the progress, read it
  now); or roll back.

**Why it matters here**
- The producer enqueues. After a turn, `loop.py:_work` runs every call that turn asked for.
  Between calls, `_held_since_the_user_spoke` checks only the pause, despite its name.
- So "make it a man", sent while the producer is deciding to `create_person`, still pays for
  the woman. Only the pause button stops it, and the owner has to know to press it.

**How to apply it**
- A middle option: before each call that spends money (not `runs_while_paused`), check for an
  owner message newer than the turn's input. If there is one, refuse the remaining calls without
  running them ("the shop owner wrote since you asked for this: read it first"), using
  `_refuse_unrun` as the pause does.
- Calls that cost nothing still run. The price is one extra turn, with no money wasted.
- Test: `meanwhile(say(...), turn(calls=[create_person ...]))`. `create_person` is refused
  without running, nothing is paid, and the next turn reads the message.
- What a message does to work in flight is a design decision, so it needs Julian's yes.

**One real post**
- LangChain docs, "Double texting" (reject, enqueue, interrupt, rollback):
  https://docs.langchain.com/langsmith/double-texting

### 8. Unfaithful explanations (the `why` isn't the real cause): good to know

**What it is**
- When a model says why it acted, that is a self-report. Research shows these reports often
  leave out what actually drove the choice.

**Why it matters here**
- Every producer call needs a `why` (`loop.py:WHY`, `_why_apart`). The per-ad page shows it
  first (`evals/agent_page.py:_call`), so that Julian can "see dumb or expensive moves".
- If a dumb move is diagnosed from its `why`, the prompt fix targets the model's story instead
  of the real cause.

**How to apply it**
- Treat the `why` as a label. Look for causes in what the turn was given: its
  `ModelCall.handoff` and its trace hold the exact input.
- To confirm a cause, replay that one turn with one thing changed (the gateway keeps exact
  handoffs) and see whether the move changes. Each replay is a paid model call, so Julian
  decides.
- On the agent page, link each call to its turn's input, next to the `why`.

**One real post**
- Anthropic, "Reasoning models don't always say what they think": Claude 3.7 Sonnet mentioned
  the hint it used 25% of the time, DeepSeek R1 39%:
  https://www.anthropic.com/research/reasoning-models-dont-say-think

## Also seen here

- **1. Stopping conditions:** `loop.py:settle` caps the agents at 100 tool calls per message
  (`settings.MAX_TOOL_CALLS_PER_MESSAGE`), but there is no dollar cap per message or per ad.
  It's a known limitation: "Nothing limits how often the producer retries a failing step but
  the 100-calls-per-message cap" (`docs/limitations.md`).
- **2. Agent evals on a real model:** `producer.py:PRODUCER` has no eval
  (`docs/decision-map.md`). Every test scripts the producer (`tests/test_steering_rows.py`), and
  the model moved from gpt-5.6-sol to gpt-6.1-sol
  (`tests/test_frozen_rules.py:test_each_job_that_was_on_gpt_5_6_sol_runs_on_gpt_6_1_sol`)
  with nothing to show its choices stayed as good.
- **3. Calibrating an LLM judge:** `look.py:look_at` says what is wrong and why, and the
  producer pays for remakes on those words, but the look has never been checked against
  Julian's grades (`docs/decision-map.md`: "No eval").
- **4. A stable prompt prefix:** `openai_adapter.py:take_turn` sends no `prompt_cache_key`, and
  `catalog.py:cost_usd` charges cached input at the full price (`cached_tokens` is never read).
  So the records overstate the producer's cost and nobody sees the cache hit rate. Concept 4
  above (append-only) is a second thing that breaks the cache.
- **7. An explicit state machine:** `tasks.py:_something_will_wake_it` works out "is the producer
  waiting?" from eight separate checks across tables each time it is about to stop (#272 part
  2), and `tasks.py:_stop_unless_theres_more_to_read` adds more on top.
