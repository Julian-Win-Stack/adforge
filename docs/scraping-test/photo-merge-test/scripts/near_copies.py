"""After blind judging: check which added photos are the same shot as a screenshot pick (a copy the
file-name and hash rules missed: other crop, other background, other file). Draws, per page, the
screenshot picks (S1, S2...) next to every added photo (A labels as on the judging sheets), each
added photo paired with its closest screenshot pick by picture hash.
Writes outputs/near/<key>-<k>.jpg. The decisions are typed into outputs/near-copies.json by hand.
Usage: backend/.venv/bin/python scripts/near_copies.py"""
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "outputs"
(OUT / "near").mkdir(exist_ok=True)
IMG = Path("/tmp/pm/img")
key_rows = json.load(open(OUT / "added-key.json"))
font = ImageFont.load_default(size=26)
T = 260


def screen_path(key, url):
    for m in (IMG / key).glob("*.json"):
        r = json.load(open(m))
        if r["url"] == url: return r["path"], r["hash"]
    return None, None


def hd(a, b):
    return bin(int(a) ^ int(b)).count("1") if a and b else 999


for key in sorted({r["key"] for r in key_rows}):
    page = json.load(open(OUT / "pages" / f"{key}.json"))
    shots = []
    for i, p in enumerate(page["screen"], 1):
        path, h = screen_path(key, p["url"])
        if path: shots.append((f"S{i}", path, h))
    prod = {ph["url"]: ph for ph in page["product_photos"]}
    rows = sorted([r for r in key_rows if r["key"] == key], key=lambda r: int(r["label"][1:]))
    pairs = []
    for r in rows:
        h = prod[r["url"]]["hash"]
        best = min(shots, key=lambda s: hd(h, s[2])) if shots else None
        pairs.append((r["label"], r["path"], best, hd(h, best[2]) if best else 999))
    # sheet: pairs, 3 per row (added | closest screenshot pick)
    PER = 12
    for s in range(0, len(pairs), PER):
        chunk = pairs[s:s + PER]
        sheet = Image.new("RGB", (3 * (2 * T + 20), ((len(chunk) + 2) // 3) * (T + 34)), "white")
        d = ImageDraw.Draw(sheet)
        for i, (lab, path, best, dist) in enumerate(chunk):
            x, y = (i % 3) * (2 * T + 20), (i // 3) * (T + 34)
            for j, p in enumerate((path, best[1] if best else None)):
                if not p: continue
                im = Image.open(p); im.thumbnail((T - 6, T - 6)); sheet.paste(im, (x + j * T + 3, y + 3))
            d.text((x + 3, y + T + 4), f"{lab}  vs  {best[0] if best else '-'}  (hash {dist})", fill="black", font=font)
        sheet.save(OUT / "near" / f"{key}-{s // PER + 1}.jpg", quality=80)
    # and all screenshot picks of the page on one sheet
    if shots:
        cols = 6
        sheet = Image.new("RGB", (cols * T, ((len(shots) + cols - 1) // cols) * (T + 34)), "white")
        d = ImageDraw.Draw(sheet)
        for i, (lab, path, _) in enumerate(shots):
            x, y = (i % cols) * T, (i // cols) * (T + 34)
            im = Image.open(path); im.thumbnail((T - 6, T - 6)); sheet.paste(im, (x + 3, y + 3))
            d.text((x + 3, y + T + 4), lab, fill="black", font=font)
        sheet.save(OUT / "near" / f"{key}-screen.jpg", quality=80)
    print(key, len(pairs), "pairs,", len(shots), "screenshot picks")
