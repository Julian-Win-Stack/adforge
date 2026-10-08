"""The prompt variants the eval tests. Nothing here is used by the app: if a variant wins,
its text is moved into backend/jobs/scenes.py and the clip adapters by a
ticket of its own."""

import sys

sys.path.insert(0, "/app")

from jobs.scenes import BROLL_PICTURE_INSTRUCTIONS, pose_for  # noqa: E402

# --- B-roll ---------------------------------------------------------------------------

# Replaces the app's paragraph that asks the picture-planning model for a motion prompt.
_NEW_MOTION_RULE = (
    "Then write the video model's motion prompt: one continuous movement, and how it looks, "
    "in one or two short sentences. The video model can move a person, a hand or the camera "
    "smoothly; it cannot work a mechanism or change the product. So ask for exactly one "
    "movement, by the person, a hand or the camera, that goes on through the whole clip: a "
    "hand that keeps turning the product, a person who keeps walking, a camera that keeps "
    "gliding. Never ask for a part of the product to rise, turn, fold, open, pour or move on "
    "its own, and never for the product to bend, squash or change shape or size: the product "
    "stays rigid. Never ask for two actions in a row, such as placing something and then "
    'picking it up. If "shows" describes a mechanism working or several actions, pick the '
    "one movement a person or the camera can make that shows it best, and say so in the "
    "reason.\n"
)


def _swap_motion_rule(instructions: str) -> str:
    start = instructions.index("Then write the video model's motion prompt")
    end = instructions.index("Make nothing up, in either prompt.")
    return instructions[:start] + _NEW_MOTION_RULE + instructions[end:]


TUNED_BROLL_PICTURE_INSTRUCTIONS = _swap_motion_rule(BROLL_PICTURE_INSTRUCTIONS)

BROLL_NEGATIVE_PROMPT = (
    "product changing shape or size, product bending or squashing, floating objects, "
    "extra hands, extra fingers, extra limbs"
)

# --- Talking ----------------------------------------------------------------------------

# The app today (talking_motion_prompt("")): "The person talks to the camera naturally, like
# a casual phone video. <handheld pose> Minimal hand movement."

# A: lively. Against the slow-motion look: asks for a natural pace and clear mouth movement.
TALKING_A_LIVELY = (
    "The person talks to the camera like a casual phone video, at a natural everyday pace, "
    "with clear, precise mouth movement on every word and small natural head movement. "
    f"A relaxed everyday expression. {pose_for('')} Minimal hand movement."
)

# B: calm. Against the over-expressive face: asks for stillness and a neutral face.
TALKING_B_CALM = (
    "The person talks to the camera like a casual phone video, calm and still, with a "
    f"relaxed, neutral face and no big expressions. {pose_for('')} Minimal hand and head "
    "movement."
)

# Appended to the [SPEECH] section. fal's own example prompt says when the speech starts.
SPEECH_NOTE = "The person starts speaking at once, from the very first moment."

TALKING_NEGATIVE_PROMPT = (
    "slow motion, exaggerated expression, shocked face, extra hands, extra fingers"
)

if __name__ == "__main__":
    print(TUNED_BROLL_PICTURE_INSTRUCTIONS)
    print("---\nA:", TALKING_A_LIVELY, "\n---\nB:", TALKING_B_CALM)
