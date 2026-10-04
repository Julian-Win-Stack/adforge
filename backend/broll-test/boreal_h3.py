"""B-roll test on Creatify's Boreal-H3, through Creatify's own API (it isn't on fal).

Boreal-H3 takes either a start picture with an optional end picture ("frames"), or up to 9
reference pictures ("refs"), never both in one request (docs.creatify.ai, Create a Boreal
task). It renders 5–15 whole seconds at 0.4 credits a second at 768p; the first 5 reference
pictures are free, each after that 0.2 credits. Pictures are sent as links, so each is first
put on fal's storage.

    docker compose exec backend python broll-test/boreal_h3.py --check     # 4 calls, ~4-8 credits
    docker compose exec backend python broll-test/boreal_h3.py frames --dry-run
    docker compose exec backend python broll-test/boreal_h3.py frames      # pays Creatify

Needs CREATIFY_API_ID and CREATIFY_API_KEY in .env (then `docker compose up -d backend`).
Only the scenes the user picked on 2026-10-01 are made. A mode's prompts and pictures come
from MEDIA_ROOT/broll-test/boreal-h3/<mode>.json, written once their rules are settled:
{scene: {"prompt": ..., "image": key, "end_image": key} or {"prompt": ..., "references": [keys]}}.
Clips go to MEDIA_ROOT/broll-test/boreal-h3/<mode>/<scene>.mp4 (raw) and <scene>_cut.mp4."""

import argparse
import json
import math
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

MEDIA = Path("/app/media")
OUT = MEDIA / "broll-test" / "boreal-h3"
CREATIFY = "https://api.creatify.ai/api/boreal/"
FAL_STORAGE = "https://rest.alpha.fal.ai/storage/upload/initiate?storage_type=fal-cdn-v3"
TESTED = ["02-2", "03-2", "05-3", "06-5", "07-3", "07-4", "08-2", "08-3"]
CREDITS_PER_SECOND = 0.4  # 768p
DOLLARS_PER_CREDIT = 99 / 500  # API Starter, 2026-10-03


def seconds_for(line_seconds: float) -> int:
    """The shortest length Boreal-H3 makes that covers the line: whole seconds, 5 at least."""
    return max(5, math.ceil(line_seconds - 1e-6))


def credits_for(seconds: int, references: int = 0) -> float:
    return seconds * CREDITS_PER_SECOND + max(0, references - 5) * 0.2


class Creatify:
    def __init__(self) -> None:
        self.http = httpx.Client(
            timeout=120,
            headers={
                "X-API-ID": os.environ["CREATIFY_API_ID"],
                "X-API-KEY": os.environ["CREATIFY_API_KEY"],
            },
        )
        self.fal = httpx.Client(
            timeout=120, headers={"Authorization": f"Key {os.environ['FAL_KEY']}"}
        )
        self.links: dict[str, str] = {}

    def link(self, key: str) -> str:
        """A public link to the media file `key`, put on fal's storage once."""
        if key not in self.links:
            data = (MEDIA / key).read_bytes()
            kind = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
            slot = self.fal.post(
                FAL_STORAGE, json={"content_type": kind, "file_name": Path(key).name}
            )
            slot.raise_for_status()
            self.fal.put(
                slot.json()["upload_url"], content=data, headers={"Content-Type": kind}
            ).raise_for_status()
            self.links[key] = slot.json()["file_url"]
        return self.links[key]

    def body(self, ask: dict[str, Any], seconds: int) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": "boreal-h3",
            "prompt": ask["prompt"],
            "resolution": "768p",
            "duration": seconds,
        }
        if "references" in ask:
            # Any picture shape works; the video's shape is set here (shape check, 2026-10-03).
            body["aspect_ratio"] = "9:16"
            body["reference_image_urls"] = [self.link(k) for k in ask["references"]]
        else:
            body["image_url"] = self.link(ask["image"])
            if ask.get("end_image"):
                body["end_image_url"] = self.link(ask["end_image"])
        return body

    def send(self, body: dict[str, Any]) -> dict[str, Any]:
        reply = self.http.post(CREATIFY, json=body)
        if reply.status_code >= 400:
            raise SystemExit(f"Creatify refused it ({reply.status_code}): {reply.text[:500]}")
        answer: dict[str, Any] = reply.json()
        return answer

    def wait(self, job_id: str) -> dict[str, Any]:
        while True:
            job: dict[str, Any] = self.http.get(f"{CREATIFY}{job_id}/").json()
            if job["status"] in ("done", "failed", "rejected"):
                return job
            time.sleep(10)


def cut(raw: Path, audio: Path, seconds: float, out: Path) -> None:
    """Lay the line's audio over a clip. The clip plays to its end, past the line, so its
    action's result isn't cut off (decided 2026-10-03); `seconds` is no longer used."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(raw),
            "-i",
            str(audio),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-af",
            "apad",
            "-shortest",
            "-vf",
            "scale=720:-2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(out),
        ],
        check=True,
    )


def check(creatify: Creatify) -> None:
    """Four 5 s calls on scene #16's pictures from check_pictures.py, to learn which kinds of
    request Creatify takes and whether it uses every picture sent: start and end, references
    only, and the two the docs say it refuses, start and end with references, and start with
    references. A clip's first and last frames are kept beside it, to set against the pictures."""
    folder = OUT / "check"
    pictures = json.loads((folder / "pictures.json").read_text())
    start, end, product = pictures["start"], pictures["end"], pictures["product_photo"]
    motion = pictures["motion_prompt"]
    refs_prompt = (
        f"Image 1 is the toilet bowl cleaner bottle: keep its shape and label exactly. {motion}"
    )
    tries: dict[str, dict[str, Any]] = {
        "1-start-end": {"image_url": start, "end_image_url": end, "prompt": motion},
        "2-refs": {"reference_image_urls": [product], "prompt": refs_prompt},
        "3-start-end-refs": {
            "image_url": start,
            "end_image_url": end,
            "reference_image_urls": [product],
            "prompt": refs_prompt,
        },
        "4-start-refs": {
            "image_url": start,
            "reference_image_urls": [product],
            "prompt": refs_prompt,
        },
    }
    for name, ask in tries.items():
        body: dict[str, Any] = {"model": "boreal-h3", "resolution": "768p", "duration": 5}
        for field, value in ask.items():
            if field == "prompt":
                body[field] = value
            elif field == "reference_image_urls":
                body[field] = [creatify.link(k) for k in value]
            else:
                body[field] = creatify.link(value)
        reply = creatify.http.post(CREATIFY, json=body)
        record: dict[str, Any] = {"sent": body, "sent_keys": ask, "reply_status": reply.status_code}
        if reply.status_code >= 400:
            record["refused"] = reply.text
            print(name, "REFUSED", reply.status_code, reply.text[:300])
        else:
            job = creatify.wait(reply.json()["id"])
            record["got"] = job
            print(
                name, job["status"], job.get("failed_reason") or "", "credits:", job["credits_used"]
            )
            if job["status"] == "done":
                raw = folder / f"{name}.mp4"
                raw.write_bytes(
                    httpx.get(job["video_output"], timeout=300, follow_redirects=True).content
                )
                for which, where in (("first", ["-ss", "0"]), ("last", ["-sseof", "-0.1"])):
                    subprocess.run(
                        [
                            "ffmpeg",
                            "-y",
                            "-loglevel",
                            "error",
                            *where,
                            "-i",
                            str(raw),
                            "-frames:v",
                            "1",
                            str(folder / f"{name}-{which}.png"),
                        ],
                        check=True,
                    )
        (folder / f"{name}.json").write_text(json.dumps(record, indent=1))


def run(creatify: Creatify | None, mode: str, scenes: dict[str, dict[str, Any]], dry: bool) -> None:
    asks = json.loads((OUT / f"{mode}.json").read_text())
    folder = OUT / mode
    folder.mkdir(parents=True, exist_ok=True)
    total = 0.0
    sent: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for scene_id in TESTED:
        if scene_id not in asks:
            print(scene_id, "no prompt yet, skipped")
            continue
        s, ask = scenes[scene_id], asks[scene_id]
        n = seconds_for(s["line_seconds"])
        credits = credits_for(n, len(ask.get("references", [])))
        total += credits
        print(f"{scene_id} {n}s {credits:.1f} credits | {ask['prompt'][:80]}")
        if dry or (folder / f"{scene_id}.mp4").exists():
            continue
        assert creatify is not None
        body = creatify.body(ask, n)
        sent[scene_id] = (body, creatify.send(body))
    # All are sent first, then waited on, so they are made at the same time.
    for scene_id, (body, reply) in sent.items():
        assert creatify is not None
        job = creatify.wait(reply["id"])
        (folder / f"{scene_id}.json").write_text(json.dumps({"sent": body, "got": job}, indent=1))
        if job["status"] != "done":
            print(scene_id, "FAILED", job["status"], job.get("failed_reason"))
            continue
        raw = folder / f"{scene_id}.mp4"
        raw.write_bytes(httpx.get(job["video_output"], timeout=300, follow_redirects=True).content)
        s = scenes[scene_id]
        cut(raw, MEDIA / s["audio"], s["line_seconds"], folder / f"{scene_id}_cut.mp4")
        print(scene_id, "done,", job["credits_used"], "credits")
    print(f"total {total:.1f} credits, about ${total * DOLLARS_PER_CREDIT:.2f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", nargs="?", choices=["frames", "refs"])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    scenes = {s["id"]: s for s in json.loads((MEDIA / "broll-test/scenes.json").read_text())}
    if args.check:
        check(Creatify())
    elif args.mode:
        run(None if args.dry_run else Creatify(), args.mode, scenes, args.dry_run)
    else:
        parser.error("give a mode or --check")


if __name__ == "__main__":
    main()
