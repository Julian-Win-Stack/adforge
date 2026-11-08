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
# Ticket #477: what a page's how-to photo (a guide, steps, a recipe card) is followed for, by
# the planner and the scene agent alike.
HOW_TO_METHOD_ONLY = (
    "Follow their method: what goes in, the steps and their order. Never copy their pose, "
    "hands or framing, and never their printed words."
)

# Round 12: 13 of 19 hold-to-lens starting pictures pinched the product by a fingertip or
# an edge to keep the label clear. Julian's design, Nov 8 04:54: a natural grip that fits
# the product's size; the picture form only, no check question.
GRIP_RULE = (
    "When a person holds the product, they hold it the way a real person does for the "
    "action (showing it or using it), with a grip that fits its size and weight: a small "
    "product can sit between the thumb and two fingers, a big one sits in the whole hand. "
    "Never balanced on a fingertip, pinched by one edge, or held up by one finger. A "
    "natural grip matters more than a fully readable label, so fingers may cover part of it."
)

# Round 12 Crocs scene 2: a fact-check rewrite told "hands and product only" swapped feet
# walking in the clogs for a hand holding them. Julian's rule, Nov 8 19:03: as long as the
# face isn't shown, any of her body may be; a scene with a face shows the presenter, and
# strangers' faces in photos are swapped for hers (jobs/face_swap.py). Told everywhere a
# scene's kind is described, so no prompt narrows it again.
NO_FACE_RULE = (
    "A B-roll scene never shows her face: it may show any part of her body with the "
    "product, such as her hands, feet, legs, wrist or neck, and her voice speaks off camera."
)

# Round 12 Crocs scene 2: the clogs drawn beside a made-up outfit. Julian, Nov 8: copy the
# outfit from the page, never make one up. The room stays the ad's shared room and the pose
# follows the scene's action. The picture form and the scene agent only; no check reads it.
WORN_RULE = (
    "When the product is worn, draw it as a page photo shows it worn (pick that photo) and "
    "copy everything from that photo except the face, the room and the pose: the outfit, "
    "the styling and how it is worn. Never make up an outfit. The room stays the shared "
    "room, and the pose follows the scene's action."
)
