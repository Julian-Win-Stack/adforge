"""Julian's per-ad "what the agent did" page (spec #247 point 17), built from the ad's
tool_calls.json and ad.json that evals.round_export wrote: every tool call after the ad's first
clip, in call order, each with its agent and tool, its `why`, the words said before it, the
instruction it was given, its answer, its fold line, cost, started and finished, and the look's
answer in words. The round grading page links each ad to it. From backend/:

    uv run python -m evals.agent_page <run folder> <ad folder>   # writes <ad folder>/agent.html

evals.grading_page writes every ad's page with the round's; publish each as a file of the
grading artifact at <ad>/agent.html, with the pictures at <ad>/tool-call-pictures/.
"""

import html
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from evals.round_export import check_folder

FILE = "agent.html"
TITLE = "What The Agent Did"
ANSWER_WORDS = {"made": "made", "cant": "can't", "question": "question"}
EXTRA_STYLE = """
.calls { list-style: none; margin: 0; padding: 0; display: grid; gap: 14px; }
.kv { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 4px 12px; margin: 0; }
.kv dt { color: var(--muted); } .kv dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.words { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; }
.fold { font-family: var(--mono); font-size: 0.85rem; background: var(--kept); padding: 6px 10px;
  border-radius: 6px; overflow-wrap: anywhere; }
.look { margin: 0; padding: 8px 12px; border-left: 3px solid var(--accent); background: var(--bg);
  white-space: pre-wrap; overflow-wrap: anywhere; }
.back { color: var(--accent); }
"""


def _esc(text: Any) -> str:
    return html.escape(str(text if text is not None else ""))


def _style() -> str:
    """The round grading page's fonts and colours, so both pages read as one."""
    from evals.grading_page import TEMPLATE

    start = TEMPLATE.index("<link ")
    end = TEMPLATE.index("</style>")
    return TEMPLATE[start:end] + EXTRA_STYLE + "</style>"


def _when(at: str | None) -> str:
    return at.replace("T", " ")[:19] + " UTC" if at else "not finished"


def _chip(answer: str, refused: bool) -> str:
    if not answer:
        return '<span class="chip unchecked">not finished</span>'
    kind = "fail" if refused or answer == "cant" else "pass" if answer == "made" else "unchecked"
    return f'<span class="chip {kind}">{_esc(ANSWER_WORDS.get(answer, answer))}</span>'


def _instruction(arguments: dict[str, Any]) -> str:
    if not arguments:
        return '<span class="muted">none</span>'
    return "<br>".join(
        f"{_esc(key)}: "
        + _esc(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
        for key, value in arguments.items()
    )


def _call(number: int, call: dict[str, Any]) -> str:
    rows = [("Why", _esc(call["why"]) or '<span class="muted">none given</span>')]
    if call["said"]:
        rows.append(("Said before", f'<p class="words">{_esc(call["said"])}</p>'))
    rows.append(("Instruction", _instruction(call["arguments"])))
    rows.append(("Answer", f'<p class="words">{_esc(call["result"])}</p>'))
    if call["pictures"]:
        rows.append(
            (
                "Pictures",
                "<br>".join(
                    f'<a href="{_esc(p["file"])}">{_esc(p["label"])}</a>' for p in call["pictures"]
                ),
            )
        )
    looks = "".join(
        f'<p class="look"><strong>The look ({_esc(each["model"])}):</strong>\n'
        f"{_esc(each['words'])}</p>"
        for each in call["model_calls"]
        if each["words"]
    )
    fold = f'<p class="fold">Folded to: {_esc(call["fold_line"])}</p>' if call["fold_line"] else ""
    paid = ", ".join(
        f"{each['purpose']} ${each['cost_usd'] or '?'}"
        + ("" if each["outcome"] == "succeeded" else f" ({each['outcome']})")
        for each in call["model_calls"]
    )
    table = "".join(f"<dt>{label}</dt><dd>{value}</dd>" for label, value in rows)
    return f"""
    <li class="card" id="call-{number}">
      <header><h3>{number}. {_esc(call["tool"])}<span class="scene">{_esc(call["agent"])}</span>
        {_chip(call["answer"], call["refused"])}</h3>
        <p class="status">${_esc(call["cost_usd"])} · started {_esc(_when(call["created_at"]))}
        · finished {_esc(_when(call["finished_at"]))}</p></header>
      <dl class="kv">{table}</dl>{looks}{fold}
      {f'<p class="muted">Model calls: {_esc(paid)}</p>' if paid else ""}
    </li>"""


def page(run: Path, ad: str) -> str:
    """The page of the ad folder `ad` in the run folder `run`."""
    base = run / check_folder(ad)
    calls: list[dict[str, Any]] = json.loads((base / "tool_calls.json").read_text())
    after = json.loads((base / "ad.json").read_text()).get("first_clip_at")
    cost = sum((Decimal(call["cost_usd"]) for call in calls), Decimal(0))
    cut = (
        f"after its first clip ({_esc(_when(after))})"
        if after
        else "from the start: no clip was made, so every call is listed"
    )
    items = "".join(_call(number, call) for number, call in enumerate(calls, 1))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{TITLE}</title>
{_style()}
</head>
<body>
<div class="wrap">
  <header>
    <h1>{TITLE}: {_esc(ad)}</h1>
    <p class="lead">Every tool call of the ad {cut}, in call order: {len(calls)} calls,
    ${cost} of the tool calls' own model calls (the agents' turns are not included; costs.json
    has the ad's whole cost).</p>
    <p><a class="back" href="../grading.html#{_esc(ad)}">Back to the round</a></p>
  </header>
  {f'<ol class="calls">{items}</ol>' if items else '<p class="muted">No tool calls.</p>'}
</div>
</body>
</html>
"""


def write(run: Path, ad: str) -> Path:
    """Write the ad's page into its folder; gives its path."""
    path = run / check_folder(ad) / FILE
    path.write_text(page(run, ad))
    return path


def main(args: list[str]) -> None:
    print(f"wrote {write(Path(args[0]), args[1])}")


if __name__ == "__main__":
    main(sys.argv[1:])
