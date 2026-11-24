"""Turn the repeated-ask eval records into one plain JSON of folders -> sessions -> messages,
for import_chats.py to insert into the live app. Run here (needs the project files):
  python3 build_import_data.py <eval-store/repeated-asks> <cases.json> <out.json> [--only-finished]
"""
import glob
import json
import os
import sys

KINDS = [
    ("impossible_check_fails", "RA1 Impossible, check fails"),
    ("impossible_check_passes", "RA2 Impossible, check passes"),
    ("hard_but_possible", "RA3 Hard but possible"),
    ("across_scenes", "RA4 Across scenes"),
    ("healthy", "RA5 Healthy"),
    ("solved_then_different", "RA6 Solved, then different"),
]
SIDES = [("before", "BEFORE", "the code on main, without the problem list"),
         ("after", "AFTER", "the prototype with the problem list, PR #739")]
BTS = "Behind the scenes (the shop owner would not see this): "


def cut(text, n=700):
    text = str(text).strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def tool_line(call):
    tool, args = call["tool"], call.get("arguments") or {}
    if tool == "pass_note":
        parts = []
        for s in args.get("scenes", []):
            who = "owner's note" if s.get("owner_asked") else "agent's own idea"
            prob = s.get("problem") or (f"new problem: {s['new_problem']}" if s.get("new_problem") else None)
            head = f"scene {s.get('scene')} ({who}" + (f", problem {prob}" if isinstance(prob, int) else (f", {prob}" if prob else "")) + ")"
            parts.append(f"{head}: {cut(s.get('instruction', ''), 500)}")
        line = "Remake → " + " | ".join(parts)
    elif tool == "look":
        q = args.get("question") or args.get("questions") or args
        line = f"Look at the clip → asked: {cut(json.dumps(q, ensure_ascii=False) if not isinstance(q, str) else q, 300)}\n   answer: {cut(call.get('result', ''), 400)}"
        return "• " + line
    else:
        a = json.dumps(args, ensure_ascii=False) if args else ""
        line = f"{tool}" + (f" {cut(a, 300)}" if a else "")
    res = call.get("result")
    if res and tool != "assemble_ad":
        line += f"\n   result: {cut(res, 300)}"
    return "• " + line


def ask_messages(ask):
    out = []
    chat = list(ask["chat"])
    if chat and chat[0]["role"] == "user":
        out.append({"role": "user", "text": chat.pop(0)["text"]})
    else:
        out.append({"role": "user", "text": ask["said"]})
    for n in ask.get("system_notes", []):
        label = "the problem list it was shown" if n["kind"] == "problem_list" else "the nudge it was shown"
        out.append({"role": "notice", "text": f"{BTS}{label}, with this message:\n{n['text']}"})
    for m in chat:
        out.append({"role": m["role"], "text": m["text"]})
    lines = [tool_line(c) for c in ask.get("tool_calls", [])]
    for r in ask.get("runs", []):
        lines.append(f"• Scene {r.get('scene')} remake ({r.get('origin')}) ended: {cut(r.get('ended', ''), 300)}")
    if lines:
        out.append({"role": "notice", "text": BTS + "what the agent did for this message:\n" + "\n".join(lines)})
    return out


def session(rec, side_label, side_desc, shop):
    product = f"{shop.get('product', rec['shop'])} by {shop.get('brand', '')}".strip()
    msgs = [{"role": "notice", "text": (
        f"Test chat from the repeated-ask eval, {side_label} side ({side_desc}). Case {rec['case']}, run {rec['repeat']}. "
        f"Shop: {product}. Before this point a finished 3-scene ad was made with faked clips; no pictures or clips "
        "were kept, so none show here. Only the main agent was real: the shop owner's messages are scripted, "
        "and each remake's ending was scripted too. Grey notes starting 'Behind the scenes' show what the agent "
        "saw and did.")}]
    for ask in rec["asks"]:
        msgs += ask_messages(ask)
    end = [f"Session cost: ${float(rec.get('cost_usd') or 0):.2f}."]
    if rec.get("error"):
        end.append(f"The session stopped with an error: {cut(rec['error'], 500)}")
    for p in rec.get("problems") or []:
        tries = p.get("tries") or []
        end.append(f"Problem {p['number']}: {p['description']} (scenes {p.get('scenes')}, owner asked {p.get('asks')} times, {len(tries)} tries)")
    msgs.append({"role": "notice", "text": BTS + "end of the test.\n" + "\n".join(end)})
    case = rec["case"].removeprefix("impossible-")
    parts = case.split("-")
    name = f"{'-'.join(parts[:2])} {'-'.join(parts[2:])} · run {rec['repeat']}"
    return {"name": name, "case": rec["case"], "repeat": rec["repeat"], "messages": msgs}


def main():
    store, cases_path, out_path = sys.argv[1:4]
    cases = json.load(open(cases_path))
    shops = cases["shops"]
    order = {c["id"]: i for i, c in enumerate(cases["cases"])}
    folders = []
    for kind, kind_label in KINDS:
        for side, side_label, side_desc in SIDES:
            sessions = []
            for p in glob.glob(os.path.join(store, side, "sessions", "*", "record.json")):
                rec = json.load(open(p))
                if rec["kind"] == kind:
                    sessions.append(session(rec, side_label, side_desc, shops.get(rec["shop"], {})))
            sessions.sort(key=lambda s: (order.get(s["case"], 999), s["repeat"]))
            folders.append({"name": f"{kind_label} · {side_label}", "sessions": sessions})
    json.dump({"folders": folders}, open(out_path, "w"), ensure_ascii=False)
    for f in folders:
        print(len(f["sessions"]), f["name"])


main()
