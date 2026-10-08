"""Build the B-roll model test's grading page: each scene's five clips side by side, blind.

Run from the repo root once backend/broll-test/run.py has finished:

    python3 docs/runs/second-run-review/build_broll.py

Writes broll.html next to this file (served at http://localhost:8765/broll.html). The clips are
labelled A–E, cheapest first, the same model under the same letter in every row, with
each model's name and price shown (the user chose this over a blind shuffle). Grades are kept in the browser and
exported with "Download CSV".
"""

import html
import json
import subprocess
import urllib.request
from pathlib import Path

MEDIA = "http://localhost:8000/media/"
HERE = Path(__file__).parent


# Only the models still in the running: the user dropped Boreal and PixVerse V6 (no end
# picture), then Seedance 1.5 Pro and Veo 3.1 Lite, on 2026-10-01. Letters stay as before,
# so saved grades still match.
SHOWN = {"h3"}

# The scenes still tested, by their number on the page (the user picked these on 2026-10-01).
TESTED = {2, 4, 7, 11, 13, 14, 15, 16}

LABELS = {
    "boreal": "Boreal, $0.01/s (now)",
    "seedance": "Seedance 1.5 Pro, $0.026/s",
    "veo": "Veo 3.1 Lite, $0.03/s",
    "h3": "H3 Max Turbo, $0.04/s",
    "pixverse": "PixVerse V6, $0.045/s",
}


def meta(model, scene_id):
    try:
        return fetch(f"broll-test/{model}/{scene_id}.json")
    except Exception:
        return {}


def sent(body, made):
    """What one model was sent for one clip, every field, plus what came back."""
    rows = []
    for field, value in body.items():
        if field == "image_url":
            value = f'<a href="{MEDIA}{e(value)}" target="_blank">the starting picture (left)</a>'
        else:
            value = f"<pre>{e(value)}</pre>" if field == "prompt" else e(json.dumps(value).strip('"'))
        rows.append(f"<div><b>{e(field)}</b>: {value}</div>")
    if made.get("made_seconds"):
        rows.append(f"<div class='got'>Came back {made['made_seconds']} s, kept "
                    f"{made['kept_seconds']} s (cut to the voice line)"
                    + (f", made in {made['seconds_to_make']} s" if made.get("seconds_to_make") else "")
                    + "</div>")
    return f"<details class='sent'><summary>What we sent</summary>{''.join(rows)}</details>"


def fetch(path):
    return json.load(urllib.request.urlopen(MEDIA + path))


def e(s):
    return html.escape(str(s or ""))


def product_links():
    """Each R2 ad's product page, by session name."""
    out = subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "psql", "-U", "adforge", "adforge", "-tAc",
         "select json_object_agg(cs.name, j.product_url) from chat_session cs "
         "join jobs_job j on j.session_id = cs.id where cs.name like 'R2 %'"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return json.loads(out) if out else {}


def main():
    scenes = fetch("broll-test/scenes.json")
    links = product_links()
    key = fetch("broll-test/key.json")
    inputs = fetch("broll-test/inputs.json")
    rows = []
    for number, s in enumerate(scenes, 1):
        if number not in TESTED:
            continue
        cells = []
        for letter, model in sorted(key[s["id"]].items()):
            if model not in SHOWN:
                continue
            src = f"{MEDIA}broll-test/blind/{s['id']}_{letter}.mp4"
            cells.append(f"""
  <td class="clip" data-key="{e(s['id'])}|{letter}">
    <b>{letter} · {e(LABELS[model])}</b><br>
    <video src="{src}" controls preload="none"></video>
    <select class="verdict"><option></option><option>pass</option><option>fail</option></select>
    <textarea class="notes" placeholder="What you see"></textarea>
    {sent(inputs[s['id']][model], meta(model, s['id']))}
  </td>""")
        rows.append(f"""
<tr><td class="info">
  <div class="number">#{number}</div>
  <b>{e(s['ad'])}</b> · scene {s['scene']} · {s['line_seconds']:.2f} s<br>
  <a class="small" href="{e(links.get(s['ad']))}" target="_blank">Product page ↗</a><br>
  <a href="{MEDIA}{e(s['picture'])}" target="_blank"><img src="{MEDIA}{e(s['picture'])}" loading="lazy"></a>
  <div class="small"><b>Line:</b> {e(s['line'])}</div>
  <div class="small"><b>Shows:</b> {e(s['shows'])}</div>
  <details><summary>Motion prompt</summary>
    <div class="small">{e(s['motion_prompt'])}</div></details>
</td>{''.join(cells)}</tr>""")
    page = PAGE.replace("ROWS", "".join(rows)).replace("MEDIA", MEDIA)
    (HERE / "broll.html").write_text(page)
    print(f"Wrote {HERE / 'broll.html'}: {len(rows)} scenes")


PAGE = """<!doctype html>
<meta charset="utf-8">
<title>B-roll model test</title>
<style>
:root { color-scheme: light; }
body { font: 14px system-ui, sans-serif; margin: 20px; background: #fff; color: #111; }
table { border-collapse: collapse; }
td { border: 1px solid #ddd; padding: 6px; vertical-align: top; }
.info { width: 220px; } .info img { max-width: 200px; max-height: 300px; }
.clip { width: 190px; }
.number { font-size: 20px; font-weight: bold; }
video { width: 180px; max-height: 330px; display: block; margin: 4px 0; }
textarea { width: 100%; height: 70px; }
.small { font-size: 12px; color: #555; margin-top: 4px; }
.model { color: #a00; font-weight: bold; }
.sent { font-size: 11px; color: #444; margin-top: 4px; }
.sent pre { white-space: pre-wrap; font: inherit; margin: 2px 0 6px; background: #f6f6f6; padding: 4px; }
.sent .got { margin-top: 6px; color: #060; }
td.pass { background: #e8f6e8; } td.fail { background: #fbe9e9; }
#bar { position: sticky; top: 0; background: #fff; padding: 8px 0; border-bottom: 1px solid #ccc; z-index: 1; }
</style>
<div id="bar">
  <b>B-roll model test.</b> Each row is one scene; D is H3 Max Turbo,
  made from the same starting picture and motion prompt, each cut to the voice
  line with the voice over it. Grade each clip pass/fail and say why.
  <button id="csv">Download CSV</button>
  <span id="count"></span>
</div>
<table>ROWS</table>
<script>
const KEY = "adforge-broll-test-grades";
const saved = JSON.parse(localStorage.getItem(KEY) || "{}");
const cells = document.querySelectorAll(".clip");
function paint(td) { td.className = "clip " + td.querySelector(".verdict").value; }
function count() {
  const n = Object.values(saved).filter(g => g.verdict).length;
  document.getElementById("count").textContent = n + " of " + cells.length + " graded";
}
cells.forEach(td => {
  const g = saved[td.dataset.key] || {};
  td.querySelector(".verdict").value = g.verdict || "";
  td.querySelector(".notes").value = g.notes || "";
  paint(td);
  td.addEventListener("input", () => {
    saved[td.dataset.key] = {verdict: td.querySelector(".verdict").value,
                             notes: td.querySelector(".notes").value};
    localStorage.setItem(KEY, JSON.stringify(saved));
    paint(td); count();
  });
});
count();
let models = null;
document.getElementById("csv").onclick = async () => {
  if (!models) models = await (await fetch("MEDIAbroll-test/key.json")).json();
  const q = s => '"' + String(s || "").replace(/"/g, '""') + '"';
  const lines = ["scene,letter,model,verdict,notes"];
  cells.forEach(td => {
    const [scene, letter] = td.dataset.key.split("|");
    const g = saved[td.dataset.key] || {};
    lines.push([scene, letter, models[scene][letter], g.verdict, g.notes].map(q).join(","));
  });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([lines.join("\\n")], {type: "text/csv"}));
  a.download = "broll-test-grades.csv";
  a.click();
};
</script>
"""

if __name__ == "__main__":
    main()
