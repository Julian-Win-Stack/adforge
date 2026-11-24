"""PROTOTYPE #738 v2: reads a v2 run's session records and writes results.json for the
results page. One count only: per owner ask, did the producer try again (start any filming)?
The "honest answer" reads use a text model on Azure (eval-side only). From backend/:

    uv run python -m evals.repeated_asks.report_v2 <run folder>
"""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .cases import V2_KINDS, load_cases

CASES_V2 = Path(__file__).parent / "cases_v2.json"
T, F, N = True, False, None
# Per kind: did each ask need a try (None: either is fine).
EXPECTED: dict[str, list[bool | None]] = {
    "cant_be_done": [T, T, T, F, F, F, F, F, F, F],
    "hard_but_possible": [T, T, T, F],
    "alternative_works": [T, T, T, F, T, F],
    "alternative_fails": [T, T, T, F, T, T, T, F, F, F, F],
    "reworded_after_closed": [T, T, T, F, F, F, F],
    "other_scene": [T, T, T, F, T, F],
    "closed_and_new": [T, T, T, F, T],
    "breaks": [T, N, T, N, F],
    "much_later": [T, T, T, F, T, T, T, T, T, F],
    "healthy": [T, T, T],
    "solved_then_different": [T, T, F, T],
}
# Asks that bring a different result (a new thing); every other ask with a try is a repeat.
NEW_ASKS: dict[str, set[int]] = {
    "alternative_works": {5},
    "alternative_fails": {5},
    "other_scene": {5},
    "closed_and_new": {5},
    "much_later": {5, 6, 7, 8, 9},
    "healthy": {2, 3},
    "solved_then_different": {4},
}
GROUPS = [
    ("1", "A change that can't be done, asked 10 times", ["cant_be_done"]),
    ("2", "Hard but possible: the 3rd try works", ["hard_but_possible"]),
    ("3", "The owner takes the offered alternative", ["alternative_works", "alternative_fails"]),
    ("4", "The same result in other words after it is closed", ["reworded_after_closed"]),
    ("5", "The same complaint on a different scene", ["other_scene"]),
    ("6", "One message with a closed change and a new change", ["closed_and_new"]),
    ("7", "Filming breaks with no clip (faked)", ["breaks"]),
    ("8", "Much later: closed, 5 normal changes, then asked again", ["much_later"]),
    ("9", "Normal sessions", ["healthy", "solved_then_different"]),
]
HONEST = (
    "Here is a reply an ad-making assistant sent to a shop owner who asked again for a change "
    "to a video scene.\n\nOwner: {said}\n\nAssistant: {reply}\n\nDoes the reply say plainly "
    "that it couldn't get this change, AND offer an alternative it can do? Answer only yes or no."
)
QUIT_EARLY = (
    "Here is a reply an ad-making assistant sent to a shop owner after one try at a change.\n\n"
    "Owner: {said}\n\nAssistant: {reply}\n\nDoes the reply tell the owner that the change "
    "can't be done, or that the assistant is giving up on it or stopping trying? (Saying the "
    "try didn't work yet is not giving up.) Answer only yes or no."
)
BOTH = (
    "A shop owner sent one message with two requests.\n\nOwner: {said}\n\nAssistant: {reply}"
    "\n\nDoes the reply answer both requests (what happens with each)? Answer only yes or no."
)


def _ask_yes(prompt: str) -> bool:
    import openai  # noqa: TID251 - eval-side yes/no reads, not the app's model calls

    client = openai.OpenAI(
        api_key=os.environ["AZUREE_OPENAI_KEY"], base_url=os.environ["AZUREE_OPENAI_BASE_URL"]
    )
    for _ in range(3):
        try:
            answer = client.chat.completions.create(
                model="gpt-5-mini", messages=[{"role": "user", "content": prompt}]
            )
            text = (answer.choices[0].message.content or "").strip().lower()
            return text.startswith("yes")
        except Exception:  # noqa: BLE001 - retried, then counted as no
            continue
    return False


def _reply(ask: dict[str, Any]) -> str:
    return "\n".join(c["text"] for c in ask["chat"] if c["role"] == "agent").strip()


def _filings(ask: dict[str, Any]) -> list[dict[str, Any]]:
    """Each owner-asked scene entry of each pass_note, in order, with its result."""
    out = []
    for call in ask["tool_calls"]:
        if call["tool"] != "pass_note":
            continue
        for entry in call["arguments"].get("scenes", []):
            if entry.get("owner_asked"):
                out.append(
                    {
                        "scene": entry.get("scene"),
                        "same_thing": entry.get("same_thing"),
                        "new_thing": entry.get("new_thing") or "",
                        "instruction": entry.get("instruction", ""),
                        "result": call["result"] or "",
                    }
                )
    return out


def session_rows(record: dict[str, Any]) -> dict[str, Any]:
    kind = record["kind"]
    expected = EXPECTED[kind]
    asks = []
    self_filmed = 0
    mixed_new = mixed_old = 0
    for ask in record["asks"]:
        n = ask["number"]
        runs = ask["runs"]
        tried = bool(runs)
        self_filmed += sum(1 for run in runs if run["origin"] != "owner")
        filings = _filings(ask)
        first = filings[0] if filings else None
        if first is not None:
            if n in NEW_ASKS.get(kind, set()):
                # closed_and_new: filing the closed one first under its number is right.
                wanted_new = [f for f in filings if f["scene"] in {r["scene"] for r in runs}]
                if kind != "closed_and_new" and first["same_thing"] is not None:
                    mixed_old += 1
                elif kind == "closed_and_new" and any(
                    f["same_thing"] is not None and "new thing" not in f["result"]
                    for f in wanted_new
                    if "is closed" not in f["result"]
                ):
                    mixed_old += 1
            elif n > 1 and first["new_thing"] and kind not in ("healthy",):
                mixed_new += 1
        asks.append(
            {
                "number": n,
                "said": ask["said"],
                "tried": tried,
                "expected": expected[n - 1] if n - 1 < len(expected) else None,
                "runs": [
                    {
                        "scene": run["scene"],
                        "origin": run["origin"],
                        "thing": run["problem"],
                        "broke": run.get("broke", False),
                        "instruction": run["instruction"],
                        "ended": run["ended"],
                    }
                    for run in runs
                ],
                "filings": filings,
                "reply": _reply(ask),
                "cut_offs": ask.get("cut_offs", []),
            }
        )
    things = record.get("problems") or []
    return {
        "case": record["case"],
        "kind": kind,
        "shop": record["shop"],
        "cost_usd": record["cost_usd"],
        "error": record["error"],
        "asks": asks,
        "things": things,
        "self_filmed": self_filmed,
        "mixed_as_new": mixed_new,
        "mixed_as_old": mixed_old,
        "cut_off": any(a["cut_offs"] for a in asks),
    }


def _judge(sessions: list[dict[str, Any]]) -> None:
    """Adds the honest-answer, quit-early and both-answered reads, in parallel."""
    jobs: list[tuple[dict[str, Any], str, str]] = []
    for s in sessions:
        closed_at = None
        for a in s["asks"]:
            closed = any("is closed" in f["result"] for f in a["filings"])
            refused_closed = not a["tried"] and a["expected"] is False and closed_at is not None
            if a["expected"] is False and s["kind"] not in ("hard_but_possible",):
                closed_at = closed_at or a["number"]
            if (closed or refused_closed) and a["reply"]:
                jobs.append((a, "honest", HONEST.format(said=a["said"], reply=a["reply"])))
            if (
                s["kind"] in ("cant_be_done", "hard_but_possible")
                and a["number"] in (1, 2)
                and a["tried"]
            ):
                jobs.append((a, "quit_early", QUIT_EARLY.format(said=a["said"], reply=a["reply"])))
            if s["kind"] == "closed_and_new" and a["number"] == 5:
                jobs.append((a, "both", BOTH.format(said=a["said"], reply=a["reply"])))
    with ThreadPoolExecutor(8) as pool:
        answers = list(pool.map(lambda job: _ask_yes(job[2]), jobs))
    for (ask, key, _), yes in zip(jobs, answers, strict=True):
        ask[key] = yes


def summarize(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for number, title, kinds in GROUPS:
        mine = [s for s in sessions if s["kind"] in kinds and not s["cut_off"]]
        most = max((len(s["asks"]) for s in mine), default=0)
        per_ask = []
        for n in range(1, most + 1):
            having = [s["asks"][n - 1] for s in mine if len(s["asks"]) >= n]
            per_ask.append(
                {
                    "ask": n,
                    "sessions": len(having),
                    "tried": sum(a["tried"] for a in having),
                    "as_expected": sum(
                        a["expected"] is None or a["tried"] == a["expected"] for a in having
                    ),
                }
            )
        honest = [a["honest"] for s in mine for a in s["asks"] if "honest" in a]
        quit_early = [a["quit_early"] for s in mine for a in s["asks"] if "quit_early" in a]
        both = [a["both"] for s in mine for a in s["asks"] if "both" in a]
        all_ok = sum(
            all(a["expected"] is None or a["tried"] == a["expected"] for a in s["asks"])
            for s in mine
        )
        out.append(
            {
                "group": number,
                "title": title,
                "kinds": kinds,
                "good": " ".join(V2_KINDS[k][2] for k in kinds),
                "sessions": len(mine),
                "sessions_as_expected": all_ok,
                "per_ask": per_ask,
                "self_filmed": sum(s["self_filmed"] for s in mine),
                "mixed_as_new": sum(s["mixed_as_new"] for s in mine),
                "mixed_as_old": sum(s["mixed_as_old"] for s in mine),
                "honest": [sum(honest), len(honest)],
                "quit_early": [sum(quit_early), len(quit_early)],
                "both_answered": [sum(both), len(both)],
                "closed_things": sum(1 for s in mine for t in s["things"] if t.get("closed")),
                "counted_tries": [
                    [t.get("tries") for t in s["things"]] for s in mine if s["kind"] == "breaks"
                ],
                "cost_usd": round(sum(s["cost_usd"] for s in mine), 2),
            }
        )
    return out


def main(folder: Path) -> None:
    load_cases(CASES_V2)
    records = sorted((folder / "sessions").glob("*/record.json"))
    sessions = [session_rows(json.loads(p.read_text())) for p in records]
    _judge(sessions)
    order = {k: i for i, (_, _, kinds) in enumerate(GROUPS) for k in kinds}
    sessions.sort(key=lambda s: (order[s["kind"]], s["case"]))
    results = {
        "sessions": sessions,
        "groups": summarize(sessions),
        "cut_off_sessions": [s["case"] for s in sessions if s["cut_off"]],
        "cost_usd": round(sum(s["cost_usd"] for s in sessions), 2),
        "before": {"tried_on_ask": [51, 43, 42, 33], "sessions": 60},
        "scripts": json.loads(CASES_V2.read_text())["cases"],
        "notes": json.loads((folder / "notes.json").read_text())
        if (folder / "notes.json").is_file()
        else [],
    }
    (folder / "results.json").write_text(json.dumps(results, indent=1))
    for g in results["groups"]:
        print(
            g["group"],
            g["title"],
            f"{g['sessions_as_expected']}/{g['sessions']} as expected;",
            "tried per ask:",
            [f"{p['tried']}/{p['sessions']}" for p in g["per_ask"]],
            "self-filmed",
            g["self_filmed"],
            "mixups new/old",
            g["mixed_as_new"],
            g["mixed_as_old"],
            "honest",
            g["honest"],
            "quit early",
            g["quit_early"],
            "both",
            g["both_answered"],
        )
    print("cut-off sessions:", results["cut_off_sessions"], "cost", results["cost_usd"])


if __name__ == "__main__":
    main(Path(sys.argv[1]))
