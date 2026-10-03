"""Build the second run's review page: one row per scene, for grading by eye.

Run from the repo root with the app's containers up:

    python3 docs/runs/second-run-review/build.py

It reads every session named "R2 …" from the database and writes index.html next to this
file. Media is loaded from the backend (http://localhost:8000/media/), so the app must be
running while you grade. Grades are kept in the browser (localStorage) and exported as a CSV
with the "Download CSV" button; that CSV is the critic's answer key.
"""

import html
import json
import subprocess
from pathlib import Path

MEDIA = "http://localhost:8000/media/"
HERE = Path(__file__).parent

QUERY = r"""
with latest as (
  select distinct on (scene_id, kind) *
  from jobs_produceditem where scene_id is not null
  order by scene_id, kind, version desc, created_at desc
), used_photo as (
  select distinct on (st.scene_id) st.scene_id, p.file, p.position, st.photo_reason, st.prompt
  from jobs_scenestep st left join jobs_productphoto p on p.id = st.photo_id
  where st.kind = 'starting_picture' and st.status = 'finished'
  order by st.scene_id, st.started_at desc
), clip_step as (
  select distinct on (scene_id) scene_id, motion_prompt
  from jobs_scenestep where kind = 'clip' and status = 'finished'
  order by scene_id, started_at desc
)
select json_agg(ad order by ad->>'name') from (
  select json_build_object(
    'name', cs.name, 'session', cs.id, 'job', j.id, 'url', j.product_url, 'size', j.product_size,
    'gender', j.person_gender, 'looks', j.person_looks, 'voice', j.person_voice,
    'portrait', (select file from jobs_produceditem where job_id = j.id and kind = 'portrait'
                 order by version desc, created_at desc limit 1),
    'ad', (select file from jobs_produceditem where job_id = j.id and kind = 'finished_ad'
           order by version desc, created_at desc limit 1),
    'photos', (select coalesce(json_agg(json_build_object('file', file, 'position', position,
               'colour', shows_product_colour) order by position), '[]')
               from jobs_productphoto where job_id = j.id),
    'scenes', (select coalesce(json_agg(json_build_object(
        'number', s.number, 'line', s.line, 'shows', s.shows, 'overlay', s.overlay,
        'heard', t.text, 'picture', pic.file, 'clip', c.file, 'clip_seconds', c.seconds,
        'photo', up.file, 'photo_position', up.position, 'photo_reason', up.photo_reason,
        'picture_prompt', up.prompt, 'motion_prompt', cl.motion_prompt
      ) order by s.number), '[]')
      from jobs_scene s
      left join latest t on t.scene_id = s.id and t.kind = 'transcript'
      left join latest pic on pic.scene_id = s.id and pic.kind = 'starting_picture'
      left join latest c on c.scene_id = s.id and c.kind = 'clip'
      left join used_photo up on up.scene_id = s.id
      left join clip_step cl on cl.scene_id = s.id
      where s.job_id = j.id)
  ) ad
  from chat_session cs join jobs_job j on j.session_id = cs.id
  where cs.name like 'R2 %'
) x;
"""


def load():
    out = subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "psql", "-U", "adforge", "adforge", "-tAc", QUERY],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return json.loads(out) if out else []


def e(s):
    return html.escape(s or "")


def media(file, tag):
    if not file:
        return '<span class="none">none</span>'
    url = MEDIA + file
    if tag == "img":
        return f'<a href="{url}" target="_blank"><img src="{url}" loading="lazy"></a>'
    return f'<video src="{url}" controls preload="none"></video>'


def grade(key):
    causes = "".join(f"<option>{c}</option>" for c in
                     ["", "page", "plan", "picture", "voice", "clip", "assembly"])
    return (f'<td class="grade" data-key="{e(key)}">'
            f'<select class="verdict"><option></option><option>pass</option><option>fail</option></select>'
            f'<select class="cause">{causes}</select>'
            f'<textarea class="notes" placeholder="What you see, in your own words"></textarea></td>')


def ad_block(ad):
    key = ad["job"]
    photos = "".join(
        f'<figure>{media(p["file"], "img")}<figcaption>Photo {p["position"]}'
        f'{"" if p["colour"] else " (not colour)"}</figcaption></figure>'
        for p in ad["photos"])
    rows = []
    for s in ad["scenes"]:
        kind = "B-roll" if s["shows"] else "Talking"
        heard = "" if (s["heard"] or "").strip() == "" else (
            f'<div class="small"><b>Heard:</b> {e(s["heard"])}</div>')
        rows.append(f"""
<tr>
  <td><b>{s["number"]}</b><br>{kind}<br><span class="small">{s["clip_seconds"] or ""} s</span></td>
  <td class="text"><b>Line:</b> {e(s["line"])}{heard}
    {f'<div class="small"><b>Overlay:</b> {e(s["overlay"])}</div>' if s["overlay"] else ""}
    {f'<div class="small"><b>Shows:</b> {e(s["shows"])}</div>' if s["shows"] else ""}
    <details><summary>Prompts</summary>
      <div class="small"><b>Photo reason:</b> {e(s["photo_reason"])}</div>
      <div class="small"><b>Picture prompt:</b> {e(s["picture_prompt"])}</div>
      <div class="small"><b>Motion prompt:</b> {e(s["motion_prompt"])}</div>
    </details></td>
  <td>{media(s["photo"], "img")}<div class="small">Photo {s["photo_position"] or "?"}</div></td>
  <td>{media(s["picture"], "img")}</td>
  <td>{media(s["clip"], "video")}</td>
  {grade(f"{key}|{s['number']}")}
</tr>""")
    return f"""
<section>
  <h2>{e(ad["name"])}</h2>
  <div class="small"><a href="{e(ad["url"])}" target="_blank">Product page ↗</a> · Session {e(ad["session"])} · size {e(ad["size"])} · {e(ad["gender"])}</div>
  <table class="top"><tr>
    <td>{media(ad["ad"], "video")}<div class="small">Finished ad</div></td>
    <td>{media(ad["portrait"], "img")}<div class="small">Portrait</div></td>
    <td class="text small"><b>Looks:</b> {e(ad["looks"])}<br><b>Voice:</b> {e(ad["voice"])}</td>
    {grade(f"{key}|ad")}
  </tr></table>
  <details><summary>Photos from the page ({len(ad["photos"])})</summary><div class="photos">{photos}</div></details>
  <table class="scenes">
    <tr><th>Scene</th><th>Words</th><th>Product photo used</th><th>Starting picture</th><th>Clip</th><th>Your grade</th></tr>
    {"".join(rows)}
  </table>
</section>"""


PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Second run: review</title>
<style>
:root { color-scheme: light; }
body { font: 14px system-ui, sans-serif; margin: 20px; max-width: 1500px; background: #fff; color: #111; }
section { border-top: 3px solid #333; margin-top: 30px; padding-top: 10px; }
table { border-collapse: collapse; width: 100%; margin-top: 8px; }
td, th { border: 1px solid #ddd; padding: 6px; vertical-align: top; }
img { max-width: 180px; max-height: 320px; }
video { max-width: 200px; max-height: 360px; }
.text { max-width: 360px; }
.small { font-size: 12px; color: #555; margin-top: 4px; }
.none { color: #aaa; }
.grade { width: 260px; }
.grade select { margin: 0 4px 4px 0; }
.grade textarea { width: 100%; height: 110px; }
.photos { display: flex; flex-wrap: wrap; gap: 8px; }
.photos img { max-width: 120px; }
#bar { position: sticky; top: 0; background: #fff; padding: 8px 0; border-bottom: 1px solid #ccc; }
tr.pass .grade { background: #e8f6e8; } tr.fail .grade { background: #fbe9e9; }
</style>
<div id="bar">
  <b>Second run review.</b> Watch each scene, write what you see, then pick pass/fail and where the
  problem started. Grades save in this browser as you type.
  <button id="csv">Download CSV</button> <span id="count"></span>
</div>
<p class="small">Where it started: <b>page</b> = the scraper gave wrong or missing facts/photos ·
<b>plan</b> = the script or scene idea · <b>picture</b> = the starting picture ·
<b>voice</b> = how the line sounds · <b>clip</b> = the moving video · <b>assembly</b> = cuts,
captions, music, order. Compare the product photo with the starting picture to tell page from picture.</p>
BODY
<script>
const KEY = "adforge-second-run-grades";
const saved = JSON.parse(localStorage.getItem(KEY) || "{}");
const cells = document.querySelectorAll(".grade");
function paint(td) { const v = td.querySelector(".verdict").value; td.parentElement.className = v; }
function count() {
  const n = Object.values(saved).filter(g => g.verdict).length;
  document.getElementById("count").textContent = n + " of " + cells.length + " graded";
}
cells.forEach(td => {
  const g = saved[td.dataset.key] || {};
  td.querySelector(".verdict").value = g.verdict || "";
  td.querySelector(".cause").value = g.cause || "";
  td.querySelector(".notes").value = g.notes || "";
  paint(td);
  td.addEventListener("input", () => {
    saved[td.dataset.key] = {
      verdict: td.querySelector(".verdict").value,
      cause: td.querySelector(".cause").value,
      notes: td.querySelector(".notes").value,
    };
    localStorage.setItem(KEY, JSON.stringify(saved));
    paint(td); count();
  });
});
count();
document.getElementById("csv").onclick = () => {
  const q = s => '"' + String(s || "").replace(/"/g, '""') + '"';
  const lines = ["job,scene,verdict,cause,notes"];
  cells.forEach(td => {
    const [job, scene] = td.dataset.key.split("|");
    const g = saved[td.dataset.key] || {};
    lines.push([job, scene, g.verdict, g.cause, g.notes].map(q).join(","));
  });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([lines.join("\\n")], {type: "text/csv"}));
  a.download = "second-run-grades.csv";
  a.click();
};
</script>
"""


def main():
    ads = load()
    (HERE / "index.html").write_text(PAGE.replace("BODY", "".join(ad_block(a) for a in ads)))
    print(f"Wrote {HERE / 'index.html'}: {len(ads)} ads, "
          f"{sum(len(a['scenes']) for a in ads)} scenes")


if __name__ == "__main__":
    main()
