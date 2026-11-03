---
name: design-mentor
description: "Read one design spec and teach Julian the software and AI engineering concepts it is missing, with how to apply each in the build."
disable-model-invocation: true
---

# Design mentor

Julian has no senior engineer to learn from yet. This skill stands in: it reads his design, finds the concepts he is not applying, and teaches him each one through his own build.

## 1. Gather the design

Ask which spec to review if he didn't name one. Then read:
- The spec (`spec.md`: what he approved).
- Its findings file next to it (causes, options, why each pick was made). This holds the design conversation.
- `CONTEXT.md` and `docs/adr/` at the repo root.
- The code the spec touches. Read it before describing how the app behaves, and cite `file:function`.

## 2. Think the design through

Think hard and carefully. Look for concepts, in software engineering or AI engineering, that the design should be using and isn't. Guess what Julian doesn't know from what the design leaves out.

- Core software engineering (queues, retries, idempotency, concurrency, data modelling, testing, caching): trust your own knowledge.
- AI engineering (agent patterns, evals, judges, model and video API features) and specific tools (Celery, Django, Postgres versions): search the web for current practice. These change fast.

## 3. Report

List every concept found, ranked by impact on this build. Mark each one **must apply now** or **good to know**.

For each concept:
- **What it is**, in plain words. Use AdForge as the example; a textbook example is fine when it explains faster.
- **Why it matters here**, tied to a specific spot in the spec or code.
- **How to apply it** in this build, concretely.
- **One real post** (engineering blog, research write-up, docs) where a team used it, with the link.

Keep it scannable: short lines, no long paragraphs. No praise of the design. Name problems plainly.

This is teaching, not building. Don't change the spec or code. Applying a concept to the app is a design change and needs Julian's yes through the normal flow.
