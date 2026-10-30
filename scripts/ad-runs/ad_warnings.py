"""Every warning the app posted in a run's chats, so a fallback is never missed.

Julian (2026-10-15): the photo picker found nothing on the phone case page and the app fell
back to the page's declared photos; the app said so in the chat, but no run script read the
chats, so nobody knew. Run this at the end of EVERY test run, before grading, and put its
output in the run notes and in the reply to Julian.

  python ad_warnings.py <run-dir>/ads.json [out.md]

ads.json is what run.py writes: {"<key>": "<session id>", ...}. Reads
each session's warnings through the app's API (the app must still be running): job.warnings,
every fallback and failure the app recorded, whether or not the shop owner's chat shows it
(Oct 30 2026, spec #272: the chat shows the owner only what they can act on). Lists every
red warning (level "problem"), in order, with its kind; grey ones (level "info") separately.
A red chat notice with no warning line (a session that never got a job) is listed too.
Exits 1 if any red warning was recorded, so a run with a warning can't pass quietly.
"""

import json
import sys
import urllib.error
import urllib.request

API = "http://127.0.0.1:8000/api"


def warnings(session_id: str) -> list[dict]:
    """The session's warnings, then any red chat notice none of them holds. An app from
    before the warnings API (404) is read from its chat notices alone, as before."""
    try:
        with urllib.request.urlopen(f"{API}/sessions/{session_id}/warnings/") as answer:
            found = json.load(answer)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        found = []
    with urllib.request.urlopen(f"{API}/sessions/{session_id}/") as answer:
        messages = json.load(answer)["messages"]
    texts = {w["text"] for w in found}
    found += [
        {"level": m["level"] or "info", "text": m["text"], "kind": "chat notice"}
        for m in messages
        if m.get("role") == "notice" and m["text"] and m["text"] not in texts
    ]
    return found


def main() -> int:
    sessions = json.load(open(sys.argv[1]))
    lines = ["# Warnings the app recorded", ""]
    red_ads = []
    for number, session_id in sessions.items():
        found = warnings(session_id)
        red = [w for w in found if w.get("level") == "problem"]
        grey = [w for w in found if w.get("level") != "problem"]
        if red:
            red_ads.append(number)
        lines.append(f"## #{number}: {len(red)} red, {len(grey)} grey")
        lines += [f"- RED [{w.get('kind', '?')}]: {w['text']}" for w in red]
        lines += [f"- grey [{w.get('kind', '?')}]: {w['text']}" for w in grey]
        lines.append("")
    summary = (
        f"RED WARNINGS in {len(red_ads)} of {len(sessions)} ads: #{', #'.join(red_ads)}"
        if red_ads
        else f"No red warnings in {len(sessions)} ads."
    )
    lines.insert(2, f"**{summary}**\n")
    text = "\n".join(lines)
    print(text)
    if len(sys.argv) > 2:
        open(sys.argv[2], "w").write(text)
    return 1 if red_ads else 0


if __name__ == "__main__":
    sys.exit(main())
