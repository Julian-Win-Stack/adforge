"""PROTOTYPE, never merged: stage 2, film each scene's stills-round picture with its video
prompt, 3 takes, on H3 in start-frame mode (no reference pictures, no end frame).

    uv run python -m scene_proto.takes <scenes dir> <round dir> [slug ...] [--fake]

Writes <round dir>/<slug>/take-N.mp4 and takes.json. The clip judge isn't run here: Julian
grades the takes against the old clips."""

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
django.setup()

from django.db import connection  # noqa: E402

from adforge import file_store  # noqa: E402
from agents import scene_agent  # noqa: E402
from gateway import gateway  # noqa: E402
from gateway.models import ModelCall  # noqa: E402
from jobs.work import _broll_clip_seconds  # noqa: E402

TAKES = 3
LOCK = threading.Lock()


def take(slug: str, number: int, picture: str, prompt: str, seconds: int, out: Path) -> None:
    record: dict[str, Any] = {"take": number, "seconds": seconds}
    try:
        video_id = gateway.submit_broll_clip(
            job=None,
            purpose="make_broll_clip",
            starting_picture_key=picture,
            example_picture_keys=[],
            seconds=seconds,
            prompt=prompt,
        )
        record["video_id"] = video_id
        key = gateway.collect_clip(job=None, purpose="collect_broll_clip", video_id=video_id)
        (out / f"take-{number}.mp4").write_bytes(file_store.read(key))
        record["file"] = f"take-{number}.mp4"
        call = ModelCall.objects.filter(output__video_id=video_id).first()
        record["cost_usd"] = str(call.cost_usd) if call is not None else None
        print(f"{slug} take {number}: made")
    except Exception as error:
        record["failed"] = f"{type(error).__name__}: {error}"
        print(f"{slug} take {number}: FAILED {record['failed']}")
    finally:
        connection.close()
    with LOCK:
        path = out / "takes.json"
        kept = json.loads(path.read_text()) if path.exists() else {}
        kept.setdefault("takes", []).append(record)
        kept["takes"].sort(key=lambda each: each["take"])
        path.write_text(json.dumps(kept, indent=2))


def main(argv: list[str]) -> None:
    fake = "--fake" in argv
    args = [each for each in argv if each != "--fake"]
    scenes, round_dir = Path(args[0]), Path(args[1])
    slugs = args[2:] or sorted(p.name for p in round_dir.iterdir() if (p / "run.json").exists())
    if fake:
        _fake()
    threads = []
    for slug in slugs:
        run = json.loads((round_dir / slug / "run.json").read_text())
        if not run["video_prompt"]:
            print(f"{slug}: no video prompt, nothing filmed")
            continue
        inputs = scene_agent.load(scenes / slug)
        picture = run["pictures"][run["video_form"]["picture"] - 1]
        seconds = _broll_clip_seconds(inputs.line_seconds)
        out = round_dir / slug
        (out / "takes.json").write_text(
            json.dumps(
                {"picture": picture["number"], "prompt": run["video_prompt"], "takes": []},
                indent=2,
            )
        )
        for number in range(1, TAKES + 1):
            threads.append(
                threading.Thread(
                    target=take,
                    args=(slug, number, picture["key"], run["video_prompt"], seconds, out),
                )
            )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def _fake() -> None:
    import subprocess
    import tempfile

    path = Path(tempfile.mkdtemp()) / "fake.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=320x568:d=2",
            str(path),
        ],
        check=True,
    )
    key = file_store.save("proto_fake_clip.mp4", path.read_bytes())

    def submit(**told: Any) -> str:
        assert told["example_picture_keys"] == [] and told["starting_picture_key"]
        return "fake-video"

    setattr(gateway, "submit_broll_clip", submit)  # noqa: B010
    setattr(gateway, "collect_clip", lambda **_: key)  # noqa: B010


if __name__ == "__main__":
    main(sys.argv[1:])
