# The plan card's lock is code

Prototype, Nov 27, 2026, for [#777](https://github.com/Julian-Win-Stack/adforge/issues/777) (spec [#768](https://github.com/Julian-Win-Stack/adforge/issues/768)).

Once a page is read, every tool that plans scenes, creates a person or music, starts a scene agent
or assembles refuses until the job's plan card is answered (`agents/plan_card.py`). Only the chat's
structured answer to the newest card sets `Job.chosen_plan`; a free-text message never does.

Why code and not an instruction: [ADR 0015](0015-code-counts-tries-for-asked-changes.md) showed an
instruction the producer broke in 33 of 60 sessions held in 0 of 20 once code enforced it. A card
that money can start without is not a promise to the shop owner.
