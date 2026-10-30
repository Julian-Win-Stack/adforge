"""A clip holding the sent-list fixture (tests/fixtures/sent_list.json, #211), with every
picture and piece it names kept in the file store, so a tool showing what the clip was sent
can open them."""

import io
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import PIL.Image
from django.conf import settings

from adforge import file_store
from jobs.library import SentList, keep_sent_list
from jobs.models import ProducedItem

FIXTURE = Path(__file__).parent / "fixtures" / "sent_list.json"


def _picture(shade: int) -> bytes:
    made = io.BytesIO()
    PIL.Image.new("RGB", (30, 40), (shade, 90, 160)).save(made, format="PNG")
    return made.getvalue()


def piece(seconds: float) -> bytes:
    """A tiny real video, `seconds` long."""
    with tempfile.TemporaryDirectory() as folder:
        made = f"{folder}/piece.mp4"
        subprocess.run(
            [
                settings.FFMPEG,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=red:s=72x128:r=25",
                "-t",
                str(seconds),
                "-pix_fmt",
                "yuv420p",
                made,
            ],
            check=True,
            capture_output=True,
        )
        return Path(made).read_bytes()


def holding_the_fixture(clip: ProducedItem, *, piece_seconds: float = 1.0) -> SentList:
    """Keep the fixture's sent list on `clip`, its files saved for real, and give it back."""
    raw: dict[str, Any] = json.loads(FIXTURE.read_text())
    for number, picture in enumerate([*raw["pictures"], *raw["left_out"]], 1):
        picture["file"] = file_store.save(f"sent_{number}.png", _picture(10 * number))
    made = piece(piece_seconds)
    for each in raw["pieces"]:
        each["file"] = file_store.save(f"piece_{each['number']}.mp4", made)
    sent = SentList.model_validate(raw)
    keep_sent_list(clip, sent)
    return sent
