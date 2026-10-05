"""Text-only check of the tuned B-roll instructions: re-run the picture-planning model on
saved handoffs and read the motion prompts it writes now. Costs about $0.016 a call in
tokens, no video. For the eval's six B-roll scenes the photo is pinned to the one the old
run picked, so the new motion prompt fits the starting picture we already have; their
prompts are written to tuned_broll_prompts.json for make_clips.py (since removed, with the
old Boreal)."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
import django  # noqa: E402

django.setup()

from gateway.gateway import call_model  # noqa: E402
from gateway.models import ModelCall  # noqa: E402
from gateway.types import Image  # noqa: E402
from jobs.scenes import (  # noqa: E402
    BrollPictureChoice,
    BrollPictureHandoff,
    pose_for,
    starting_picture_choice_for,
)

sys.path.insert(0, str(Path(__file__).parent))
from tuned_prompts import TUNED_BROLL_PICTURE_INSTRUCTIONS  # noqa: E402

HERE = Path(__file__).parent
SET = json.loads((HERE / "set.json").read_text())
IN_SET = {s["choose_call"]: s["id"] for s in SET["scenes"] if s["kind"] == "broll"}

# Saved calls from other jobs whose old motion prompts asked for mechanics or several
# actions, or worked: a regression check that the new rule holds across products.
OTHERS = [196, 200, 291, 501, 631, 692, 753, 754, 885, 1090, 1175, 1583, 1854, 1855]


def rerun(call: ModelCall, pin_photo: bool) -> BrollPictureChoice:
    # First-run handoffs predate the `pose` field; every job then was posed as handheld.
    output = call.output or {}
    given = {"pose": pose_for(""), **call.handoff}
    if pin_photo:
        pin = f"Make the picture from photo {output['photo']}."
        given["note"] = f"{given['note']} {pin}" if given.get("note") else pin
    handoff = BrollPictureHandoff(**given)
    return call_model(
        job=None,
        purpose="choose_broll_picture",
        instructions=TUNED_BROLL_PICTURE_INSTRUCTIONS,
        handoff=handoff,
        output=starting_picture_choice_for(handoff.colour_photos, BrollPictureChoice),
        images=[Image(label=i["label"], key=i["key"]) for i in call.images],
    )


def main() -> None:
    ids = list(IN_SET) + OTHERS
    if len(sys.argv) > 1:
        ids = [int(x) for x in sys.argv[1].split(",")]
    lines = ["# Tuned B-roll instructions: what the planner writes now\n"]
    tuned: dict[str, str] = {}
    for call_id in ids:
        call = ModelCall.objects.get(id=call_id)
        scene_id = IN_SET.get(call_id)
        choice = rerun(call, pin_photo=scene_id is not None)
        old = call.output or {}
        if scene_id:
            tuned[scene_id] = choice.motion_prompt
        lines += [
            f"## call {call_id}"
            + (f" ({scene_id})" if scene_id else "")
            + f" · job {str(call.job_id)[:8]} · scene {call.handoff['scene']}",
            f"- shows: {call.handoff['shows']}",
            f"- old motion prompt: {old['motion_prompt']}",
            f"- **new motion prompt:** {choice.motion_prompt}",
            f"- new reason: {choice.motion_prompt_reason}",
            f"- photo: old {old['photo']}, new {choice.photo}",
            "",
        ]
        print(f"{call_id}: {choice.motion_prompt}", flush=True)
    (HERE / "tuned_broll_check.md").write_text("\n".join(lines))
    if tuned:
        (HERE / "tuned_broll_prompts.json").write_text(json.dumps(tuned, indent=2))
    print(
        f"\nwrote {HERE / 'tuned_broll_check.md'}",
        f"and {len(tuned)} prompts to tuned_broll_prompts.json" if tuned else "",
    )


if __name__ == "__main__":
    main()
