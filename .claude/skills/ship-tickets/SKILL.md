---
name: ship-tickets
description: "Build a spec's tickets, one reviewed PR per ticket, and squash-merge each once CI is green."
disable-model-invocation: true
---

# Ship tickets

Input: a spec issue link. The spec always has tickets.

## Build

- Run `implement-spec` on the spec, but give each ticket its own branch (cut from `main`, or from its blocker's unmerged branch) and its own PR instead of one integration branch. Build every ready ticket at once.
- **Review each ticket in its own sub-agent:** when a ticket's implementer sub-agent is done, start a separate review sub-agent that runs `supermatt:code-review` at medium effort on that ticket's diff. This replaces `implement-spec`'s single review of the whole branch.
- Fix the findings, test, push. No second review.

## PR

- **Title:** imperative verb plus outcome, under 70 characters ("Plan ads when the page never names the brand").
- **Body:** the attribution lines, then only these sections, no filler or emoji:
  1. **Why:** the problem in 1-2 lines with real evidence. `Closes #N`.
  2. **What changed:** 3-5 bullets of behaviour, not files.
  3. **How I tested:** commands and before/after numbers.
- Title and body are the squash commit: no separate squash-message block.

## Merge

Squash-merge each PR once its CI is green. Fix red CI; never skip a test. After a blocker merges, rebase its dependants onto `main` and wait for green. Report merged PRs and any stuck ones with why.
