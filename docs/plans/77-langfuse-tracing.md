# Plan: #77 — Send every agent turn, tool call and model call to Langfuse

What and why: the ticket, #77, and "Traces" under Further Notes in spec #1. This page is
how to build it.

## Decisions already made

- **Langfuse, on its free cloud plan (Hobby).** Not self-hosted: that is 6 services and
  about 16 GB of RAM. Chosen over Phoenix, Opik, LangSmith, Braintrust, Helicone and
  TraceRoot on 2026-09-26/27: MIT licence with every feature free, shows images, audio and
  video inside traces, and has annotation queues and experiments for the critic and evals
  later.
- **The `ModelCall` table stays the only source of cost and time numbers.** Langfuse is
  for looking at a run. Nothing reads numbers back from it.
- **Off when no keys are set.** Tests and CI set none, so they never send anything.
- **A tracing failure never stops work.** Langfuse down, wrong keys, a media upload that
  fails: logged, never raised into a tool, a turn or a scene step.
- **The tree:** one trace for each time the producer works on a message (one call of
  `loop.run`), with the chat session as the Langfuse session, so a session's traces are
  grouped. Inside a trace: each producer turn → the tools that turn asked for → the model
  calls each tool made.
- **A background scene step finds its parent through the database, saved, not
  calculated.** Langfuse can make a *trace* ID from a seed (`create_trace_id(seed=...)`),
  but a step's own ID (its observation ID) is always random. So when a tool's step starts,
  its trace ID and observation ID are saved on the `ToolCall` row (two new columns, one
  migration). `run_scene_step` already loads `step.tool_call` and re-enters
  `charged_to(step.tool_call)` (`backend/agents/tasks.py:201`) so costs land on the right
  tool call; tracing hooks in there too, starting the scene step's work with
  `trace_context={"trace_id": ..., "parent_span_id": ...}` read from that row. Agreed with
  the user: the task message is not the place, because only the database survives a
  re-queue or a restart.
- **A tool call run again after a restart** (`_settle` for an unfinished call in
  `loop.run`) gets a new step in the new trace, and its saved IDs are replaced, so
  scene steps started from then on land under the new one.
- **Only one module imports `langfuse`**, like only the gateway imports `openai`: add it to
  the ruff `banned-api` list in `backend/pyproject.toml` with a per-file ignore for that
  module.

## Where it hooks in

| Place | What it records |
|---|---|
| `loop.run` (`backend/agents/loop.py:88`) | The trace for one message's work, with the session ID |
| Each `take_turn` inside it | A producer turn (as an agent step) holding the turn's model call |
| `_run` (`backend/agents/loop.py:160`), next to `charged_to(call)` | A tool step: tool name, arguments, what it handed back. Saves its IDs on the `ToolCall` |
| `run_scene_step` (`backend/agents/tasks.py:193`), next to `charged_to(step.tool_call)` | The scene step's work, under the tool step whose IDs are saved on `step.tool_call` |
| `_recorded` (`backend/gateway/gateway.py:531`), every attempt | A generation: model, purpose, handoff, output, tokens, cost, duration, outcome, error, decision, reason. Pictures shown and pictures, audio or clips made go in as `LangfuseMedia`, their bytes read through `adforge.file_store.read` |
| `_answered_before` (`backend/gateway/gateway.py:608`) when it hands back a record | A step marked as answered from its record, with no cost |

## Things to check first

- **The SDK is v4** (4.15.6 on PyPI on 2026-09-27). The docs pages read while planning show
  `get_client`, `start_as_current_observation(as_type=..., trace_context=...)`,
  `create_trace_id`, `propagate_attributes` and `LangfuseMedia`. Check each name against the
  v4 reference before using it.
- **Python 3.14.** PyPI says `>=3.10,<4.0`. Install it with `uv add langfuse` and run the
  tests before writing anything else.
- **Celery forks its workers.** The SDK sends in a background thread, which doesn't
  survive a fork. Create the Langfuse client lazily, in the process that uses it, never
  at import time, and flush at the end of each task (`run_producer`, `run_scene_step`).
- **How to turn it off cleanly** when keys are missing: find the SDK's own switch
  (for example a `tracing_enabled` option) rather than wrapping every call in `if`.

## Tests

The project fakes every model at one seam (`gateway.use_model`, `gateway/fake.py`). Do the
same for tracing: the tracing module takes a recorder, and tests swap in an in-memory one.
Test through the chat API, as the other tests do.

- No keys: nothing recorded, every existing test passes.
- One message's work: turn → tool → model call, with cost and tokens.
- A scene step's model call lands under the tool call that started it.
- The recorder raising doesn't fail a tool, a turn or a scene step.

## Your part (the user's)

1. Sign up at cloud.langfuse.com, make a project `adforge`, make API keys.
2. Put `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_BASE_URL` in `.env`.
   `docker-compose.yml` already passes `.env` to the backend and the worker.
3. Once built, run one real session and click through its trace before making the test
   ads.
