"""Rules for what a starting picture may show, so the video model can film it (spec #401,
ticket #406). The planner, the scene agent's picture form and the picture check read the
same words, so a rule says the same thing wherever it is told."""

# Round 11 clip 4198: label letters garbled with the product at middle distance. Told to
# the planner and the picture form only; no check reads it (ticket #427 replaced #406's
# one-third size gate, which a product in use can't meet).
LABEL_RULE = (
    "A scene that only shows the product to the lens holds it close to the lens, so its "
    "printed letters are big. A scene where the product is in use has no size rule: the "
    "product is as big in the frame as the action needs."
)

# Round 11 clip 4250: a sealed stick pack was "poured" without being opened.
OPEN_RULE = (
    "The product is shown ready for its action (a pack torn, a cap off) only when a page "
    "photo shows what it looks like open (the dropper, the pump, the powder)."
)

# For the planner only: no plan is sent back for it; the picture check catches the rest.
PLAN_OPEN_RULE = (
    f"{OPEN_RULE} With no such photo, pick an action that doesn't need the product opened. "
    "A scene never films the opening itself unless opening is the point of its line and a "
    "photo shows the open state."
)

# Proven rule, widened from "a side" with Julian's OK, Nov 6 06:13.
SIDE_RULE = "Never show a side or part no photo shows."
