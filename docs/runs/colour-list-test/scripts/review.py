"""The review page for the user's judgement (issue #110, number 3): for each planned page,
the plan's B-roll scenes and every photo, on the colour list or dropped, with Claude's guesses.
The user ticks the dropped photos a scene needs and the listed ones that shouldn't be on the
list, and copies the answers.

    docker compose exec backend python scratch/colour-list-test/review.py

Writes outputs/review/index.html, with small copies of the photos beside it."""

import html
import io
import json
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
OUT = HERE / "outputs"
REVIEW = OUT / "review"
REASON = {
    "other_colour": "another colour",
    "other_size": "another size",
    "with_others": "with other products",
    "not_clear": "product not clearly seen",
    "no_product": "no product in it",
    "clear_right_colour": "right colour and clearly seen",
    "unsure": "can't tell",
}

judged = json.load(open(HERE / "judged.json"))
# Every planned page.
SAMPLE = sorted(judged)
# Every planned page, those with dropped photos first.
SAMPLE = sorted(judged, key=lambda key: (not judged[key]["dropped"], key))
REVIEW.mkdir(parents=True, exist_ok=True)
parts = []
for key in SAMPLE:
    r = json.load(open(OUT / f"{key}.json"))
    files = {str(p["position"]): p["file"] for p in r["photos"]}
    on_list = [str(p["position"]) for p in r["photos"] if p["on_list"]]
    shows = "".join(
        f"<li>Scene {s['number']}: {html.escape(s['shows'])}</li>" for s in r["scenes"] if s["shows"]
    )
    tiles = []
    for position in on_list + list(judged[key]["dropped"]):
        name = f"{key}-{position}.jpg"
        picture = Image.open(io.BytesIO((OUT / key / files[position]).read_bytes())).convert("RGB")
        picture.thumbnail((360, 360))
        picture.save(REVIEW / name, quality=82)
        if position in on_list:
            wrong = judged[key]["wrong_on_list"].get(position)
            checked = " checked" if wrong else ""
            note = f"<br><small>{html.escape(wrong[1])}</small>" if wrong else ""
            tiles.append(
                f'<figure class="kept"><img src="{name}"><figcaption><b>{position}</b> on the list'
                f'{note}<br><label><input type=checkbox data-kind="wrong" data-key="{key}" '
                f'data-photo="{position}"{checked}> shouldn\'t be on the list</label>'
                "</figcaption></figure>"
            )
            continue
        reason, needed, note = judged[key]["dropped"][position]
        checked = " checked" if needed else ""
        tiles.append(
            f'<figure><img src="{name}"><figcaption><b>{position}</b> dropped: {REASON[reason]}'
            f"<br><small>{html.escape(note)}</small><br><label><input type=checkbox "
            f'data-kind="needed" data-key="{key}" data-photo="{position}"{checked}> a scene needs this</label>'
            "</figcaption></figure>"
        )
    parts.append(
        f"<section><h2>{key}</h2><p>Plan's colour: <b>{html.escape(r['product_colour'])}</b>"
        f"</p><p>B-roll scenes:</p><ul>{shows}</ul><div class=grid>{''.join(tiles)}</div>"
        "</section>"
    )

page = f"""<!doctype html><meta charset=utf-8><title>Colour list: which dropped photos does a scene need?</title>
<style>
body {{ font: 15px/1.4 system-ui, sans-serif; max-width: 1200px; margin: 2em auto; padding: 0 1em; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }}
figure {{ margin: 0; border: 3px solid #d33; padding: 6px; border-radius: 6px; }}
figure.kept {{ border-color: #2a2; }}
img {{ width: 100%; height: 190px; object-fit: contain; background: #f4f4f4; }}
section {{ border-top: 2px solid #ccc; margin-top: 2em; }}
#out {{ width: 100%; height: 8em; }}
</style>
<h1>Which dropped photos does a scene need?</h1>
<p>For each product: the plan's colour, its B-roll scenes, and all its photos. Green ones are on
the colour list; red ones were dropped. Claude's guesses are already ticked.<br>
<b>Red:</b> tick "a scene needs this" if a B-roll scene needs that photo.<br>
<b>Green:</b> tick "shouldn't be on the list" if it isn't the right colour or doesn't clearly show the product.<br>
Then press the button and paste the answers into the chat.</p>
<p><button onclick="copyAnswers()">Copy my answers</button></p>
<textarea id=out readonly></textarea>
{''.join(parts)}
<script>
function copyAnswers() {{
  const answers = {{}};
  for (const box of document.querySelectorAll('input[type=checkbox]')) {{
    const page = (answers[box.dataset.key] ??= {{needed: [], wrong: []}});
    if (box.checked) page[box.dataset.kind].push(Number(box.dataset.photo));
  }}
  const text = JSON.stringify(answers);
  document.getElementById('out').value = text;
  navigator.clipboard?.writeText(text);
}}
</script>
"""
(REVIEW / "index.html").write_text(page)
print(REVIEW / "index.html")
