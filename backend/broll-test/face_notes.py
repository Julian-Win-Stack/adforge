"""Test B of docs/plans/broll-boreal-h3.md: does the photo picker's Face note match the user
(items 26 and 28 of docs/broll-picture-logic.md)? The picker gets one more field, the marks
of kept photos that show a stranger's face, and is run on fresh Firecrawl marked screenshots
of the 8 test products' pages (the second run was made before Firecrawl, so it kept none).
Pictures only: no videos.

    docker compose exec backend python broll-test/face_notes.py

Each page's Firecrawl answers are kept, so running it again pays only for the picker. Writes
MEDIA_ROOT/broll-test/face-notes/results.json and sheet.jpg, a contact sheet of every kept
photo marked FACE or no face, for the user to mark where they disagree."""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")

import django  # noqa: E402

django.setup()

from django.db import connection  # noqa: E402
from PIL import Image as Picture  # noqa: E402
from PIL import ImageDraw, ImageFont  # noqa: E402

from adforge import file_store  # noqa: E402
from gateway.gateway import IMAGE_TYPES, call_model  # noqa: E402
from gateway.types import Image  # noqa: E402
from jobs import firecrawl, page, photos  # noqa: E402
from jobs.models import Job  # noqa: E402

OUT = Path("/app/media/broll-test/face-notes")
PRODUCTS = ["02", "03", "05", "06", "07", "08", "01", "09"]

FACE_NOTE = """

Then note faces: face_images are the I numbers, among the ones you picked, of pictures that \
show a person's face you could recognise. A body, a hand, an arm, lips or a face turned away, \
cut off or too small to recognise doesn't count."""
INSTRUCTIONS = photos.PICK_INSTRUCTIONS + FACE_NOTE


class PickedWithFaces(photos.PickedPhotos):
    face_images: list[int]


def firecrawl_answers(product: str, link: str) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    """The marked screenshot's answer, the screenshot, and the record's answer: kept on disk
    the first time, so Firecrawl is paid once."""
    folder = OUT / product
    folder.mkdir(parents=True, exist_ok=True)
    if not (folder / "marked.json").exists():
        answer, shot = firecrawl.marked_screenshot(link)
        (folder / "screenshot.png").write_bytes(shot)
        (folder / "marked.json").write_text(json.dumps(answer))
    if not (folder / "product.json").exists():
        (folder / "product.json").write_text(json.dumps(firecrawl.product_record(link)))
    return (
        json.loads((folder / "marked.json").read_text()),
        (folder / "screenshot.png").read_bytes(),
        json.loads((folder / "product.json").read_text()),
    )


def picker_images(product: str, record: dict[str, Any] | None, screenshot: bytes) -> list[Image]:
    """As jobs.work._picker_images: the official photos R1, R2, ..., then the parts."""
    images: list[Image] = []
    for url in photos.reference_urls(record) if record else []:
        try:
            official = page.download(url, max_bytes=page.MAX_PHOTO_BYTES, what="product photo")
        except Exception:  # noqa: BLE001 - a guide only
            continue
        if official.content_type not in IMAGE_TYPES:
            continue
        number = len(images) + 1
        key = file_store.save(
            f"broll-test/face-notes/{product}/official-{number}", official.content
        )
        images.append(Image(f"R{number}", key))
    with photos.any_size():
        parts = photos.parts(screenshot)
    for number, (top, part) in enumerate(parts, 1):
        key = file_store.save(f"broll-test/face-notes/{product}/part-{number}.jpg", part)
        images.append(Image(f"Part {number} of {len(parts)} (from {top} px down the page):", key))
    return images


def run(product: str) -> dict[str, Any]:
    try:
        job = Job.objects.get(session__name__startswith=f"R2 {product} ")
        answer, screenshot, record_answer = firecrawl_answers(product, job.product_url)
        marks = firecrawl.marks_in(answer)
        record = firecrawl.record_in(record_answer)
        picked = call_model(
            job=None,
            purpose="pick_photos",
            instructions=INSTRUCTIONS,
            handoff=photos.handoff(
                record, marks, str(answer.get("metadata", {}).get("title") or "")
            ),
            output=PickedWithFaces,
            images=picker_images(product, record, screenshot),
        )
        by_number = {picture["n"]: picture for picture in photos.numbered(marks)}
        copies = photos.Copies()
        kept = []
        for number in [*picked.gallery_images, *picked.more_images]:
            if number not in by_number:
                continue
            url = photos.best_url(by_number[number])
            if copies.seen_link(url):
                continue
            try:
                photo = page.download(url, max_bytes=page.MAX_PHOTO_BYTES, what="product photo")
            except Exception as error:  # noqa: BLE001
                kept.append({"mark": number, "url": url, "failed": str(error)})
                continue
            if photo.content_type not in IMAGE_TYPES or not copies.is_new(url, photo.content):
                continue
            file = OUT / product / f"I{number}.img"
            file.write_bytes(photo.content)
            kept.append(
                {
                    "mark": number,
                    "url": url,
                    "file": str(file),
                    "face": number in picked.face_images,
                }
            )
        return {
            "product": product,
            "name": job.session.name if job.session else product,
            "picked": picked.model_dump(),
            "kept": kept,
        }
    except Exception as error:  # noqa: BLE001 - a failed page is a result too
        return {"product": product, "failed": f"{type(error).__name__}: {error}"}
    finally:
        connection.close()


def contact_sheet(results: list[dict[str, Any]]) -> Picture.Image:
    size, label, columns = 220, 34, 8
    font = ImageFont.load_default(size=20)
    rows = []
    for result in results:
        photos_kept = [k for k in result.get("kept", []) if "file" in k]
        for start in range(0, max(len(photos_kept), 1), columns):
            rows.append((result, photos_kept[start : start + columns], start == 0))
    sheet = Picture.new(
        "RGB", (columns * (size + 10) + 10, len(rows) * (size + label + 40)), "white"
    )
    draw = ImageDraw.Draw(sheet)
    y = 0
    for result, row, first in rows:
        if first:
            draw.text((10, y + 8), result.get("name", result["product"]), fill="black", font=font)
        for column, kept in enumerate(row):
            x = 10 + column * (size + 10)
            with Picture.open(kept["file"]) as opened:
                photo = opened.convert("RGB")
            photo.thumbnail((size, size))
            sheet.paste(photo, (x + (size - photo.width) // 2, y + 36 + (size - photo.height) // 2))
            colour = "#d00" if kept["face"] else "#080"
            draw.rectangle((x, y + 36 + size, x + size, y + 36 + size + label), fill=colour)
            text = f"{result['product']}-I{kept['mark']}  {'FACE' if kept['face'] else 'no face'}"
            draw.text((x + 6, y + 40 + size), text, fill="white", font=font)
        y += size + label + 40
    return sheet


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run, PRODUCTS))
    (OUT / "results.json").write_text(json.dumps(results, indent=2))
    contact_sheet(results).save(OUT / "sheet.jpg", quality=85)
    print("Wrote", OUT / "results.json", "and", OUT / "sheet.jpg")
    for result in results:
        if "failed" in result:
            print(result["product"], "FAILED", result["failed"])
            continue
        faces = [k["mark"] for k in result["kept"] if k.get("face")]
        print(
            result["product"],
            len(result["kept"]),
            "kept; faces:",
            faces,
            "|",
            result["picked"]["notes"][:150],
        )


if __name__ == "__main__":
    main()
