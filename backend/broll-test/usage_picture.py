"""The toilet cleaner's starting picture (08-3), made with the three forced steps in
broll-picture-logic.md ("Using the product right"): the usage fact from the page's "how to
use", a scene built on it, and a checker model's yes/no questions on the picture before any
video is paid for. One picture and one check; a "no" stops here, it isn't remade unasked.

    docker compose exec backend python broll-test/usage_picture.py

Writes MEDIA_ROOT/broll-test/boreal-h3/usage-08-3.json."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")

import django  # noqa: E402

django.setup()

from pydantic import BaseModel  # noqa: E402

from gateway.gateway import call_model as call_model  # noqa: E402
from gateway.gateway import edit_picture as edit_picture  # noqa: E402
from gateway.types import Handoff  # noqa: E402
from gateway.types import Image as Image  # noqa: E402

OUT = Path("/app/media/broll-test/boreal-h3")
PRODUCT = "broll-test/photos/08/01.png"

# Step 1: from the page ("Point nozzle under the rim, squeeze, then scrub"; "Extended reach
# neck provides 360 degree coverage under rim").
USAGE = (
    "The bottle is held neck-down with its nozzle tucked under the toilet bowl's rim; "
    "it is squeezed, the gel runs down the inside of the bowl, then the bowl is scrubbed."
)

# Step 2: the line is "The clinging gel fights rings and stains from hard water and rust."
PROMPT = (
    "Upright 9:16 close-up phone photo looking down into a white toilet bowl with a visible "
    "brown hard-water stain ring on the inside wall. A hand holds the bottle from the picture "
    "tipped steeply neck-down, its long angled neck reaching under the front rim so the nozzle "
    "is hidden beneath the rim's edge. The label turns with the bottle, tilted and partly "
    "upside down, as on a real bottle held this way, and otherwise looks exactly as in the "
    "picture. A ribbon of clear, thick gel has just started to come out from under the rim "
    "and is beginning to run down the inside wall toward the stain ring. The rim, the bottle "
    "and the stain ring are in the middle of the frame, with the raised seat above and the "
    "inside of the bowl below. Bright bathroom light. Natural, casual phone-video look."
)

# Step 3: the checker's questions, built from the usage fact and the line.
QUESTIONS = [
    "Is the bottle's nozzle under the bowl's rim, not hanging in the open bowl or in the water?",
    "Is clear gel coming out from under the rim and running down the inside wall of the bowl?",
    "Is a brown stain ring visible on the bowl's wall, where the gel is running?",
    "Is the label turned the way it would be on a real bottle held at this angle?",
    "Do the bottle and its label look like the bottle in Photo 1?",
]
CHECK = (
    "You check a picture made for a product video before the video is paid for. Photo 1 is "
    "the shop's photo of the product. Picture 2 is the picture made. Answer each question "
    "about Picture 2 strictly from what you see, with yes or no and one short reason. Answer "
    "no if you are unsure."
)


class CheckHandoff(Handoff):
    how_the_product_is_used: str
    questions: list[str]


class Answer(BaseModel):
    question: str
    yes: bool
    reason: str


class Check(BaseModel):
    answers: list[Answer]


def main() -> None:
    picture = edit_picture(
        job=None, purpose="make_starting_picture", prompt=PROMPT, pictures=[PRODUCT]
    )
    print("picture:", picture)
    check = call_model(
        job=None,
        purpose="choose_broll_picture",
        instructions=CHECK,
        handoff=CheckHandoff(how_the_product_is_used=USAGE, questions=QUESTIONS),
        output=Check,
        images=[Image(label="Photo 1", key=PRODUCT), Image(label="Picture 2", key=picture)],
    )
    for a in check.answers:
        print("YES" if a.yes else "NO ", a.question, "|", a.reason)
    print("PASS" if all(a.yes for a in check.answers) else "FAIL")
    (OUT / "usage-08-3.json").write_text(
        json.dumps(
            {
                "usage": USAGE,
                "prompt": PROMPT,
                "photo": PRODUCT,
                "picture": picture,
                "check": check.model_dump(),
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
