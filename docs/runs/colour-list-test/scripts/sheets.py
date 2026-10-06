"""One contact sheet per page: every kept photo, numbered, with a green frame if it is on the
planner's colour list and a red one if not, under the colour the plan chose.

    docker compose exec backend python scratch/colour-list-test/sheets.py

Sheets go to outputs/sheets/<key>.jpg."""

import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE / "outputs"
TILE = 300
COLUMNS = 5
HEAD = 40


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except OSError:
        return ImageFont.load_default(size)


def sheet(result: dict) -> None:
    photos = result.get("photos") or []
    if not photos:
        return
    rows = (len(photos) + COLUMNS - 1) // COLUMNS
    canvas = Image.new("RGB", (COLUMNS * TILE, HEAD + rows * TILE), "white")
    draw = ImageDraw.Draw(canvas)
    draw.text(
        (8, 8),
        f"{result['key']}  colour: {result.get('product_colour')!r}",
        fill="black",
        font=font(20),
    )
    for index, photo in enumerate(photos):
        x, y = (index % COLUMNS) * TILE, HEAD + (index // COLUMNS) * TILE
        path = OUT / result["key"] / photo["file"]
        try:
            picture = Image.open(io.BytesIO(path.read_bytes())).convert("RGB")
            picture.thumbnail((TILE - 16, TILE - 16))
            canvas.paste(picture, (x + 8 + (TILE - 16 - picture.width) // 2, y + 8))
        except Exception:  # noqa: BLE001
            draw.text((x + 20, y + 100), "can't open", fill="black", font=font(18))
        colour = "green" if photo["on_list"] else "red"
        draw.rectangle((x + 2, y + 2, x + TILE - 3, y + TILE - 3), outline=colour, width=6)
        draw.rectangle((x + 6, y + 6, x + 56, y + 40), fill=colour)
        draw.text((x + 12, y + 8), str(photo["position"]), fill="white", font=font(26))
    (OUT / "sheets").mkdir(exist_ok=True)
    canvas.save(OUT / "sheets" / f"{result['key']}.jpg", quality=85)


for file in sorted(OUT.glob("*.json")):
    sheet(json.load(open(file)))
