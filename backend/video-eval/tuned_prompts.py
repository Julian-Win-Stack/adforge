"""The prompt variants the eval tests. Nothing here is used by the app: if a variant wins,
its text is moved into backend/jobs/scenes.py and the clip adapters by a
ticket of its own."""

import sys

sys.path.insert(0, "/app")

from jobs.scenes import pose_for  # noqa: E402

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
    print("---\nA:", TALKING_A_LIVELY, "\n---\nB:", TALKING_B_CALM)
