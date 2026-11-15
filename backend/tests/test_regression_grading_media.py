"""Grading page media and parts, checked without a browser."""

import base64
import hashlib
import io
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import PIL.Image
import pytest

from evals.regression import grading
from evals.regression.grading import build_page, main

from .conftest import picture
from .test_regression_grading import fixture_store


def page_data(path: Path) -> dict[str, Any]:
    match = re.search(r'<script type="application/json" id="data">(.*?)</script>', path.read_text())
    assert match
    data: dict[str, Any] = json.loads(match.group(1).replace("<\\/", "</"))
    return data


def test_a_page_can_hold_part_of_a_run(tmp_path: Path) -> None:
    store = fixture_store(tmp_path)
    output = tmp_path / "part"
    assert main(["new", "--only", "chair", "--output", str(output)], store=store) == 0
    data = page_data(output / "grading.html")
    assert list(data["ads"]) == ["chair"]
    assert not any(key.startswith(("new/mug", "old/mug")) for key in data["media"])
    assert main(["new", "--only", "sofa", "--output", str(output)], store=store) == 1


def bundled(target: Path, data: dict[str, Any], key: str) -> bytes:
    """A picture's bytes, read back from its bundle the way the page does."""
    bundle, start, length, kind = data["media"][key]
    assert kind == "image/webp"
    return base64.b64decode((target / data["packs"][bundle]).read_text()[start : start + length])


def test_pictures_travel_in_a_few_bundles_and_videos_on_their_own(tmp_path: Path) -> None:
    store = fixture_store(tmp_path)
    target = store.run_dir("new") / "grading-page"
    data = page_data(build_page(store, "new"))

    pictures = {k: v for k, v in data["media"].items() if k.endswith(".webp")}
    videos = {k: v for k, v in data["media"].items() if k.endswith(".mp4")}
    assert pictures and videos
    assert all(isinstance(v, list) for v in pictures.values())
    assert all(isinstance(v, str) and v.startswith("up/") for v in videos.values())
    # The old run's and the new run's scene pictures are the same bytes: kept once.
    assert len({tuple(v) for v in pictures.values()}) < len(pictures)
    with PIL.Image.open(io.BytesIO(bundled(target, data, next(iter(pictures))))) as image:
        assert image.format == "WEBP"
    plan = json.loads((target / "uploads.json").read_text())
    singles, batches = plan["single"], plan["batches"]
    assert [Path(name).name for name in singles] == ["p01.txt"]
    assert all(len(batch) <= 25 for batch in batches)
    uploaded = [Path(name).name for name in singles + [n for b in batches for n in b]]
    assert len(uploaded) == len(set(uploaded)) == plan["files"]
    assert sorted(uploaded[1:]) == sorted({Path(v).name for v in videos.values()})
    # Building the page again can relink it without redoing the media.
    assert json.loads((target / "page-data.json").read_text())["media"] == data["media"]


def test_bundles_stay_under_the_host_file_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = fixture_store(tmp_path)
    chair = store.export_dir("new", "chair") / "chair/scene-1/start-picture.png"
    chair.write_bytes(picture(300, 200, (200, 10, 10)))
    target = store.run_dir("new") / "grading-page"
    data = page_data(build_page(store, "new"))
    sizes = {tuple(v)[1:3] for v in data["media"].values() if isinstance(v, list)}
    assert len(data["packs"]) == 1 and len(sizes) >= 3
    limit = max(length for _, length in sizes)
    monkeypatch.setattr(grading, "PACK_CHARS", limit)

    data = page_data(build_page(store, "new"))

    assert len(data["packs"]) >= 2
    assert all(len((target / p).read_text()) <= limit for p in data["packs"])
    for key in (k for k, v in data["media"].items() if isinstance(v, list)):
        PIL.Image.open(io.BytesIO(bundled(target, data, key))).verify()


def test_full_size_pictures_are_encoded_once_and_reused_from_the_cache(tmp_path: Path) -> None:
    store = fixture_store(tmp_path)
    picture_file = store.export_dir("new", "chair") / "chair/scene-1/start-picture.png"
    picture_file.write_bytes(picture(1600, 1200, (90, 130, 110)))
    cache = tmp_path / "cache"
    target = store.run_dir("new") / "grading-page"
    key = "new/chair/scene-1/start-picture.webp"
    data = page_data(build_page(store, "new", cache=cache))
    with PIL.Image.open(io.BytesIO(bundled(target, data, key))) as image:
        assert image.size == (1600, 1200)
    cached = list(cache.iterdir())
    assert cached
    marker = io.BytesIO()
    PIL.Image.new("RGB", (7, 7)).save(marker, "WEBP")
    for file in cached:
        file.write_bytes(marker.getvalue())

    data = page_data(build_page(store, "new", cache=cache))

    assert bundled(target, data, key) == marker.getvalue()


def test_music_gets_a_still_video_track_so_the_host_accepts_it(tmp_path: Path) -> None:
    music = tmp_path / "music.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=1",
            "-c:a",
            "aac",
            str(music),
        ],
        check=True,
    )
    media = grading.Media(tmp_path / "page")
    key = media.keep(music, "new/mug/raw/music.m4a")
    media.finish()

    assert key == "new/mug/raw/music.mp4"
    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0"]
        + [str(tmp_path / "page" / media.urls[key])],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert sorted(streams) == ["audio", "video"]


def test_a_page_video_keeps_its_own_container(tmp_path: Path) -> None:
    clip = tmp_path / "page-video.webm"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=32x32:d=1"]
        + ["-c:v", "libvpx-vp9", str(clip)],
        check=True,
    )
    media = grading.Media(tmp_path / "page")
    key = media.keep(clip, "new/mug/raw/page-video.webm")
    media.finish()

    assert media.urls[key].endswith(".webm")
    assert (tmp_path / "page" / media.urls[key]).read_bytes() == clip.read_bytes()


URL = "https://claude.ai/artifact/" + "a" * 22


def transcript(path: Path, calls: list[tuple[str, dict[str, Any], str]]) -> Path:
    """A session transcript holding Artifact calls and their results."""
    lines = []
    for number, (name, call, result) in enumerate(calls):
        use = {"type": "tool_use", "id": f"toolu_{number}", "name": name, "input": call}
        answer = {
            "type": "tool_result",
            "tool_use_id": f"toolu_{number}",
            "content": [{"type": "text", "text": result}],
        }
        lines.append(json.dumps({"type": "assistant", "message": {"content": [use]}}))
        lines.append(json.dumps({"type": "user", "message": {"content": [answer]}}))
    path.write_text("\n".join(lines) + "\n")
    return path


def test_upload_results_relink_the_page_to_its_hosted_files(tmp_path: Path) -> None:
    store = fixture_store(tmp_path)
    target = tmp_path / "page"
    assert main(["new", "--output", str(target)], store=store) == 0
    plan = json.loads((target / "uploads.json").read_text())
    blob = iter(f"/_blob/{n:032x}" for n in range(1, 1000))
    calls = []
    bundle_ids = {}
    for name in plan["single"]:
        file = target / plan["names"][name]
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        bundle_ids[name] = next(blob)
        text = (
            f'Asset uploaded: "{file.name}" ({file.stat().st_size} bytes, text/plain, '
            f'sha256 {digest}) is now stored in the artifact as "{bundle_ids[name]}"'
        )
        calls.append(("Artifact", {"url": URL, "asset": True, "file_path": name}, text))
        # The same file sent to another page later: that page's id is not this page's.
        other = text.replace(bundle_ids[name], next(blob))
        calls.append(("Artifact", {"url": URL + "x", "asset": True, "file_path": name}, other))
    lines = [
        f'"{name}" ({(target / plan["names"][name]).stat().st_size} bytes, video/mp4) → '
        f'"{next(blob)}"'
        for batch in plan["batches"]
        for name in batch
    ]
    videos = [name for batch in plan["batches"] for name in batch]
    call = {"url": URL, "asset": True, "file_paths": videos[:-1]}
    calls.append(("Artifact", call, "Uploaded:\n" + "\n".join(lines[:-1])))
    # An upload call to something that isn't a page link is someone else's.
    calls.append(("Artifact", {"url": "https://example.com/x", "asset": True}, "Uploaded"))
    transcripts = transcript(tmp_path / "session.jsonl", calls)

    relink = ["new", "--output", str(target), "--url", URL, "--apply-uploads"]
    # A file with no upload result yet is named, and the page is left as it was.
    assert main([*relink, str(transcripts)], store=store) == 1
    assert "/_blob/" not in (target / "grading.html").read_text()

    call = {"url": URL, "asset": True, "file_paths": videos[-1:]}
    calls.append(("Artifact", call, "Uploaded:\n" + lines[-1]))
    transcript(tmp_path / "session.jsonl", calls)
    assert main([*relink, str(tmp_path)], store=store) == 0

    data = page_data(target / "grading.html")
    assert data["packs"] == [bundle_ids[name] for name in plan["single"]]
    hosted = [v for v in data["media"].values() if isinstance(v, str)]
    assert hosted and all(re.fullmatch("/_blob/[0-9a-f]{32}", v) for v in hosted)
    assert {v[0] for v in data["media"].values() if isinstance(v, list)} <= set(
        range(len(data["packs"]))
    )
