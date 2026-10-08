"""The toilet cleaner's scene (08-3) ending on its result (broll-picture-logic.md, "End on
the result"): a start picture of the gel on the stain ring with a brush going in, checked
before the video; then, with `--video-check`, the made video's last frame is checked for the
promised result.

    docker compose exec backend python broll-test/usage_scrub.py
    docker compose exec backend python broll-test/usage_scrub.py --video-check"""

import json
import subprocess
import sys
from pathlib import Path

from usage_picture import OUT, Check, CheckHandoff, Image, call_model, edit_picture

MEDIA = Path("/app/media")
EARLIER = "make_starting_picture_dCzVpsc.png"  # the gel picture, for the same bowl and light
USAGE = (
    "The gel is squeezed out under the rim and runs down the bowl; then the bowl is "
    "scrubbed with a toilet brush."
)
RESULT = "The brown stain ring is gone and the bowl is clean and white."
PROMPT = (
    "Edit the picture. Remove the bottle and the hand holding it. Keep the same toilet bowl, "
    "camera, light and brown hard-water stain ring. Streaks of the same clear, thick gel now "
    "cover the stain ring, clinging to the bowl's wall. A hand pushes a white toilet brush "
    "into the bowl from the top right; the bristles are just touching the stain ring, not yet "
    "scrubbing. The stain ring is in the middle of the frame, with the raised seat above and "
    "the inside of the bowl below. Natural, casual phone-video look."
)
PICTURE_QUESTIONS = [
    "Is clear gel on the bowl's wall, over the brown stain ring?",
    "Is the brown stain ring clearly visible?",
    "Is a toilet brush just entering the bowl, touching the ring but not yet scrubbing?",
    "Is the stain ring in the middle of the frame?",
]
# From the templates in broll-picture-logic.md ("does a job you can see").
VIDEO_QUESTIONS = [
    "Is the promised result visible by the end, coming from the product being used: is the "
    "stain ring gone where it was scrubbed?",
]
CHECK = (
    "You check a picture made for a product video. Answer each question about the picture "
    "strictly from what you see, with yes or no and one short reason. Answer no if unsure."
)


def ask(picture: str, questions: list[str], usage: str) -> bool:
    check = call_model(
        job=None,
        purpose="choose_broll_picture",
        instructions=CHECK,
        handoff=CheckHandoff(how_the_product_is_used=usage, questions=questions),
        output=Check,
        images=[Image(label="Picture", key=picture)],
    )
    for a in check.answers:
        print("YES" if a.yes else "NO ", a.question, "|", a.reason)
    return all(a.yes for a in check.answers)


def main() -> None:
    record = OUT / "usage-08-3-scrub.json"
    if "--video-check" in sys.argv:
        last = "broll-test/boreal-h3/frames/08-3-last.png"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-sseof",
                "-0.1",
                "-i",
                str(OUT / "frames/08-3.mp4"),
                "-frames:v",
                "1",
                str(MEDIA / last),
            ],
            check=True,
        )
        passed = ask(last, VIDEO_QUESTIONS, f"{USAGE} Promised result: {RESULT}")
        print("VIDEO PASS" if passed else "VIDEO FAIL")
        return
    picture = edit_picture(
        job=None, purpose="make_starting_picture", prompt=PROMPT, pictures=[EARLIER]
    )
    print("picture:", picture)
    passed = ask(picture, PICTURE_QUESTIONS, USAGE)
    print("PASS" if passed else "FAIL")
    record.write_text(
        json.dumps(
            {
                "usage": USAGE,
                "result": RESULT,
                "prompt": PROMPT,
                "picture": picture,
                "passed": passed,
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
