"""Look at the pairs the picture hash called "the same picture": draw them side by side with the
hash distance and a colour distance, to choose the duplicate rule (outputs/dupe-check/dupe-pairs-<n>.jpg).
Usage: backend/.venv/bin/python scripts/check_dupes.py"""
import glob, json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent.parent
IMG = Path("/tmp/pm/img")


def colour(path):
    im = Image.open(path).convert("RGB").resize((4, 4))
    return list(im.getdata())


def cdist(a, b):
    return sum(abs(x - y) for p, q in zip(a, b) for x, y in zip(p, q)) / (16 * 3)


def hd(a, b):
    return bin(int(a) ^ int(b)).count("1")


pairs = []
for f in sorted(glob.glob(str(HERE / "outputs/pages/*.json"))):
    r = json.load(open(f))
    shots = [p for p in r["screen"] if p.get("hash")]
    prods = [p for p in r["product_photos"] if p.get("hash") and p.get("path")]
    for i, p in enumerate(prods):
        near = min(prods[:i], key=lambda q: hd(p["hash"], q["hash"]), default=None)  # closest earlier photo only
        if near is not None and hd(p["hash"], near["hash"]) <= 20:
            pairs.append((r["key"], "prod-prod", hd(p["hash"], near["hash"]), near["path"], p["path"]))
        for s in shots:
            d = hd(p["hash"], s["hash"])
            if d <= 20 and p["name"] not in s["names"]:
                sp = IMG / r["key"]
                spath = next((str(x) for x in sp.glob("*.json") if json.load(open(x))["url"] == s["url"]), None)
                if spath: pairs.append((r["key"], "prod-screen", d, spath.replace(".json", ".jpg"), p["path"]))
print(len(pairs), "pairs")
font = ImageFont.load_default(size=22)
T, PER = 220, 12
rows = []
for k, kind, d, a, b in pairs:
    rows.append((k, kind, d, cdist(colour(a), colour(b)), a, b))
rows.sort(key=lambda x: (x[2], x[3]))
json.dump([r[:4] for r in rows], open(HERE / "outputs" / "dupe-check" / "dupe-pairs.json", "w"), indent=0)
for s in range(0, len(rows), PER):
    chunk = rows[s:s + PER]
    sheet = Image.new("RGB", (3 * (2 * T + 20), ((len(chunk) + 2) // 3) * (T + 40)), "white")
    d = ImageDraw.Draw(sheet)
    for i, (k, kind, h, c, a, b) in enumerate(chunk):
        x, y = (i % 3) * (2 * T + 20), (i // 3) * (T + 40)
        for j, p in enumerate((a, b)):
            im = Image.open(p); im.thumbnail((T - 6, T - 6)); sheet.paste(im, (x + j * T + 3, y + 3))
        d.text((x + 3, y + T + 4), f"#{s + i} {k[:18]} {kind[-6:]} h{h} c{c:.0f}", fill="black", font=font)
    sheet.save(HERE / "outputs" / "dupe-check" / f"dupe-pairs-{s // PER + 1}.jpg", quality=80)
