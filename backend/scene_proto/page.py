"""PROTOTYPE, never merged: the stills round's page, one per round, for Julian's phone.

    uv run python -m scene_proto.page <scenes dir> <round out dir> <round name>

Writes <round out dir>/page/index.html and its media (pictures as phone-size JPEGs, the old
clips). Notes are kept in the artifact's db, collection "notes", one doc per scene."""

import html
import io
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import PIL.Image

NAMES = {
    "mouse": "Logitech mouse",
    "chipolo": "Chipolo key finder",
    "serum": "Glow Recipe serum",
    "frother": "Zulay frother",
    "preworkout": "Legion pre-workout",
}
ORDER = list(NAMES)


def _jpeg(source: Path, target: Path, width: int = 720) -> None:
    with PIL.Image.open(source) as picture:
        picture = picture.convert("RGB")
        if picture.width > width:
            picture = picture.resize((width, round(picture.height * width / picture.width)))
        data = io.BytesIO()
        picture.save(data, "JPEG", quality=82)
    target.write_bytes(data.getvalue())


def _e(text: Any) -> str:
    return html.escape(str(text or ""))


def _scene(slug: str, scenes: Path, out: Path, media: Path) -> str:
    run_file = out / slug / "run.json"
    told = json.loads((scenes / slug / "scene.json").read_text())
    name = NAMES.get(slug, slug)
    if not run_file.exists():
        return f'<section id="{slug}"><h2>{_e(name)}</h2><p class="bad">No run.</p></section>'
    run = json.loads(run_file.read_text())
    row = run["row"]
    parts = [
        f'<section id="{slug}"><h2>{_e(name)} · scene {run["scene"]}</h2>',
        f'<p class="meta">Ended: {_e(run["ended"])} · {len(run["pictures"])} pictures · '
        f'${float(run["cost_usd"]):.2f}</p>',
        f'<p><b>Line:</b> "{_e(row["line"])}"<br><b>Shows:</b> {_e(row.get("shows"))}<br>'
        f'<b>Kind:</b> {_e(row.get("broll_kind"))} · {_e(row.get("person_shown"))}</p>',
    ]
    for change in run["scene_changes"]:
        parts.append(
            f'<p class="note">After picture {change["after_picture"]} the agent changed the '
            f"scene: {_e(change['why'])}<br>Now shows: {_e(change['shows'])}<br>Line: "
            f'"{_e(change["line"])}"</p>'
        )
    for picture in run["pictures"]:
        file = f"{slug}-p{picture['number']}.jpg"
        _jpeg(out / slug / f"picture-{picture['number']}.png", media / file)
        mark = "pass" if picture["passed"] else "fail"
        q1, q2 = picture["product_same"], picture["physics"]
        parts.append(
            f'<div class="pic {mark}"><img src="media/{file}" loading="lazy" '
            f'alt="Starting picture {picture["number"]}">'
            f'<div class="ans"><b>Picture {picture["number"]}</b> ({picture["how"]}) · '
            f'<span class="{mark}">{mark.upper()}</span><br>'
            f"1. Product same as photos: <b>{'yes' if q1['passes'] else 'no'}</b>. "
            f"{_e(q1['says'])}<br>"
            f"2. Breaks physics: <b>{'no' if q2['passes'] else 'yes'}</b>. {_e(q2['says'])}"
            f"<details><summary>Picture prompt</summary><p>{_e(picture['prompt'])}</p>"
            f"</details></div></div>"
        )
    parts.append(
        "<h3>Final video prompt</h3>"
        + (
            f'<p class="prompt">{_e(run["video_prompt"])}</p>'
            if run["video_prompt"]
            else '<p class="bad">None written.</p>'
        )
    )
    old = told.get("old_clip") or {}
    if old.get("file") and (scenes / slug / old["file"]).exists():
        target = f"{slug}-old.mp4"
        shutil.copy(scenes / slug / old["file"], media / target)
        parts.append(
            f'<h3>Old clip (before)</h3><video src="media/{target}" controls playsinline '
            'preload="metadata"></video>'
        )
    elif old.get("url"):
        parts.append(f'<h3>Old clip (before)</h3><p><a href="{_e(old["url"])}">Open</a></p>')
    if old.get("prompt"):
        parts.append(
            f"<details><summary>Old clip's prompt</summary><p>{_e(old['prompt'])}</p></details>"
        )
    parts.append(
        f'<h3>Your notes</h3><textarea data-scene="{slug}" placeholder="What is wrong?">'
        '</textarea><p class="saved" data-for="' + slug + '"></p></section>'
    )
    return "".join(parts)


STYLE = """
:root{--bg:#fafaf9;--fg:#1c1917;--muted:#78716c;--card:#fff;--line:#e7e5e4;--pass:#15803d;--fail:#b91c1c}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#1c1917;--fg:#f5f5f4;--muted:#a8a29e;--card:#292524;--line:#44403c;--pass:#4ade80;--fail:#f87171}}
:root[data-theme="dark"]{--bg:#1c1917;--fg:#f5f5f4;--muted:#a8a29e;--card:#292524;--line:#44403c;--pass:#4ade80;--fail:#f87171}
body{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;margin:0;padding:0 16px 40px;max-width:760px;margin-inline:auto}
nav{position:sticky;top:0;background:var(--bg);padding:8px 0;border-bottom:1px solid var(--line);display:flex;gap:10px;flex-wrap:wrap;font-size:14px}
nav a{color:var(--fg)}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin:16px 0}
h1{font-size:20px} h2{font-size:18px;margin:4px 0} h3{font-size:15px;margin:14px 0 4px}
.meta,.saved{color:var(--muted);font-size:13px}
.pic{border-top:1px solid var(--line);padding-top:10px;margin-top:10px}
.pic img{width:100%;max-width:420px;border-radius:8px;display:block}
.pass{color:var(--pass)} .fail{color:var(--fail)} .bad{color:var(--fail)}
.ans{margin-top:6px;font-size:14px}
.prompt{background:var(--bg);padding:8px;border-radius:8px}
.note{background:var(--bg);padding:8px;border-radius:8px;font-size:14px}
video{width:100%;max-width:420px;border-radius:8px}
textarea{width:100%;box-sizing:border-box;min-height:90px;font:inherit;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:8px}
details summary{color:var(--muted);cursor:pointer}
"""

SCRIPT = """
(async () => {
  const db = await window.claude?.use?.("db").catch(() => null);
  const boxes = document.querySelectorAll("textarea[data-scene]");
  const say = (slug, text) => { document.querySelector(`[data-for="${slug}"]`).textContent = text; };
  if (!db) { boxes.forEach(b => say(b.dataset.scene, "Notes can't be saved in this view.")); return; }
  for (const box of boxes) {
    const slug = box.dataset.scene;
    const doc = db.collection("notes").doc(ROUND.toLowerCase().replace(/[^a-z0-9]+/g, "-") + "-" + slug);
    try { const got = await doc.get(); if (got.exists) box.value = (got.data() || {}).text || ""; } catch (e) {}
    let timer;
    box.addEventListener("input", () => {
      clearTimeout(timer); say(slug, "Saving…");
      timer = setTimeout(async () => {
        try { await doc.set({round: ROUND, scene: slug, text: box.value}); say(slug, "Saved"); }
        catch (e) { say(slug, "Not saved: " + (e.code || e.message)); }
      }, 800);
    });
  }
})();
"""


def main(argv: list[str]) -> None:
    scenes, out, round_name = Path(argv[0]), Path(argv[1]), argv[2]
    page = out / "page"
    media = page / "media"
    media.mkdir(parents=True, exist_ok=True)
    slugs = [slug for slug in ORDER if (scenes / slug / "scene.json").exists()]
    nav = " · ".join(f'<a href="#{slug}">{_e(NAMES[slug])}</a>' for slug in slugs)
    body = "".join(_scene(slug, scenes, out, media) for slug in slugs)
    (page / "index.html").write_text(
        f"<title>Scene agent stills</title><style>{STYLE}</style>"
        f"<h1>Scene agent stills · {_e(round_name)}</h1>"
        "<p class=meta>Each scene: every starting picture in order with the check's 2 answers, "
        "then the final video prompt and the old clip. Notes save as you type.</p>"
        f"<nav>{nav}</nav>{body}"
        f"<script>const ROUND = {json.dumps(round_name)};{SCRIPT}</script>"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
