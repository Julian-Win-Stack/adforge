"""Julian's page for grading a decider run (fix redesign ticket 07), built from the run's
folder: per case, the shop owner's messages, every picture the decider looked at with the
label it was shown, its decide answer and the plan the chat would show, what the producer
decided on the same note in the past run and Julian's grade of it, and the expected answer,
marked as Claude's. A Right / Wrong tap and words per case, saved to the page's db collection
"grades" by the round grading page's own logic (evals/grading_page.js), one document per case
id. Pictures are shrunk and put in the page, so it is one file.

    uv run python -m evals.decider page <run folder>   # writes <run folder>/grading.html
"""

import base64
import html
import io
import json
from pathlib import Path
from typing import Any

import PIL.Image

from evals.grading_page import COLLECTION, LOGIC, TEMPLATE

from .score import counts

TITLE = "Decider Grading"
LEAD = (
    "Each case is one note from a shop owner, sent to the new decider. You see what it looked "
    "at, what it decided, what the old producer decided on the same note, and your earlier "
    "grade. The expected answer is Claude's guess, not a grade. Tap Right or Wrong on the "
    "decider's answer; words are optional."
)
THUMB = 300
FRAME_THUMB = 120


def page(run: Path) -> str:
    results: list[dict[str, Any]] = json.loads((run / "results.json").read_text())["cases"]
    sections = [_summary(run, results)] + [_case(run, result) for result in results]
    made = (
        TEMPLATE.replace("<title>Round Grading</title>", f"<title>{TITLE}</title>")
        .replace("<h1>Round Grading</h1>", f"<h1>{TITLE}</h1>")
        .replace(_old_lead(), f'<p class="lead">{_esc(LEAD)}</p>')
        .replace("{{SECTIONS}}", "\n".join(sections))
        .replace("{{LOGIC}}", LOGIC)
        .replace("{{COLLECTION}}", COLLECTION)
    )
    return made.replace("</style>", EXTRA_STYLE + "</style>", 1)


def _old_lead() -> str:
    start = TEMPLATE.index('<p class="lead">')
    return TEMPLATE[start : TEMPLATE.index("</p>", start) + len("</p>")]


EXTRA_STYLE = """
.pics { display: flex; flex-wrap: wrap; gap: 8px; }
.pic { margin: 0; display: grid; gap: 4px; width: 220px; max-width: 100%; }
.pic img { width: 100%; height: auto; border-radius: 6px; border: 1px solid var(--line); }
.frames { display: flex; flex-wrap: wrap; gap: 4px; }
.frames figure { margin: 0; width: 110px; display: grid; gap: 2px; }
.frames img { width: 100%; border-radius: 4px; }
.pic figcaption, .frames figcaption { font-size: 0.75rem; color: var(--muted); }
.said { margin: 0; padding: 10px 12px; border-left: 3px solid var(--accent);
  background: var(--bg); }
.kv { display: grid; grid-template-columns: max-content 1fr; gap: 4px 12px; font-size: 0.9rem; }
.kv dt { color: var(--muted); } .kv dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
details summary { cursor: pointer; color: var(--muted); }
table.counts { border-collapse: collapse; font-size: 0.9rem; }
table.counts td, table.counts th { border-bottom: 1px solid var(--line); padding: 4px 10px;
  text-align: left; }
.same { color: var(--pass); } .differs { color: var(--fail); }
"""


def _esc(text: Any) -> str:
    return html.escape(str(text if text is not None else ""))


def _summary(run: Path, results: list[dict[str, Any]]) -> str:
    meta = json.loads((run / "results.json").read_text())
    rows = "".join(
        f"<tr><td>{_esc(kind)}</td><td>{same} of {total}</td></tr>"
        for kind, (same, total) in sorted(counts(results).items())
    )
    movement = [r for r in results if r.get("movement")]
    with_findings = sum(1 for r in movement if r["judge_findings_calls"])
    # Runs recorded before these counts were kept have none.
    judged = (
        f" {sum(r['judge_findings_judged'] for r in results)} of its"
        f" {sum(r['judge_findings_calls'] for r in results)} calls found a clip a judge had"
        " judged: the replayed ads' clips weren't judged (their video is faked), so most"
        " findings were empty."
        if all("judge_findings_judged" in r for r in results)
        else ""
    )
    decided = [row for r in results for row in r.get("rows", [])]
    looked = (
        f"Scenes decided after a look at that scene: {sum(r['looked'] for r in decided)} of"
        f" {len(decided)}. "
        if all("rows" in r for r in results)
        else ""
    )
    refused = sum(1 for r in results for each in r["refused"] if each["tool"] == "decide")
    red = sum(len(r["red_notices"]) for r in results)
    failed = meta.get("failed") or []
    return f"""
  <section class="card">
    <header><h2>Run {_esc(run.name)}</h2>
      <p class="muted">Model {_esc(meta.get("model"))}. {len(results)} cases. Spent
      ${_esc(meta.get("spend_usd"))} on OpenAI credits. Red notices: {red}.</p></header>
    <p class="muted">Matches with Claude's expected answers (not grades):</p>
    <table class="counts"><tr><th>Check</th><th>Same as expected</th></tr>{rows}</table>
    <p class="muted">Judge's findings looked at on {with_findings} of {len(movement)} notes
    about movement.{judged}</p>
    <p class="muted">{looked}Decide answers refused by code: {refused}.</p>
    {f'<p class="differs">Cases that didn\'t run: {_esc(", ".join(failed))}</p>' if failed else ""}
  </section>"""


def _case(run: Path, result: dict[str, Any]) -> str:
    folder = run / result["id"]
    calls = json.loads((folder / "tool_calls.json").read_text())
    title = result["id"]
    tag = " · ".join(
        each for each in [result.get("layer"), result.get("rule"), result.get("side")] if each
    )
    turns = "".join(_turn(folder, turn) for turn in result["turns"])
    seen: set[str] = set()
    looks = "".join(
        _look(folder, call, seen) for call in calls if call["pictures"] or call["refused"]
    )
    past = result.get("past") or {}
    past_rows = "".join(
        f"<dt>{_esc(key)}</dt><dd>{_esc(value)}</dd>" for key, value in past.items() if value
    )
    checks = "".join(
        f'<li class="{"same" if check["same"] else "differs"}">turn {check["turn"]}, '
        f"{_esc(check['what'])}: expected {_esc(check['expected'])}, got "
        f"{_esc(check['got'])}</li>"
        for check in result["checks"]
    )
    return f"""
  <section class="card" id="{_esc(title)}">
    <header><h2>{_esc(title)}<span class="scene">{_esc(tag)}</span></h2>
      <p class="status">ad #{_esc(result.get("ad"))} · ${_esc(result["spend_usd"])} ·
      {len(calls)} tool calls</p></header>
    {turns}
    <h3>What it looked at</h3>
    {looks or '<p class="muted">Nothing.</p>'}
    {f'<h3>Before the redesign</h3><dl class="kv">{past_rows}</dl>' if past_rows else ""}
    <details><summary>Claude's expected answer and how it compares (not a grade)</summary>
      <ul>{checks or "<li>No expected answer.</li>"}</ul></details>
    {_taps(title)}
  </section>"""


def _turn(folder: Path, turn: dict[str, Any]) -> str:
    photos = f" (+{len(turn['photos'])} photo)" if turn["photos"] else ""
    sent = "".join(
        _figure(folder, {"label": f"Sent: {Path(file).name}", "file": file}, frame=False)
        for file in turn.get("photo_files", [])
    )
    answer = {
        "planned": "Planned a fix",
        "not_a_fix": "Not a fix note: handed to the producer",
        "yes": "Read it as yes to the plan",
        "no": "Read it as no to the plan",
        "go_ahead": "Go ahead: scene "
        + ", ".join(str(each) for each in turn.get("gone_ahead", []))
        + " carries on with its new version",
        "no_decision": f"Stopped without an answer ({turn.get('why')})",
        "error": f"Stopped on an error ({turn.get('why')})",
    }.get(turn["outcome"], turn["outcome"])
    plan = turn.get("plan") or {}
    rows = "".join(_row(row) for row in plan.get("scenes", []))
    cants = "".join(f"<li>{_esc(cant['text'])}</li>" for cant in plan.get("cants", []))
    decide = turn.get("decide") or {}
    things = "".join(
        f"<li>Owner photo {each['photo']}: {_esc(each['thing'])}"
        f"{'' if each['clear'] else ' (not clear)'}</li>"
        for each in decide.get("photos", [])
    )
    return f"""
    <div><p class="said"><strong>Owner{photos}:</strong> {_esc(turn["said"])}</p>
      {f'<div class="pics">{sent}</div>' if sent else ""}
      <p><strong>{_esc(answer)}</strong></p>
      {f"<ul>{rows}</ul>" if rows else ""}
      {f"<ul>{things}</ul>" if things else ""}
      {f"<p>Can't: </p><ul>{cants}</ul>" if cants else ""}
      {f"<p>Question: {_esc(plan['question'])}</p>" if plan.get("question") else ""}
    </div>"""


def _row(row: dict[str, Any]) -> str:
    action = row["action"].replace("_", " ")
    if row["action"] == "go_back":
        action += f" to v{row['go_back_to']}"
    said = row["change"] or ""
    if row["line"]:
        said += f' New line: "{row["line"]}"'
    photos = "; ".join(f"photo {p['photo']} ({p['thing']})" for p in row["photos"])
    cant = f" Can't: {row['cant']}" if row["cant"] else ""
    return (
        f"<li>Scene {row['scene']}: <strong>{_esc(action)}</strong>. {_esc(said)}{_esc(cant)}"
        f"{f'<br><span class=muted>{_esc(photos)}</span>' if photos else ''}</li>"
    )


def _look(folder: Path, call: dict[str, Any], seen: set[str]) -> str:
    """One tool call with the pictures it showed. Pictures it already showed earlier in the
    case are named, not shown again, so a case that looks many times stays small."""
    asked = ", ".join(f"{key} {value}" for key, value in call["arguments"].items())
    said = f'<p class="muted">{_esc(call["tool"])} ({_esc(asked)}): {_esc(call["result"])}</p>'
    if call["refused"]:
        return said
    new = [p for p in call["pictures"] if p["file"] not in seen]
    seen.update(p["file"] for p in call["pictures"])
    if not new:
        return f"""
    <div>{said}<p class="muted">The same {len(call["pictures"])} pictures as above.</p></div>"""
    frames = call["tool"] == "look_at_clip"
    figures = "".join(_figure(folder, p, frames) for p in new)
    return f"""
    <div>{said}<div class="{"frames" if frames else "pics"}">{figures}</div></div>"""


def _figure(folder: Path, picture: dict[str, str], frame: bool) -> str:
    label = picture["label"]
    # A frame's caption is its time; its whole label is the picture's alt text.
    caption = label.rsplit(", ", 1)[-1] if frame else label
    source = _inline(folder / picture["file"], FRAME_THUMB if frame else THUMB)
    return (
        f'<figure{"" if frame else " class=pic"}><img alt="{_esc(label)}" src="{source}">'
        f"<figcaption>{_esc(caption)}</figcaption></figure>"
    )


def _inline(path: Path, width: int) -> str:
    """The picture shrunk to `width` as a JPEG in the page, small enough for a page of a whole
    run's pictures."""
    picture = PIL.Image.open(path).convert("RGB")
    picture.thumbnail((width, width * 2))
    made = io.BytesIO()
    picture.save(made, format="JPEG", quality=65)
    return "data:image/jpeg;base64," + base64.b64encode(made.getvalue()).decode()


def _taps(case_id: str) -> str:
    return f"""
    <div class="grade" data-doc="{_esc(case_id)}">
      <div class="q" data-field="grade">
        <span class="qt">The decider's answer:</span>
        <span class="opts"><button type="button" data-v="pass">Right</button>
          <button type="button" data-v="fail">Wrong</button></span>
      </div>
      <div class="note">
        <textarea rows="2" aria-label="Your words on {_esc(case_id)}"
          placeholder="Your words (optional)"></textarea>
        <button class="save" type="button">Save words</button>
        <span class="saved" hidden>Saved</span>
      </div>
    </div>"""


def write(run: Path) -> Path:
    made = run / "grading.html"
    made.write_text(page(run))
    return made
