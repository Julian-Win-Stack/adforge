"""Job A: one contact sheet per page. Numbered thumbnails, kept section then dropped section,
product name at the top. Drops that were never downloaded (svg, data URIs, listed-tiny,
download failures) have no picture to show, so they are counted in the footer instead.

Usage: python contact_sheet.py <filter.json> <cache-root> <sheets-dir>
Writes <sheets-dir>/<key>.png (or <key>-1.png, -2.png when over 40 tiles) and <key>.json.
"""
import json, sys, textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

COLS, TILE, LABEL_H, PAD = 5, 230, 60, 12
MAX_TILES = 40
W = COLS * (TILE + PAD) + PAD
FONT = ImageFont.load_default(size=15)
FONT_B = ImageFont.load_default(size=22)
FONT_S = ImageFont.load_default(size=13)


def thumb(path: Path) -> Image.Image:
    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        im = Image.new("RGB", (TILE, TILE), (200, 200, 200))
    im.thumbnail((TILE, TILE))
    canvas = Image.new("RGB", (TILE, TILE), (245, 245, 245))
    canvas.paste(im, ((TILE - im.width) // 2, (TILE - im.height) // 2))
    return canvas


def render(key: str, product: str, tiles: list[dict], cache: Path, out: Path) -> list[str]:
    parts, files = [], []
    for i in range(0, len(tiles), MAX_TILES):
        parts.append(tiles[i:i + MAX_TILES])
    if not parts:
        parts = [[]]
    for pi, chunk in enumerate(parts, 1):
        rows = max(1, -(-len(chunk) // COLS))
        H = 70 + rows * (TILE + LABEL_H + PAD) + 40
        img = Image.new("RGB", (W, H), "white")
        d = ImageDraw.Draw(img)
        d.text((PAD, 10), f"{product[:70]}  [{key}]  sheet {pi}/{len(parts)}", fill="black", font=FONT_B)
        d.text((PAD, 40), "K = kept by the filter, D = dropped (reason under each). Numbers continue across sheets.", fill=(90, 90, 90), font=FONT_S)
        for j, t in enumerate(chunk):
            x = PAD + (j % COLS) * (TILE + PAD)
            y = 70 + (j // COLS) * (TILE + LABEL_H + PAD)
            img.paste(thumb(cache / t["file"]), (x, y))
            colour = (0, 120, 0) if t["id"].startswith("K") else (180, 0, 0)
            d.rectangle([x, y, x + TILE - 1, y + TILE - 1], outline=colour, width=3)
            d.rectangle([x, y, x + 46, y + 22], fill=colour)
            d.text((x + 4, y + 3), t["id"], fill="white", font=FONT)
            cap = textwrap.wrap(t["caption"], 34)[:3]
            for li, line in enumerate(cap):
                d.text((x, y + TILE + 4 + li * 17), line, fill=(40, 40, 40), font=FONT_S)
        name = f"{key}.png" if len(parts) == 1 else f"{key}-{pi}.png"
        img.save(out / name, optimize=True)
        files.append(name)
    return files


def main() -> None:
    filt, cache_root, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    data = json.loads(filt.read_text())
    for key, r in data.items():
        items, tiles = [], []
        for i, x in enumerate(r["kept"], 1):
            it = {"id": f"K{i}", "n": x["n"], "url": x["url"], "file": x["file"], "w": x["w"], "h": x["h"], "verdict": x.get("verdict"), "reason": x.get("reason")}
            items.append(it)
            tiles.append({"id": it["id"], "file": x["file"], "caption": f"{x['w']}x{x['h']} {x.get('verdict','')}: {x.get('reason','')}"})
        shown_drops = [x for x in r["dropped"] if x.get("file")]
        for i, x in enumerate(shown_drops, 1):
            it = {"id": f"D{i}", "n": x["n"], "url": x["url"], "file": x["file"], "w": x.get("w"), "h": x.get("h"), "drop": x["drop"], "verdict": x.get("verdict"), "reason": x.get("reason")}
            items.append(it)
            cap = x["drop"] + (f": {x['reason']}" if x.get("reason") else "")
            tiles.append({"id": it["id"], "file": x["file"], "caption": f"{x.get('w')}x{x.get('h')} {cap}"})
        unshown = {}
        for x in r["dropped"]:
            if not x.get("file"):
                reason = x["drop"].split("-")[0] if x["drop"].startswith(("tiny", "download")) else x["drop"]
                unshown[reason] = unshown.get(reason, 0) + 1
        files = render(key, r["product"], tiles, cache_root / key, out)
        (out / f"{key}.json").write_text(json.dumps({"key": key, "product": r["product"], "sheets": files, "items": items, "not_shown": unshown}, indent=1))
        print(f"{key}: {len(r['kept'])} kept, {len(shown_drops)} shown drops, {sum(unshown.values())} not shown -> {files}", flush=True)


if __name__ == "__main__":
    main()
