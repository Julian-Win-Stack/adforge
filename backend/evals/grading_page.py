"""Julian's tap page for grading a round of ads, built from a run folder evals.round_export
wrote. Per ad: the finished ad with a pass/fail tap. Per B-roll scene: the first clip and each
remake, every one with the judge's verdict and what it saw wrong in view (never hidden), a
pass/fail tap on the first clip and, when the auto-redo kept a remake, on the kept clip, and
on a failed clip one tap for the first step that broke (evals.results.CAUSES; no "judge"
choice: its misses are counted from its saved verdicts). Words are optional on every tap.

Each tap block saves one document to the page's db collection "grades", its id the
grades.json key it fills ("<ad>__whole-ad", "<ad>__scene-N", "<ad>__scene-N__kept") and its
data {"grade", "cause", "note", "at"}: the collection read back as {id: data} IS the run's
grades.json. From backend/:

    uv run python -m evals.grading_page <run folder>   # writes <run folder>/grading.html

Publish grading.html as an artifact with the db capability and the clips as its files, at
the paths the page names (<ad>/ad.mp4, <ad>/scene-N/first.mp4, remake-K.mp4).
"""

import html
import json
import sys
from pathlib import Path
from typing import Any

from evals.results import CAUSES, grade_key
from evals.round_export import check_folder, clip_name

COLLECTION = "grades"
CAUSE_LABELS = {
    "planner": "Planner",
    "start_picture": "Start picture",
    "motion_prompt": "Motion prompt",
    "video_model": "Video model",
}
CHECK_WORDS = {
    "sides": "impossible sides or parts",
    "real_motion": "unreal motion",
    "sudden_change": "change where nothing touched",
    "does_what_was_ordered": "not the ordered action",
}
assert set(CAUSE_LABELS) == set(CAUSES)


def _esc(text: Any) -> str:
    return html.escape(str(text or ""))


def _verdict(clip: dict[str, Any]) -> str:
    ruling = clip.get("ruling") or {}
    verdict = ruling.get("verdict")
    if verdict == "pass":
        return '<span class="chip pass">judge: pass</span>'
    if verdict == "fail":
        failed = [
            CHECK_WORDS.get(c, c) for c, r in (ruling.get("checks") or {}).items() if r == "fail"
        ]
        return f'<span class="chip fail">judge: fail ({_esc(", ".join(failed))})</span>'
    if verdict == "not_checked":
        return '<span class="chip unchecked">judge: couldn\'t check</span>'
    return '<span class="chip unchecked">judge: not run</span>'


def _taps(doc: str, title: str, causes: bool) -> str:
    cause_row = ""
    if causes:
        buttons = "".join(
            f'<button type="button" data-v="{key}">{label}</button>'
            for key, label in CAUSE_LABELS.items()
        )
        cause_row = f"""
      <div class="q cause" data-field="cause" hidden>
        <span class="qt">First step that broke?</span>
        <span class="opts">{buttons}</span>
      </div>"""
    return f"""
    <div class="grade" data-doc="{_esc(doc)}">
      <div class="q" data-field="grade">
        <span class="qt">{_esc(title)}</span>
        <span class="opts"><button type="button" data-v="pass">Pass</button>
          <button type="button" data-v="fail">Fail</button></span>
      </div>{cause_row}
      <div class="note">
        <textarea rows="2" aria-label="Your words for: {_esc(title)}"
          placeholder="Your words (optional)"></textarea>
        <button class="save" type="button">Save words</button>
        <span class="saved" hidden>Saved</span>
      </div>
    </div>"""


def _scene_card(ad: str, result: dict[str, Any]) -> str:
    number = result["scene"]
    kept = result.get("kept") or {}
    figures = []
    for clip in result["tries"]:
        name = clip_name(clip.get("remake"))
        label = "First clip" if clip.get("remake") is None else f"Remake {clip['remake']}"
        badge = (
            '<span class="kept">app kept this</span>'
            if clip.get("remake") == kept.get("remake") and len(result["tries"]) > 1
            else ""
        )
        saw = "".join(f"<li>{_esc(p)}</li>" for p in clip.get("judge_saw") or [])
        figures.append(f"""
      <figure class="clip">
        <figcaption><strong>{label}</strong> {badge}</figcaption>
        <video controls preload="metadata" playsinline
          src="{_esc(ad)}/scene-{number}/{name}.mp4"></video>
        <div>{_verdict(clip)}</div>
        {f'<ul class="problem">{saw}</ul>' if saw else ""}
      </figure>""")
    grades = _taps(grade_key(ad, number), "First clip: good?", causes=True)
    if result.get("kept_is_remake"):
        grades += _taps(
            grade_key(ad, number, kept=True),
            f"Kept clip (remake {kept.get('remake')}): good?",
            causes=True,
        )
    return f"""
  <section class="card scene-card" id="{_esc(ad)}-scene-{number}">
    <header>
      <h3>Scene {number} <span class="scene">{_esc(result.get("broll_kind"))}</span></h3>
      <p class="muted">Says: {_esc(result.get("line"))}</p>
      <p class="muted">Shows: {_esc(result.get("shows"))}</p>
    </header>
    <div class="clips">{"".join(figures)}
    </div>
    <div class="questions">{grades}
    </div>
  </section>"""


def page(run: Path) -> str:
    """The page for the run folder `run`."""
    results: dict[str, dict[str, Any]] = json.loads((run / "results.json").read_text())
    ads: dict[str, list[dict[str, Any]]] = {}
    for key, result in results.items():
        ad = check_folder(result.get("ad_folder") or key.rsplit(":", 1)[0])
        ads.setdefault(ad, []).append(result)
    sections = []
    for ad, scenes in ads.items():
        cards = "".join(_scene_card(ad, r) for r in sorted(scenes, key=lambda r: r["scene"]))
        sections.append(f"""
  <section class="ad" id="{_esc(ad)}">
    <h2>{_esc(ad)}</h2>
    <div class="card">
      <video class="whole" controls preload="metadata" playsinline src="{_esc(ad)}/ad.mp4"></video>
      <div class="questions">{_taps(grade_key(ad), "Whole ad: good?", causes=False)}
      </div>
    </div>{cards}
  </section>""")
    return (
        TEMPLATE.replace("{{SECTIONS}}", "".join(sections))
        .replace("{{LOGIC}}", LOGIC)
        .replace("{{COLLECTION}}", COLLECTION)
    )


TEMPLATE = (Path(__file__).parent / "grading_page.html").read_text()
LOGIC = (Path(__file__).parent / "grading_page.js").read_text()


def main(args: list[str]) -> None:
    run = Path(args[0])
    (run / "grading.html").write_text(page(run))
    print(f"wrote {run / 'grading.html'}")


if __name__ == "__main__":
    main(sys.argv[1:])
