"""Makes the clips of one arm of the video eval: every scene in set.json, `--runs` times
each, asked of Boreal on fal the way the app asks, but straight through BorealProvider's
pieces so nothing touches a real job. Each clip is one line in runs.jsonl.

    python video-eval/make_clips.py --arm baseline --runs 2 [--scenes B1,T2] [--kinds broll]
    python video-eval/make_clips.py --arm baseline --runs 2 --dry-run   # prints, pays nothing

Every real run pays fal. A clip whose file is already there is skipped, and a clip whose
request was sent but not yet fetched (a `.request` file) is fetched, not asked for again."""

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")

import django  # noqa: E402

django.setup()

import httpx  # noqa: E402
from django.conf import settings  # noqa: E402

from adforge.retry import OutsideServiceDown  # noqa: E402
from gateway.boreal_adapter import (  # noqa: E402
    MODEL,
    BorealProvider,
    _data_uri,
    _extension,
    _prompt,
)
from gateway.types import LEAST_CLIP_SECONDS, ClipFailed  # noqa: E402
from jobs.scenes import talking_motion_prompt, with_nothing_made_up  # noqa: E402
from jobs.work import _silent_clip_seconds  # noqa: E402

OUT_ROOT = Path(settings.MEDIA_ROOT) / "video-eval"
RUNS_FILE = OUT_ROOT / "runs.jsonl"
COST_PER_SECOND = {"720p": 0.01, "1080p": 0.03}
POLL_SECONDS = 5
MOST_WAIT_SECONDS = 15 * 60
SPEECH_LINE = "[SPEECH]\nOnly the given audio, unchanged."


@dataclass
class Ask:
    """One clip to make: everything sent, and where it goes."""

    arm: str
    scene: str
    kind: str
    run: int
    prompt: str
    negative_prompt: str
    seconds: float
    resolution: str
    picture_key: str
    audio_key: str | None  # None for B-roll: no audio is sent

    @property
    def file(self) -> Path:
        return OUT_ROOT / self.arm / f"{self.scene}_run{self.run}.mp4"

    @property
    def request_file(self) -> Path:
        return self.file.with_suffix(".request")

    @property
    def cost_usd(self) -> float:
        return round(self.seconds * COST_PER_SECOND[self.resolution], 4)


@dataclass
class Sent:
    ask: Ask
    request_id: str
    sent_at: float
    video_url: str | None = None
    error: str | None = None
    seconds_to_make: float | None = None


def main() -> None:
    args = parse_args()
    arm = load_arm(args.arm)
    scenes = pick_scenes(load_set(), args.scenes, args.kinds)
    asks = [ask_for(arm, scene, run) for scene in scenes for run in range(1, args.runs + 1)]
    if args.dry_run:
        print_dry_run(asks)
        return
    if not settings.FAL_KEY:
        sys.exit("FAL_KEY is not set")
    todo = [ask for ask in asks if not skip_if_made(ask)]
    (OUT_ROOT / arm["name"]).mkdir(parents=True, exist_ok=True)
    provider = BorealProvider()
    sent = submit_all(provider, todo)
    poll_all(provider, sent)
    rows = [download_and_record(provider, one) for one in sent]
    print_table(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--arm", required=True, help="an arm's name in arms.json")
    parser.add_argument("--runs", type=int, default=2, help="clips per scene")
    parser.add_argument("--scenes", help="comma-separated scene ids; default all")
    parser.add_argument("--kinds", help="comma-separated: broll,talking; default both")
    parser.add_argument("--dry-run", action="store_true", help="print what would be sent")
    return parser.parse_args()


def load_set() -> list[dict[str, Any]]:
    scenes: list[dict[str, Any]] = json.loads((HERE / "set.json").read_text())["scenes"]
    return scenes


def load_arm(name: str) -> dict[str, Any]:
    arms: list[dict[str, Any]] = json.loads((HERE / "arms.json").read_text())["arms"]
    for arm in arms:
        if arm["name"] == name:
            if arm["resolution"] not in COST_PER_SECOND:
                sys.exit(f"arm {name}: resolution must be one of {list(COST_PER_SECOND)}")
            return arm
    sys.exit(f"no arm named {name!r} in arms.json; have {[arm['name'] for arm in arms]}")


def pick_scenes(
    scenes: list[dict[str, Any]], ids: str | None, kinds: str | None
) -> list[dict[str, Any]]:
    wanted_ids = set(ids.split(",")) if ids else None
    wanted_kinds = set(kinds.split(",")) if kinds else {"broll", "talking"}
    picked = [
        scene
        for scene in scenes
        if (wanted_ids is None or scene["id"] in wanted_ids) and scene["kind"] in wanted_kinds
    ]
    if wanted_ids and (missing := wanted_ids - {scene["id"] for scene in picked}):
        sys.exit(f"not in set.json (or not of the asked kinds): {sorted(missing)}")
    return picked


def ask_for(arm: dict[str, Any], scene: dict[str, Any], run: int) -> Ask:
    talking = scene["kind"] == "talking"
    if talking:
        seconds = max(scene["audio_seconds"], LEAST_CLIP_SECONDS)
        prompt = talking_prompt(arm)
    else:
        seconds = _silent_clip_seconds(scene["audio_seconds"])
        prompt = _prompt(with_nothing_made_up(broll_motion(arm, scene)), speaks=False)
    return Ask(
        arm=arm["name"],
        scene=scene["id"],
        kind=scene["kind"],
        run=run,
        prompt=prompt,
        negative_prompt=arm.get("negative_prompt") or "",
        seconds=seconds,
        resolution=arm["resolution"],
        picture_key=scene["picture"],
        audio_key=scene["audio"] if talking else None,
    )


def talking_prompt(arm: dict[str, Any]) -> str:
    visual = arm.get("talking_visual") or talking_motion_prompt("")
    prompt = _prompt(visual, speaks=True)
    note = arm.get("speech_note")
    if note:
        # The adapter's SPEECH line, with the note after it; the rest of the sections
        # stay exactly as the adapter writes them.
        assert SPEECH_LINE in prompt, "the adapter's SPEECH line changed; update SPEECH_LINE"
        prompt = prompt.replace(SPEECH_LINE, f"{SPEECH_LINE[:-1]}. {note}", 1)
    return prompt


def broll_motion(arm: dict[str, Any], scene: dict[str, Any]) -> str:
    path = arm.get("broll_prompts")
    if not path:
        return str(scene["motion_prompt"])
    prompts: dict[str, str] = json.loads((HERE / path).read_text())
    if scene["id"] not in prompts:
        sys.exit(f"{path} has no prompt for scene {scene['id']}")
    return prompts[scene["id"]]


def print_dry_run(asks: list[Ask]) -> None:
    for ask in asks:
        print(f"=== {ask.scene} run {ask.run} ({ask.kind}) -> {ask.file}")
        print(
            f"seconds: {ask.seconds}  resolution: {ask.resolution}  "
            f"audio sent: {'yes, ' + ask.audio_key if ask.audio_key else 'no'}  "
            f"cost: ${ask.cost_usd:.4f}"
        )
        print(f"negative_prompt: {ask.negative_prompt!r}")
        print("prompt:")
        print(ask.prompt)
        print()
    total = sum(ask.cost_usd for ask in asks)
    print(f"{len(asks)} clips, estimated total ${total:.2f} (dry run: nothing sent)")


def skip_if_made(ask: Ask) -> bool:
    if ask.file.exists():
        print(f"skip {ask.scene} run {ask.run}: {ask.file} already exists")
        return True
    return False


# --- asking fal -------------------------------------------------------------------------


def submit_all(provider: BorealProvider, asks: list[Ask]) -> list[Sent]:
    """Ask fal for every clip, in order. Stops asking at the first failure but keeps what
    was already asked for, so those get polled and fetched rather than paid for twice."""
    sent: list[Sent] = []
    audio_urls: dict[str, str] = {}
    for ask in asks:
        if (resumed := resume(ask)) is not None:
            sent.append(resumed)
            continue
        try:
            if ask.audio_key and ask.audio_key not in audio_urls:
                audio_urls[ask.audio_key] = provider._store_audio(read_media(ask.audio_key))
            audio_url = audio_urls.get(ask.audio_key) if ask.audio_key else None
            request_id = submit(provider, ask, audio_url)
        except ClipFailed as error:
            # _send raises this only when a paid POST's reply was lost.
            print("\n" + "!" * 78)
            print(f"LOST REPLY for {ask.scene} run {ask.run}: {error}")
            print("It may have been charged. NOT retried; no more clips are asked for.")
            print("!" * 78 + "\n")
            break
        except (OutsideServiceDown, httpx.HTTPStatusError) as error:
            print(f"could not ask for {ask.scene} run {ask.run}: {error}; stopping submits")
            break
        ask.request_file.write_text(request_id)
        sent.append(Sent(ask=ask, request_id=request_id, sent_at=time.monotonic()))
        print(f"asked for {ask.scene} run {ask.run}: request {request_id}")
    return sent


def resume(ask: Ask) -> Sent | None:
    """A clip asked for by an earlier run that crashed before fetching it."""
    if not ask.request_file.exists():
        return None
    request_id = ask.request_file.read_text().strip()
    print(f"resume {ask.scene} run {ask.run}: request {request_id} was already sent")
    return Sent(ask=ask, request_id=request_id, sent_at=time.monotonic())


def submit(provider: BorealProvider, ask: Ask, audio_url: str | None) -> str:
    """Exactly BorealProvider.submit's body, plus the arm's resolution and negative prompt."""
    picture = read_media(ask.picture_key)
    body = {
        "prompt": ask.prompt,
        "image_url": _data_uri(picture, f"image/{_extension(picture)}"),
        "duration": ask.seconds,
        "resolution": ask.resolution,
        "aspect_ratio": "9:16",
    }
    if audio_url is not None:
        body["audio_url"] = audio_url
    if ask.negative_prompt:
        body["negative_prompt"] = ask.negative_prompt
    created = provider._send("POST", f"/{MODEL}", paid=True, json=body)
    return str(created["request_id"])


def poll_all(provider: BorealProvider, sent: list[Sent]) -> None:
    waiting = list(sent)
    while waiting:
        for one in list(waiting):
            try:
                status = provider.status(video_id=one.request_id)
            except (OutsideServiceDown, httpx.HTTPStatusError) as error:
                print(f"status of {one.ask.scene} run {one.ask.run} unknown for now: {error}")
                continue
            if status.state == "working":
                if time.monotonic() - one.sent_at > MOST_WAIT_SECONDS:
                    one.error = f"still not made after {MOST_WAIT_SECONDS} s"
                    waiting.remove(one)
                continue
            one.seconds_to_make = round(time.monotonic() - one.sent_at, 1)
            one.video_url = status.video_url
            one.error = status.error
            waiting.remove(one)
            print(
                f"{one.ask.scene} run {one.ask.run}: {status.state} after {one.seconds_to_make} s"
            )
        if waiting:
            time.sleep(POLL_SECONDS)


def download_and_record(provider: BorealProvider, one: Sent) -> dict[str, Any]:
    ask = one.ask
    measured: dict[str, Any] = {}
    if one.video_url is not None:
        try:
            ask.file.write_bytes(provider.download(url=one.video_url))
            ask.request_file.unlink(missing_ok=True)
            measured = probe(ask.file)
        except (OutsideServiceDown, httpx.HTTPStatusError, subprocess.CalledProcessError) as error:
            one.error = f"made but not fetched: {error}"
    row = {
        "arm": ask.arm,
        "scene": ask.scene,
        "kind": ask.kind,
        "run": ask.run,
        "request_id": one.request_id,
        "file": str(ask.file) if ask.file.exists() else None,
        "asked_seconds": ask.seconds,
        "got_seconds": measured.get("seconds"),
        "got_frames": measured.get("frames"),
        "fps": measured.get("fps"),
        "resolution": measured.get("resolution"),
        "prompt": ask.prompt,
        "negative_prompt": ask.negative_prompt,
        "seconds_to_make": one.seconds_to_make,
        "cost_usd": ask.cost_usd,
        "status": "ok" if one.error is None else f"failed: {one.error}",
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    with RUNS_FILE.open("a") as runs:
        runs.write(json.dumps(row) + "\n")
    return row


# --- measuring ----------------------------------------------------------------------------


def read_media(key: str) -> bytes:
    return (Path(settings.MEDIA_ROOT) / key).read_bytes()


def probe(file: Path) -> dict[str, Any]:
    """Length, frame count, frame rate and size of a clip, by ffprobe."""
    said = json.loads(
        ffprobe(
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "format=duration:stream=nb_read_frames,r_frame_rate,width,height",
            "-of",
            "json",
            str(file),
        )
    )
    stream = said["streams"][0]
    top, bottom = stream["r_frame_rate"].split("/")
    return {
        "seconds": round(float(said["format"]["duration"]), 3),
        "frames": int(stream["nb_read_frames"]),
        "fps": round(int(top) / int(bottom), 3),
        "resolution": f"{stream['width']}x{stream['height']}",
    }


def ffprobe(*args: str) -> str:
    return subprocess.run(
        ["ffprobe", "-v", "error", *args], check=True, capture_output=True, text=True
    ).stdout


def print_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("nothing made")
        return
    print()
    print(
        f"{'scene':6} {'run':3} {'kind':8} {'asked':>6} {'got':>6} {'frames':>6} "
        f"{'fps':>5} {'size':>9} {'made in':>8} {'cost':>6}  status"
    )
    for row in rows:
        print(
            f"{row['scene']:6} {row['run']:<3} {row['kind']:8} {row['asked_seconds']:>6} "
            f"{fmt(row['got_seconds']):>6} {fmt(row['got_frames']):>6} {fmt(row['fps']):>5} "
            f"{fmt(row['resolution']):>9} {fmt(row['seconds_to_make']):>8} "
            f"{row['cost_usd']:>6.2f}  {row['status']}"
        )
    print(f"total cost ${sum(row['cost_usd'] for row in rows):.2f}; records in {RUNS_FILE}")


def fmt(value: object) -> str:
    return "-" if value is None else str(value)


if __name__ == "__main__":
    main()
