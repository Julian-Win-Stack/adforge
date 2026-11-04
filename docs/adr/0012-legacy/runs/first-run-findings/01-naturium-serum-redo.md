# 01 · Naturium Multi-Active Exosome Serum (redo)
**Result:** failed at `create_person` (Inworld `design_voice` 400, twice); plan made, no scenes, no ad. (known)

## Silent problems
- **The portrait was paid for and never shown** — `[23:38:16] draw_person` succeeded ($0.0289, `draw_person_CK4l9gZ.png`), then `design_voice` failed, so `CreatePerson.run` raised before `messages.add`. The chat has no attachment on any message (checked in the DB: 8 messages, 0 attachments), yet first-run.md says the run failed "right after … the presenter's picture" as if it was seen. The retry (`[23:39:00]`) correctly reused the portrait (only `design_voice` ran, 0.2 s), so nothing was double-paid. Severity: low.
- **Why the 3rd try later worked was luck, not a fix** — the rejected description is "A clear, warm woman’s voice…" with a curly apostrophe (known). The `sample` text also contains one ("Meet Naturium’s…") in all three runs, and `design_voice` succeeded in the first and 3rd tries, so Inworld only rejects non-ASCII in the *description*. The 3rd try's description happened to have no apostrophe. Nothing in the code strips or checks it, so the same failure will recur on any plan whose voice description has a curly quote. Severity: med (recurrence risk).
- **Plan differs from the first try with no new input** — same page, same photos, same first message, but 5 scenes instead of 4, two bottle-only B-rolls instead of an application scene, product name "Naturium’s Multi-Active Exosome Serum" instead of "Multi-Active Exosome Serum", and a different presenter (mid-30s vs late-30s). Fine in itself, but it shows the plan is not repeatable, so a redo is a new ad, not a retry. Severity: low.
- **"Sealed" bottle** — both B-roll `shows` say "the sealed serum bottle" (`[23:37:53]`). The page says nothing about a seal; the photos show a capped bottle. Never rendered, so no harm here. Severity: low.

## Loud failures
- `design_voice` 400 Bad Request at `[23:38:36]` and `[23:39:00]`. The tool told the producer only "an unexpected error stopped the tool. The details are in the server log", and the producer told the owner "hit an unexpected error" then "failed again due to a server error". A 400 is the app's own request being rejected, not a server error, so the second message misdescribes it. (known: the app records only the status line)
- The producer promised "I'll pick up from the finished five-scene plan" and did (plan reused, portrait reused).

## Waste
- The retry after "please try again" was certain to fail: same description, same endpoint, deterministic 400 (0.2 s, no cost). The producer had no way to know that, since the tool hides the reason.
- $0.029 portrait made for an ad that never used it; $0.048 plan likewise.

## Bottom line
Nothing delivered, and the owner was told it was a server problem when it was our own request. The single fix is to ASCII-fold (or reject in the plan validator) the voice description before it goes to Inworld, and to record Inworld's reply body so the tool can say why.
