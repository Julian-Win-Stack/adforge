"""ffmpeg, as installed where the app and its tests run: a build that can draw text and
subtitles on a clip, with a font to draw them in. Assembly and captions need both."""

import json
import subprocess
from pathlib import Path

from django.conf import settings


def run(*args: str) -> str:
    done = subprocess.run(args, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_ffmpeg_draws_text_and_a_subtitle_on_a_clip(tmp_path: Path) -> None:
    subtitles = tmp_path / "line.srt"
    subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\nYours for $24.00.\n")
    clip = tmp_path / "clip.mp4"

    run(
        settings.FFMPEG,
        "-hide_banner",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=320x568:d=1",
        "-vf",
        f"drawtext=text='Kiln & Co':fontcolor=white:fontsize=32,subtitles={subtitles}",
        "-pix_fmt",
        "yuv420p",
        str(clip),
    )

    probed = json.loads(
        run(settings.FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(clip))
    )
    (video,) = probed["streams"]
    assert (video["width"], video["height"]) == (320, 568)
    assert float(video["duration"]) == 1.0
