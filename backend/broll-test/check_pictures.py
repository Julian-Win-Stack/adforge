"""The start and end pictures for the Boreal-H3 check (boreal_h3.py --check): scene #16
(Molly's Suds, 08-3) remade as the shot of its "clinging gel" claim, written by hand on
2026-10-03 from the product page: the nozzle under the rim (its "How to use" step 1) and a
clear gel, as the page says the cleaner has no dyes.

    docker compose exec backend python broll-test/check_pictures.py

The start picture is made from the scene's old starting picture (for the bathroom and light)
and the product photo (for the label); the end picture is an edit of the start picture, with
the product photo again. Their keys are written to
MEDIA_ROOT/broll-test/boreal-h3/check/pictures.json."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")

import django  # noqa: E402

django.setup()

from gateway.gateway import edit_picture  # noqa: E402

OUT = Path("/app/media/broll-test/boreal-h3/check")
OLD_START = "make_starting_picture_MfPOBVV.png"
PRODUCT = "broll-test/photos/08/01.png"

START = (
    "Close-up, upright 9:16 phone photo looking into a clean white toilet bowl at the "
    "underside of the front rim. A hand holds the bottle from the second picture tilted "
    "forward, its long angled neck tucked under the rim and the nozzle pointing down. The "
    "bottle's front label faces the camera and looks exactly as in the second picture. A small "
    "bead of clear, colourless, thick gel is just starting to come out of the nozzle. Soft, "
    "bright bathroom light like the first picture. The bottle and rim sit in the middle of the "
    "frame, so the top and bottom stay clear. Natural, casual phone-video look. No added text."
)
END = (
    "Edit the first picture. The hand has moved the nozzle a little further along under the "
    "rim. A ribbon of the same clear, thick gel now lines the underside of the rim, and a few "
    "thick drips have started to creep slowly down the inside wall of the bowl, clinging to it. "
    "Keep everything else exactly the same: camera, framing, light, the hand's grip, and the "
    "bottle and its label, which looks exactly as in the second picture. No added text."
)
MOTION = (
    "The hand gently squeezes the bottle and moves the nozzle along under the rim. Thick, clear "
    "gel comes out and clings to the bowl, slowly creeping down the wall. Slight handheld "
    "camera movement."
)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    start = edit_picture(
        job=None, purpose="make_starting_picture", prompt=START, pictures=[OLD_START, PRODUCT]
    )
    print("start:", start)
    end = edit_picture(
        job=None, purpose="make_starting_picture", prompt=END, pictures=[start, PRODUCT]
    )
    print("end:", end)
    (OUT / "pictures.json").write_text(
        json.dumps(
            {
                "start": start,
                "end": end,
                "start_prompt": START,
                "end_prompt": END,
                "motion_prompt": MOTION,
                "product_photo": PRODUCT,
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
