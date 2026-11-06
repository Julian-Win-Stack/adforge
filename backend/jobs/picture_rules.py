"""Rules for what a starting picture may show, so the video model can film it (spec #401,
ticket #406). The planner, the scene agent's picture form and the picture check read the
same words, so a rule says the same thing wherever it is told."""

# Round 11 clip 4198: label letters garbled with the product at middle distance.
LABEL_RULE = (
    "A label faces the camera only when the product fills at least a third of the picture's "
    "height; otherwise the product is far away, turned away or covered by a hand, never in "
    "between."
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

# The whole percent of the picture's height a product with a label facing the camera must
# fill: a third, as the check answers in whole percent (33 meets it).
LEAST_LABEL_PERCENT = 33


def label_too_small(faces_camera: bool, height_percent: int) -> bool:
    """A label facing the camera on a product filling under a third of the height."""
    return faces_camera and height_percent < LEAST_LABEL_PERCENT
