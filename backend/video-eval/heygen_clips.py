"""Throwaway evaluation: make the fixed test set's talking clips with HeyGen Avatar IV.

    python video-eval/heygen_clips.py --arm heygen --runs 2 [--scenes T1,T2]
        [--expressiveness low] [--motion-prompt "<text>"] [--dry-run]

Every real run costs money: HeyGen charges once it has the request. Use --dry-run to see
what would be sent and roughly what it would cost before asking for the real thing.

HeyGen values come straight from the environment (the app's settings no longer have them):
HEYGEN_API_KEY (required for a real run), HEYGEN_BASE_URL (default https://api.heygen.com)
and HEYGEN_TIMEOUT_SECONDS (default 120). The HeyGen code is the app's old adapter
(git show 65b2a95:backend/gateway/heygen_adapter.py) made standalone.
"""

import argparse
import io
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import PIL.Image

APP_ROOT = Path("/app")
MEDIA_ROOT = APP_ROOT / "media"
OUT_ROOT = MEDIA_ROOT / "video-eval"
RUNS_LOG = OUT_ROOT / "runs.jsonl"
TEST_SET = Path(__file__).resolve().parent / "set.json"

# From docs/video-model-tests.md: HeyGen does not publish a clear per-second price; the
# wallet went from $5.00 to $4.80 for a 5.72 s clip, so about $0.035 a second. An estimate.
ESTIMATED_USD_PER_SECOND = 0.035

POLL_EVERY_SECONDS = 5
POLL_FOR_AT_MOST_SECONDS = 20 * 60

ASPECT_RATIO = "9:16"
RESOLUTION = "1080p"


# ----------------------------------------------------------------------------------------
# HeyGen, standalone (the old adapter without Django or the gateway's retry types)
# ----------------------------------------------------------------------------------------


class HeyGenError(Exception):
    pass


class HeyGen:
    """Talks to HeyGen's HTTP API: upload the picture and audio, ask for the clip, ask how
    it is going, fetch it when done."""

    def __init__(self, api_key: str, base_url: str, timeout_seconds: float) -> None:
        self._client = httpx.Client(
            base_url=base_url, headers={"x-api-key": api_key}, timeout=timeout_seconds
        )

    def upload(self, name: str, data: bytes, content_type: str = "") -> str:
        content_type = content_type or f"image/{picture_extension(data)}"
        uploaded = self._send("POST", "/v3/assets", files={"file": (name, data, content_type)})
        return str(uploaded["data"]["asset_id"])

    def submit(self, request: dict[str, Any]) -> str:
        """Asks for the clip. Charged for once HeyGen has it, so never retried here."""
        created = self._send("POST", "/v3/videos", json=request)
        return str(created["data"]["video_id"])

    def status(self, video_id: str) -> dict[str, Any]:
        """HeyGen's own record of the clip: status, video_url, duration, failure_*."""
        try:
            return dict(self._send("GET", f"/v3/videos/{video_id}")["data"])
        except httpx.HTTPStatusError as error:
            code = error_of(error.response).get("code")
            # The old adapter looked for "video_not_found"; the docs now say "not_found".
            if code in ("video_not_found", "not_found"):
                why = error_of(error.response).get("message")
                return {
                    "status": "failed",
                    "failure_message": f"HeyGen no longer knows of this clip: {why}",
                }
            raise

    def download(self, url: str) -> bytes:
        # A presigned link somewhere other than HeyGen's API, so no API key is sent.
        response = httpx.get(url, timeout=300, follow_redirects=True)
        response.raise_for_status()
        return response.content

    def _send(self, method: str, path: str, **sending: Any) -> dict[str, Any]:
        response = self._client.request(method, path, **sending)
        if response.status_code == 429 or response.status_code >= 500:
            raise HeyGenError(f"HeyGen answered {response.status_code}: {response.text[:300]}")
        response.raise_for_status()
        reply: dict[str, Any] = response.json()
        return reply


def error_of(response: httpx.Response) -> dict[str, Any]:
    """What HeyGen's reply says went wrong, or nothing if it isn't HeyGen's own reply."""
    try:
        said = response.json()
    except ValueError:
        return {}
    error = said.get("error") if isinstance(said, dict) else None
    return error if isinstance(error, dict) else {}


def picture_extension(picture: bytes) -> str:
    """png or jpeg: what the picture's bytes hold, which HeyGen is told."""
    with PIL.Image.open(io.BytesIO(picture)) as opened:
        return (opened.format or "png").lower()


# ----------------------------------------------------------------------------------------
# The evaluation
# ----------------------------------------------------------------------------------------


def build_request(
    image_id: str, audio_id: str, motion_prompt: str, expressiveness: str, title: str
) -> dict[str, Any]:
    """The same shape the app's old adapter sent, with expressiveness now a choice."""
    return {
        "type": "image",
        "image": {"type": "asset_id", "asset_id": image_id},
        "audio_asset_id": audio_id,
        "motion_prompt": motion_prompt,
        "expressiveness": expressiveness,
        "aspect_ratio": ASPECT_RATIO,
        "resolution": RESOLUTION,
        "title": title,
    }


def default_motion_prompt() -> str:
    """The app's own talking-scene prompt, with no product size (so the plain pose)."""
    if str(APP_ROOT) not in sys.path:
        sys.path.insert(0, str(APP_ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
    import django

    django.setup()
    from jobs.scenes import talking_motion_prompt

    return talking_motion_prompt("")


def load_talking_scenes(wanted: list[str] | None) -> list[dict[str, Any]]:
    scenes = [s for s in json.loads(TEST_SET.read_text())["scenes"] if s["kind"] == "talking"]
    if wanted:
        unknown = set(wanted) - {s["id"] for s in scenes}
        if unknown:
            sys.exit(f"Not talking scenes in {TEST_SET.name}: {', '.join(sorted(unknown))}")
        scenes = [s for s in scenes if s["id"] in wanted]
    return scenes


def probe(path: Path) -> dict[str, Any]:
    """What ffprobe says the clip is: seconds, frames, fps, width x height."""
    said = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=width,height,r_frame_rate,nb_read_frames:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    info = json.loads(said)
    stream = info["streams"][0]
    num, den = stream["r_frame_rate"].split("/")
    fps = float(num) / float(den) if float(den) else 0.0
    return {
        "got_seconds": round(float(info["format"]["duration"]), 3),
        "got_frames": int(stream["nb_read_frames"]),
        "fps": round(fps, 3),
        "resolution": f"{stream['width']}x{stream['height']}",
    }


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def log_run(record: dict[str, Any]) -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    with RUNS_LOG.open("a") as log:
        log.write(json.dumps(record) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--arm", required=True, help="name of this arm, e.g. heygen; also the output folder"
    )
    parser.add_argument("--runs", type=int, default=2, help="how many clips per scene")
    parser.add_argument(
        "--scenes", help="comma-separated scene ids, e.g. T1,T2 (default: all talking scenes)"
    )
    parser.add_argument("--expressiveness", default="low", choices=["low", "medium", "high"])
    parser.add_argument("--motion-prompt", help="default: the app's talking_motion_prompt('')")
    parser.add_argument(
        "--dry-run", action="store_true", help="print what would be sent; contact nothing"
    )
    args = parser.parse_args()

    wanted = [s.strip() for s in args.scenes.split(",")] if args.scenes else None
    scenes = load_talking_scenes(wanted)
    motion_prompt = (
        args.motion_prompt if args.motion_prompt is not None else default_motion_prompt()
    )
    out_dir = OUT_ROOT / args.arm

    # Which clips are still to be made: existing files are never made (or paid for) again.
    todo: list[tuple[dict[str, Any], int, Path]] = []
    for scene in scenes:
        for run in range(1, args.runs + 1):
            target = out_dir / f"{scene['id']}_run{run}.mp4"
            if target.exists():
                print(f"skip  {target} already exists")
            else:
                todo.append((scene, run, target))

    asked_total = sum(scene["audio_seconds"] for scene, _, _ in todo)
    print(
        f"\n{len(todo)} clip(s) to make for arm '{args.arm}' at {ASPECT_RATIO} {RESOLUTION}, "
        f"expressiveness={args.expressiveness}"
    )
    print(f"motion_prompt: {motion_prompt!r}")
    print(
        f"asked seconds in total: {asked_total:.2f}  "
        "(HeyGen decides the length itself: the audio's length is what we ask for)"
    )
    print(
        f"estimated cost: ${asked_total * ESTIMATED_USD_PER_SECOND:.2f}  "
        f"(at ${ESTIMATED_USD_PER_SECOND}/s, docs/video-model-tests.md's measured estimate)\n"
    )

    if args.dry_run:
        for scene, run, target in todo:
            request = build_request(
                "<image asset id>",
                "<audio asset id>",
                motion_prompt,
                args.expressiveness,
                f"video-eval {args.arm} {scene['id']} run{run}",
            )
            print(f"--- {scene['id']} run{run} -> {target}")
            print(f"    upload picture: {MEDIA_ROOT / scene['picture']}")
            print(
                f"    upload audio:   {MEDIA_ROOT / scene['audio']}  ({scene['audio_seconds']} s)"
            )
            print(f"    POST /v3/videos {json.dumps(request)}")
            print(f"    est. ${scene['audio_seconds'] * ESTIMATED_USD_PER_SECOND:.3f}")
        print("\ndry run: nothing was sent to HeyGen.")
        return

    if not todo:
        print("nothing to do.")
        return

    api_key = os.environ.get("HEYGEN_API_KEY", "")
    if not api_key:
        sys.exit(
            "HEYGEN_API_KEY is not set in this process's environment. It is in the repo's .env, "
            "but the running backend container may need recreating to see it "
            "(docker compose up -d --force-recreate backend). Nothing was sent."
        )
    heygen = HeyGen(
        api_key=api_key,
        base_url=os.environ.get("HEYGEN_BASE_URL", "https://api.heygen.com"),
        timeout_seconds=float(os.environ.get("HEYGEN_TIMEOUT_SECONDS", "120")),
    )

    # 1. Submit everything first, uploading each scene's picture and audio once.
    uploaded: dict[str, tuple[str, str]] = {}
    pending: list[dict[str, Any]] = []
    for scene, run, target in todo:
        if scene["id"] not in uploaded:
            picture = (MEDIA_ROOT / scene["picture"]).read_bytes()
            audio = (MEDIA_ROOT / scene["audio"]).read_bytes()
            image_id = heygen.upload(f"starting_picture.{picture_extension(picture)}", picture)
            audio_id = heygen.upload("line.wav", audio, "audio/wav")
            uploaded[scene["id"]] = (image_id, audio_id)
            print(f"uploaded {scene['id']}: image {image_id}, audio {audio_id}")
        image_id, audio_id = uploaded[scene["id"]]
        request = build_request(
            image_id,
            audio_id,
            motion_prompt,
            args.expressiveness,
            f"video-eval {args.arm} {scene['id']} run{run}",
        )
        started = time.monotonic()
        video_id = heygen.submit(request)
        print(f"submitted {scene['id']} run{run}: video {video_id}")
        pending.append(
            {
                "scene": scene,
                "run": run,
                "target": target,
                "video_id": video_id,
                "request": request,
                "started": started,
                "submitted_at": now(),
            }
        )

    # 2. Poll until every clip is done, failed, or we have waited long enough.
    deadline = time.monotonic() + POLL_FOR_AT_MOST_SECONDS
    done: list[dict[str, Any]] = []
    while pending and time.monotonic() < deadline:
        time.sleep(POLL_EVERY_SECONDS)
        still: list[dict[str, Any]] = []
        for clip in pending:
            said = heygen.status(clip["video_id"])
            state = said.get("status")
            if state == "completed":
                clip["seconds_to_make"] = round(time.monotonic() - clip["started"], 1)
                clip["video_url"] = said.get("video_url")
                clip["heygen_duration"] = said.get("duration")
                clip["status"] = "completed"
                done.append(clip)
                print(
                    f"done  {clip['scene']['id']} run{clip['run']} in {clip['seconds_to_make']} s"
                )
            elif state == "failed":
                clip["seconds_to_make"] = round(time.monotonic() - clip["started"], 1)
                clip["status"] = "failed"
                clip["error"] = str(
                    said.get("failure_message")
                    or said.get("failure_code")
                    or said.get("error")
                    or "HeyGen gave no reason"
                )
                done.append(clip)
                print(f"FAILED {clip['scene']['id']} run{clip['run']}: {clip['error']}")
            else:
                still.append(clip)
        pending = still
    for clip in pending:
        clip["status"] = "timed_out"
        clip["seconds_to_make"] = round(time.monotonic() - clip["started"], 1)
        clip["error"] = (
            f"still not done after {POLL_FOR_AT_MOST_SECONDS} s "
            f"(video_id {clip['video_id']} may finish later)"
        )
        done.append(clip)
        print(f"TIMED OUT {clip['scene']['id']} run{clip['run']} (video {clip['video_id']})")

    # 3. Download what was made, measure it, and write one line per clip.
    out_dir.mkdir(parents=True, exist_ok=True)
    total_got = 0.0
    for clip in done:
        scene = clip["scene"]
        measured: dict[str, Any] = {
            "got_seconds": None,
            "got_frames": None,
            "fps": None,
            "resolution": None,
        }
        if clip["status"] == "completed":
            file: Path = clip["target"]
            if file.exists():
                print(f"skip  {file} already exists")
            else:
                file.write_bytes(heygen.download(clip["video_url"]))
                print(f"saved {file}")
            measured = probe(file)
            total_got += measured["got_seconds"]
        got = measured["got_seconds"]
        record = {
            "arm": args.arm,
            "scene": scene["id"],
            "kind": "talking",
            "run": clip["run"],
            "video_id": clip["video_id"],
            "asked_seconds": scene["audio_seconds"],
            "asked_seconds_note": "the audio's length; HeyGen decides the clip's length itself",
            **measured,
            "heygen_duration": clip.get("heygen_duration"),
            "prompt": clip["request"]["motion_prompt"],
            "expressiveness": clip["request"]["expressiveness"],
            "aspect_ratio": clip["request"]["aspect_ratio"],
            "asked_resolution": clip["request"]["resolution"],
            "seconds_to_make": clip["seconds_to_make"],
            "cost_usd": round(got * ESTIMATED_USD_PER_SECOND, 4) if got else None,
            "cost_usd_note": (
                f"estimate: {ESTIMATED_USD_PER_SECOND}/s measured in docs/video-model-tests.md"
            ),
            "status": clip["status"],
            "error": clip.get("error"),
            "timestamp": now(),
        }
        log_run(record)
    print(f"\nwrote {len(done)} line(s) to {RUNS_LOG}")
    print(f"got {total_got:.2f} s of video, estimated ${total_got * ESTIMATED_USD_PER_SECOND:.2f}")


if __name__ == "__main__":
    main()
