"""The starting pictures for the way-1 tests (#94): toilet cleaner (08-3) and bag (05-3).
Each is made from the shop's product photo only, used for how the product looks; the setting
is written in the prompt (broll-picture-logic.md, "Start picture"). Both are made at once.

    docker compose exec backend python broll-test/start_pictures.py [scene ...]

Keys go to MEDIA_ROOT/broll-test/boreal-h3/frames-pictures.json."""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")

import django  # noqa: E402

django.setup()

from gateway.gateway import edit_picture  # noqa: E402

OUT = Path("/app/media/broll-test/boreal-h3")
ASKS = {
    "08-3": {
        "photo": "broll-test/photos/08/01.png",
        "prompt": (
            "Upright 9:16 phone photo of a clean white toilet in a bright bathroom. The bottle "
            "from the picture stands on the floor right beside the toilet, its front label "
            "facing the camera and looking exactly as in the picture. A hand holds a toilet "
            "brush resting inside the bowl, about to start scrubbing. The toilet and bottle are "
            "in the middle of the frame, with plain wall above and floor tiles below. Natural, "
            "casual phone-video look."
        ),
    },
    # The redo of 08-3: the first one showed the bottle standing idle while a hand scrubbed,
    # so it showed nothing the line claims. Here the gel clings over a stain ring.
    "08-3b": {
        "photo": "broll-test/photos/08/01.png",
        "prompt": (
            "Upright 9:16 close-up phone photo looking into a white toilet bowl with a visible "
            "brown hard-water stain ring around the inside, at the waterline. A hand holds the "
            "bottle from the picture upside down and tilted, its angled neck tucked under the "
            "front rim, nozzle pointing down. The bottle's front label faces the camera and "
            "looks exactly as in the picture. A small bead of clear, thick gel is just starting "
            "to come out of the nozzle. The bottle and the stain ring are in the middle of the "
            "frame, with the raised seat above and the inside of the bowl below. Bright "
            "bathroom light. Natural, casual phone-video look."
        ),
    },
    "05-3": {
        "photo": "broll-test/photos/05/01.jpg",
        "prompt": (
            "Upright 9:16 phone photo of a woman standing in a bright, simple room, wearing the "
            "bag from the picture across her body: the chain strap goes over one shoulder and "
            "across her chest, and the bag rests at the opposite hip, facing the camera. The bag "
            "looks exactly as in the picture. She stands still, about to turn a little. Framed "
            "from her shoulders to her knees, the bag in the middle of the frame, with plain "
            "wall above and floor below. Natural, casual phone-video look."
        ),
    },
}


def make(scene_id: str) -> str:
    ask = ASKS[scene_id]
    return edit_picture(
        job=None, purpose="make_starting_picture", prompt=ask["prompt"], pictures=[ask["photo"]]
    )


def main() -> None:
    wanted = sys.argv[1:] or list(ASKS)
    with ThreadPoolExecutor() as pool:
        made = dict(zip(wanted, pool.map(make, wanted), strict=True))
    for scene_id, key in made.items():
        print(scene_id, key)
    kept = OUT / "frames-pictures.json"
    old = json.loads(kept.read_text()) if kept.exists() else {}
    kept.write_text(
        json.dumps({**old, **{s: {**ASKS[s], "picture": made[s]} for s in made}}, indent=1)
    )


if __name__ == "__main__":
    main()
