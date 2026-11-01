---
name: try-it
description: "Quick throwaway try of a change to how ads are made, checked free first, then on a small paid run, before a proper build."
disable-model-invocation: true
---

# Try it

A try answers one question fast: does this change make better ads? It is throwaway code. A good result is rebuilt properly with `implement`; the try itself is never merged.

## 1. Plan in plain words, get one yes

Before any code, post Julian one short plan:

- **The question** the try answers, and what a good result looks like.
- **Each rule, with what it takes away.** Every rule removes something: "send only what the scene needs" drops the front photo; "show contents only when the line names them" lets the planner hide them. Say both sides. Never add a rule of your own he didn't ask for; if one seems needed, it goes in the plan as a question.
- **Each kind of clip touched:** what it shows (product, its parts or contents, a person, the place) and where each thing's picture comes from. A thing with no source is a gap; name it now, not after the run.
- **The smallest paid run that answers the question** (a pictures-only check beats full ads; one ad beats six), its cost, and a cap.

His yes covers the whole try: build, free run, the paid run up to the cap, and the fixes in between. Don't ask again for anything inside it.

## 2. Build fast

- A throwaway branch from latest `main`. Push it; no PR, never merge.
- No new tests, no code review, no `implement`. Change the code directly.
- Box-only patches (an env switch that stops early, the busy-check off) are code too: they go through step 3.

## 3. Free run first, never skipped

"No tests" means no new tests. It never means "run it for the first time on paid ads."

1. Run the existing tests of every file you touched (`uv run pytest -q -n auto --dist worksteal tests/test_x.py ...`) and fix what breaks. Search for other code that checks what you changed (an old rule that only allows a field in one slot breaks silently).
2. Run one faked ad through the chat on the changed path, every paid service faked (the fakes in `backend/tests/conftest.py`; `tests/test_broll_sent_list_end_to_end.py` is the pattern). Print what each scene would send or make.
3. Read that output scene by scene against the plan's "what it shows" list. Every thing has a picture? No empty slot, no missing product, no photo used for the wrong job? Fix and rerun until it is clean.
4. Then one real ad (or one pictures-only ad) before the batch.

## 4. Paid run, on its own

- **Approved means no re-asks.** On a crash: find the cause, fix, free run again, retry, within the cap. Fallback reds are findings: log them for the report and keep going. Hold only on a real break: the same failure on 3+ ads, a service down, the cap reached. Then say so in one line.
- **Never sit on a permission card.** Reuse an open Remote Control session. If a new one needs his tap, ask once, then keep doing free work (read code, prepare the results page) while you wait.
- **Fast:** start ads in parallel within `docs/rate-limits.md`.
- **Say at once when anything stops,** in one line, with what restarts it. Never say "running" or "resuming" before you have checked it is. A "stop" from him applies only to what it was said about; if it could reach the box by mistake, tell the box right away that the run goes on.
- **Only what was asked.** No extra runs, no extra steps, no unasked checks.
- Report the cost when done.

## 5. Show everything raw

- One page with everything per scene: every picture sent and its job, failed tries, videos, the plan, the prompt, warnings, and an auto-saving notes box. Not a summary.
- Check the page scene by scene yourself before you send it, and name the gaps you see. Only Julian grades.
- When he has written notes, read them before anything else. His notes are ground truth.
- **Root cause = the earliest cause.** Ask why until you reach the first wrong decision (often your own build or a rule). A missing fallback is rarely the cause; a fallback only hides it.

## 6. Hand off

- A handoff file in `/mnt/project-files/handoffs/<topic>/`: branch and commits, box state and how to restore it, cost, results page, his notes, what's open.
- Restore the box when nothing runs.
- Good result: a plain-words spec of what worked, then a proper build with `implement` (tests, review, PR).
