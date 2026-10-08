"""Build the product photos page: every photo on the test ads' pages, downloaded by
backend/broll-test/photos.py, with the ones picked for the test marked.

    python3 docs/runs/second-run-review/build_photos.py

Writes photos.html next to this file (served at http://localhost:8765/photos.html)."""

import html
import json
import urllib.request
from pathlib import Path

MEDIA = "http://localhost:8000/media/"
HERE = Path(__file__).parent
ADS = {
    "02": "Merit Flush Balm · Le Bonbon (bright fuchsia pink)",
    "03": "Vital Proteins Salted Caramel Collagen Peptides",
    "05": "Steve Madden Kenzo Bag · Gold",
    "06": "Anker 313 Power Bank · Black",
    "07": "Great Jones Dutch Baby · Blueberry (royal blue)",
    "08": "Molly's Suds Toilet Bowl Cleaner",
}


def main():
    picked = json.loads((HERE / "picked_photos.json").read_text()) if (HERE / "picked_photos.json").exists() else {}
    sections = []
    for ad, title in ADS.items():
        photos = json.load(urllib.request.urlopen(f"{MEDIA}broll-test/photos/{ad}/index.json"))
        cells = []
        for photo in photos:
            name = photo["file"].rsplit("/", 1)[-1].split(".")[0]
            why = picked.get(ad, {}).get(name)
            note = " · ".join(filter(None, [photo["alt"], ", ".join(photo["variants"])]))
            cells.append(
                f'<figure class="{"picked" if why else ""}"><a href="{MEDIA}{photo["file"]}" target="_blank">'
                f'<img src="{MEDIA}{photo["file"]}" loading="lazy"></a>'
                f'<figcaption><b>{name}</b> {html.escape(note[:80])}'
                + (f'<div class="why">✓ {html.escape(why)}</div>' if why else "")
                + "</figcaption></figure>"
            )
        sections.append(f'<h2 id="ad{ad}">R2 {ad} · {html.escape(title)}</h2><div class="grid">{"".join(cells)}</div>')
    (HERE / "photos.html").write_text(PAGE.replace("SECTIONS", "".join(sections)))
    print("Wrote", HERE / "photos.html")


PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Product photos</title>
<style>
body { font: 14px system-ui, sans-serif; margin: 20px; background: #fff; color: #111; }
.grid { display: flex; flex-wrap: wrap; gap: 8px; }
figure { margin: 0; width: 170px; border: 3px solid #eee; padding: 4px; }
figure.picked { border-color: #2a2; background: #eef8ee; }
img { width: 160px; height: 160px; object-fit: contain; background: #f6f6f6; }
figcaption { font-size: 11px; color: #555; }
.why { color: #060; margin-top: 2px; }
</style>
<p><b>Product photos.</b> Every photo on each test ad's product page. Green = picked for the
end pictures and reference pictures.</p>
SECTIONS
"""

if __name__ == "__main__":
    main()
