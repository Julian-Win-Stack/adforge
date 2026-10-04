"""One Boreal-H3 request: a square reference photo with aspect_ratio 9:16, to learn whether the
video's shape can be set when references are sent. Refused costs nothing; accepted, 2 credits.

    docker compose exec backend python broll-test/shape_check.py"""

import json
import subprocess

import httpx
from boreal_h3 import CREATIFY, OUT, Creatify

creatify = Creatify()
folder = OUT / "shape-check"
folder.mkdir(parents=True, exist_ok=True)
body = {
    "model": "boreal-h3",
    "resolution": "768p",
    "duration": 5,
    "aspect_ratio": "9:16",
    "prompt": "The pot sits on a bathroom shelf, the camera slowly moves closer.",
    "reference_image_urls": [creatify.link("broll-test/photos/02/01.jpg")],
}
reply = creatify.http.post(CREATIFY, json=body)
record = {"sent": body, "reply_status": reply.status_code}
if reply.status_code >= 400:
    record["refused"] = reply.text
    print("REFUSED", reply.status_code, reply.text[:500])
else:
    job = creatify.wait(reply.json()["id"])
    record["got"] = job
    print(job["status"], job.get("failed_reason") or "", "credits:", job["credits_used"])
    if job["status"] == "done":
        raw = folder / "clip.mp4"
        raw.write_bytes(httpx.get(job["video_output"], timeout=300, follow_redirects=True).content)
        print(
            subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height",
                    "-of",
                    "csv=p=0",
                    str(raw),
                ],
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-ss",
                "0",
                "-i",
                str(raw),
                "-frames:v",
                "1",
                str(folder / "first.png"),
            ],
            check=True,
        )
(folder / "record.json").write_text(json.dumps(record, indent=1))
