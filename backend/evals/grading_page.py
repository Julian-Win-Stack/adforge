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

It also writes each ad's "what the agent did" page (evals.agent_page) as <ad>/agent.html, which
the ad links to when its export has a tool_calls.json.

Publish grading.html as an artifact with the db capability and the clips as its files, at
the paths the page names (<ad>/ad.mp4, <ad>/scene-N/first.mp4, remake-K.mp4, <ad>/agent.html
and its <ad>/tool-call-pictures/).
"""

import html
import json
import sys
from pathlib import Path
from typing import Any

from evals import agent_page
from evals.results import CAUSES, grade_key
from evals.round_export import check_folder, clip_name
from jobs.library import VIDEO_API_MOST_PICTURES

COLLECTION = "grades"
CAUSE_LABELS = {
    "planner": "Planner",
    "start_picture": "Start picture",
    "motion_prompt": "Motion prompt",
    "video_model": "Video model",
}
CHECK_WORDS = {
    "sides": "impossible sides or parts",
    "product": "not the real product",
    "real_motion": "unreal motion",
    "sudden_change": "change where nothing touched",
    "does_what_was_ordered": "not the ordered action",
}
assert set(CAUSE_LABELS) == set(CAUSES)
# A left-out picture's tier is its reason (jobs.library.PictureRef.tier).
TIER_WORDS = {
    1: "the front look photo",
    2: "a scene picture the planner named",
    3: "the drawn picture",
    4: "another side photo",
}


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


def _words(value: Any) -> str:
    return str(value or "").replace("_", " ")


def _picture(picture: dict[str, Any]) -> str:
    """'#N label' for a library picture, 'drawn <which> label' for a drawn one."""
    if picture.get("library_number") is not None:
        where = f"#{picture['library_number']}"
    else:
        where = f"drawn {_words(picture.get('drawn'))}"
    used_for = f" (for {_esc(picture['for'])})" if picture.get("for") else ""
    return f"{_esc(where)} {_esc(picture.get('label'))}{used_for}"


def _headed(head: str, items: str) -> str:
    return f'<p class="sent-head">{head}</p><ul class="sent-list">{items}</ul>' if items else ""


def _sent(clip: dict[str, Any]) -> str:
    """What the clip was sent: the pictures, any left out with the reason, the pieces and the
    recolours. Nothing for a clip made before cut-and-send."""
    sent = clip.get("sent")
    if not sent:
        return ""
    pictures = "".join(
        f"<li>Image {p['image']}: {_picture(p)}</li>" for p in sent.get("pictures") or []
    )
    pieces = "".join(
        f"<li>Video {p['number']}: page video {p['video']}, moment {p['moment']}, "
        f"{p['start']:g}-{p['end']:g} s"
        f"{f' (trimmed: {_esc(_words(p["trim"]))})' if p.get('trim') else ''}</li>"
        for p in sent.get("pieces") or []
    )
    recolours = "".join(
        f"<li>#{r['of']} to {_esc(r['colour'])}: "
        f"{'done, #' + str(r['made']) if r['outcome'] == 'done' else _esc(_words(r['outcome']))}"
        "</li>"
        for r in sent.get("recolours") or []
    )
    left = sent.get("left_out") or []
    left_out = ""
    if left:
        names = "".join(
            f"<li>{_picture(p)}: past the {VIDEO_API_MOST_PICTURES}-picture cap, tier "
            f"{p['tier']} ({_esc(TIER_WORDS.get(p['tier'], ''))})</li>"
            for p in left
        )
        left_out = f"""
          <p class="left-out-head">Left out: {len(left)}</p>
          <ul class="left-out">{names}</ul>"""
    return f"""
        <details class="sent" open>
          <summary>Sent to the video model: {len(sent.get("pictures") or [])} pictures</summary>
          <ol class="sent-list">{pictures}</ol>{left_out}
          {_headed("Pieces", pieces)}
          {_headed("Recolours", recolours)}
        </details>"""


def _left_out_count(results: dict[str, dict[str, Any]]) -> str:
    """How many clips had pictures left out, of the clips with a sent list; nothing when no
    clip has one."""
    sent = [
        clip["sent"]
        for result in results.values()
        for clip in result.get("tries") or []
        if clip.get("sent")
    ]
    if not sent:
        return ""
    capped = sum(1 for s in sent if s.get("left_out"))
    return (
        f'<p class="count{" capped" if capped else ""}" id="left-out-count">'
        f"Clips with pictures left out: {capped} of {len(sent)} sent.</p>"
    )


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
            if kept and clip.get("remake") == kept.get("remake") and len(result["tries"]) > 1
            else ""
        )
        saw = "".join(f"<li>{_esc(p)}</li>" for p in clip.get("judge_saw") or [])
        figures.append(f"""
      <figure class="clip">
        <figcaption><strong>{label}</strong> {badge}</figcaption>
        <video controls preload="metadata" playsinline
          src="{_esc(ad)}/scene-{number}/{name}.mp4"></video>
        <div>{_verdict(clip)}</div>
        {f'<ul class="problem">{saw}</ul>' if saw else ""}{_sent(clip)}
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


def _agent_link(run: Path, ad: str) -> str:
    """The link to the ad's "what the agent did" page; nothing for a run exported before it."""
    if not (run / ad / "tool_calls.json").exists():
        return ""
    return (
        f'\n    <p><a href="{_esc(ad)}/{agent_page.FILE}">What the agent did</a>: every tool '
        "call after the first clip, with its why, answer and cost.</p>"
    )


def ads_of(run: Path) -> list[str]:
    """The run's ad folders that have a tool-call record, an ad with no clip graded too."""
    return sorted(each.parent.name for each in run.glob("*/tool_calls.json"))


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
    <h2>{_esc(ad)}</h2>{_agent_link(run, ad)}
    <div class="card">
      <video class="whole" controls preload="metadata" playsinline src="{_esc(ad)}/ad.mp4"></video>
      <div class="questions">{_taps(grade_key(ad), "Whole ad: good?", causes=False)}
      </div>
    </div>{cards}
  </section>""")
    return (
        TEMPLATE.replace("{{SECTIONS}}", "".join(sections))
        .replace("{{COUNT}}", _left_out_count(results))
        .replace("{{LOGIC}}", LOGIC)
        .replace("{{COLLECTION}}", COLLECTION)
    )


TEMPLATE = (Path(__file__).parent / "grading_page.html").read_text()
LOGIC = (Path(__file__).parent / "grading_page.js").read_text()


def main(args: list[str]) -> None:
    run = Path(args[0])
    (run / "grading.html").write_text(page(run))
    print(f"wrote {run / 'grading.html'}")
    for ad in ads_of(run):
        print(f"wrote {agent_page.write(run, ad)}")


if __name__ == "__main__":
    main(sys.argv[1:])
