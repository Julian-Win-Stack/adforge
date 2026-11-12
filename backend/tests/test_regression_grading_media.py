"""Grading page media and parts, checked without a browser."""

import json
import re
from pathlib import Path
from typing import Any

import PIL.Image

from evals.regression.grading import build_page, main

from .conftest import picture
from .test_regression_grading import fixture_store


def page_data(path: Path) -> dict[str, Any]:
    match = re.search(r'<script type="application/json" id="data">(.*?)</script>', path.read_text())
    assert match
    data: dict[str, Any] = json.loads(match.group(1).replace("<\\/", "</"))
    return data


def test_pictures_are_served_full_size_as_webp(tmp_path: Path) -> None:
    store = fixture_store(tmp_path)
    picture_file = store.export_dir("new", "chair") / "chair/scene-1/start-picture.png"
    picture_file.write_bytes(picture(1600, 1200, (90, 130, 110)))
    data = page_data(build_page(store, "new"))
    pictures = [url for key, url in data["media"].items() if "chair/scene-1" in key]
    assert pictures and all(url.endswith(".webp") for url in pictures)
    served = store.run_dir("new") / "grading-page" / pictures[0]
    with PIL.Image.open(served) as image:
        assert image.format == "WEBP"
        assert image.size == (1600, 1200)
    assert served.stat().st_size < picture_file.stat().st_size


def test_a_page_can_hold_part_of_a_run(tmp_path: Path) -> None:
    store = fixture_store(tmp_path)
    output = tmp_path / "part"
    assert main(["new", "--only", "chair", "--output", str(output)], store=store) == 0
    data = page_data(output / "grading.html")
    assert list(data["ads"]) == ["chair"]
    assert not any(key.startswith(("new/mug", "old/mug")) for key in data["media"])
    assert main(["new", "--only", "sofa", "--output", str(output)], store=store) == 1
