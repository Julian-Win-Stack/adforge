"""Lays out the clips of some arms for blind grading: each clip and its starting picture
copied under a random name, in a shuffled order, with a grading sheet and a page to watch
them on. KEY.json says which clip is which; it is not to be opened until grading is done.

    python video-eval/blind.py --arms baseline,tuned [--out /app/media/video-eval/blind] [--seed 7]
"""

import argparse
import csv
import html
import json
import random
import shutil
import string
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MEDIA = Path("/app/media")
RUNS_FILE = MEDIA / "video-eval" / "runs.jsonl"

RULES = {
    "talking": ["lips_in_step", "normal_speed", "calm_face_fits_line", "no_extra_hands_or_objects"],
    "broll": ["keeps_size_and_shape", "no_extra_hands_limbs_objects", "motion_could_happen"],
}


def main() -> None:
    args = parse_args()
    rng = random.Random(args.seed)
    clips = clips_of(args.arms.split(","))
    pictures = {scene["id"]: scene["picture"] for scene in load_set()}
    rng.shuffle(clips)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    named = [(fresh_name(rng, out), clip) for clip in clips]
    for name, clip in named:
        shutil.copyfile(clip["file"], out / f"{name}.mp4")
        shutil.copyfile(MEDIA / pictures[clip["scene"]], out / f"{name}.png")
    write_key(out, named)
    write_grading_sheet(out, named)
    write_page(out, named)
    write_readme(out)
    print(f"{len(named)} clips laid out in {out}; open {out / 'index.html'} to grade")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--arms", required=True, help="comma-separated arm names")
    parser.add_argument("--out", default=str(MEDIA / "video-eval" / "blind"))
    parser.add_argument("--seed", type=int, default=None, help="fix the names and order")
    return parser.parse_args()


def load_set() -> list[dict[str, Any]]:
    scenes: list[dict[str, Any]] = json.loads((HERE / "set.json").read_text())["scenes"]
    return scenes


def clips_of(arms: list[str]) -> list[dict[str, Any]]:
    """The ok clips of those arms recorded in runs.jsonl, whose files are still there."""
    if not RUNS_FILE.exists():
        sys.exit(f"no {RUNS_FILE}: make some clips first (make_clips.py)")
    rows = [json.loads(line) for line in RUNS_FILE.read_text().splitlines() if line.strip()]
    clips = [row for row in rows if row["arm"] in arms and row["status"] in ("ok", "completed")]
    # The HeyGen script records no file; its clips live where make_clips.py puts its own.
    for row in clips:
        row.setdefault(
            "file", f"/app/media/video-eval/{row['arm']}/{row['scene']}_run{row['run']}.mp4"
        )
    missing = [row["file"] for row in clips if not row["file"] or not Path(row["file"]).exists()]
    if missing:
        sys.exit(f"recorded as ok but the file is gone: {missing}")
    if not clips:
        sys.exit(f"no ok clips of arms {arms} in {RUNS_FILE}")
    found = {row["arm"] for row in clips}
    if unmade := set(arms) - found:
        sys.exit(f"no ok clips of arms {sorted(unmade)} in {RUNS_FILE}")
    return clips


def fresh_name(rng: random.Random, out: Path) -> str:
    while True:
        name = "".join(rng.choices(string.ascii_lowercase, k=8))
        if not (out / f"{name}.mp4").exists():
            return name


def write_key(out: Path, named: list[tuple[str, dict[str, Any]]]) -> None:
    key = {
        name: {"arm": clip["arm"], "scene": clip["scene"], "run": clip["run"], "kind": clip["kind"]}
        for name, clip in named
    }
    (out / "KEY.json").write_text(json.dumps(key, indent=2) + "\n")


def write_grading_sheet(out: Path, named: list[tuple[str, dict[str, Any]]]) -> None:
    columns = ["name", "kind", *RULES["talking"], *RULES["broll"], "notes"]
    with (out / "grading.csv").open("w", newline="") as sheet:
        writer = csv.DictWriter(sheet, fieldnames=columns)
        writer.writeheader()
        for name, clip in named:
            writer.writerow({"name": name, "kind": clip["kind"]})


def write_page(out: Path, named: list[tuple[str, dict[str, Any]]]) -> None:
    cards = "\n".join(
        f'<div class="card"><h3>{name} <small>{html.escape(clip["kind"])}</small></h3>'
        f'<div class="pair"><video src="{name}.mp4" controls loop preload="metadata"></video>'
        f'<img src="{name}.png" alt=""></div></div>'
        for name, clip in named
    )
    (out / "index.html").write_text(f"""<!doctype html>
<meta charset="utf-8">
<title>blind grading</title>
<style>
  body {{ font-family: sans-serif; margin: 1em; }}
  .card {{ margin-bottom: 2em; }}
  .pair {{ display: flex; gap: 1em; }}
  .pair video, .pair img {{ height: 480px; background: #eee; }}
  small {{ font-weight: normal; color: #666; }}
</style>
{cards}
""")


def write_readme(out: Path) -> None:
    (out / "README.txt").write_text(
        "Blind grading set.\n\n"
        "Open index.html in a browser. Each clip is shown next to the picture it was made\n"
        "from, under a random name. Fill grading.csv with pass or fail in each rule column\n"
        "that applies to the clip's kind (talking or broll) and anything else in notes.\n\n"
        "Do NOT open KEY.json until grading is done: it says which arm each clip is from.\n"
    )


if __name__ == "__main__":
    main()
