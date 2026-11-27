# The plan card's lock is code

Spec [#768](https://github.com/Julian-Win-Stack/adforge/issues/768), ticket [#777](https://github.com/Julian-Win-Stack/adforge/issues/777).

Once the page is read, every tool that plans scenes, makes a person, a picture or music, starts a
scene agent or assembles refuses until the shop owner answers the job's newest plan card. Only the
chat's structured answer to that card sets `Job.chosen_plan`; a message typed in the chat never
does. While a card is open the producer is not nudged. [`hold` and `answer`](../../backend/agents/plan_card.py)

Why code and not an instruction: [ADR 0015](0015-code-counts-tries-for-asked-changes.md) showed an
instruction the producer broke in 33 of 60 sessions held in 0 of 20 once code enforced it. A card
that money can start without is not a promise to the shop owner.

A job planned before plan cards existed has scenes, and the lock never holds for it. Tests about
something else run with `PLAN_CARD_LOCK` off, as if every card were answered; tests marked
`plan_card` run with it on, as the app does.
