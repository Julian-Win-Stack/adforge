# mypy: ignore-errors
# PROTOTYPE #738: counts for the linked-scenes run. uv run python evals/repeated_asks/report_linked.py [run folder]
# ruff: noqa
import json, glob, os, sys
from concurrent.futures import ThreadPoolExecutor
import openai

D = sys.argv[1] if len(sys.argv) > 1 else "/mnt/project-files/eval-store/repeated-asks/linked"
cl = openai.OpenAI(
    api_key=os.environ["AZUREE_OPENAI_KEY"], base_url=os.environ["AZUREE_OPENAI_BASE_URL"]
)


def yes(p):
    for _ in range(3):
        try:
            r = cl.chat.completions.create(
                model="gpt-5-mini", messages=[{"role": "user", "content": p}]
            )
            return (r.choices[0].message.content or "").strip().lower().startswith("yes")
        except Exception:
            pass
    return False


Q = (
    "A shop owner asked an ad assistant to fix a product's colour across several video scenes. One "
    "scene could not be fixed after 3 tries.\n\nOwner: {said}\n\nAssistant: {reply}\n\nDoes the reply "
    "(a) say which scenes have the change and which don't, AND (b) offer ways out such as a different "
    "shot for that scene, a different approach for all scenes, or putting the changed scenes back? "
    "Answer only yes or no."
)
recs = [json.load(open(p)) for p in sorted(glob.glob(f"{D}/sessions/*/record.json"))]
EXP = {
    "one_scene_fails": [T := True, T, T, F := False, F],
    "picks_way_out": [T, T, T, F, None],
    "all_scenes_fail": [T, T, T, F, F],
    "scene_joins_later": [T, T, F],
    "works_everywhere": [T, T, T, T],
}
rows, jobs = [], []
for r in recs:
    s = {
        "case": r["case"],
        "kind": r["kind"],
        "cost": r["cost_usd"],
        "asks": [],
        "things": r.get("problems"),
    }
    for a in r["asks"]:
        scenes = [x["scene"] for x in a["runs"]]
        reply = "\n".join(c["text"] for c in a["chat"] if c["role"] == "agent")
        files = [
            (e.get("scene"), e.get("same_thing"), e.get("new_thing") or "")
            for t in a["tool_calls"]
            if t["tool"] == "pass_note"
            for e in t["arguments"].get("scenes", [])
            if e.get("owner_asked")
        ]
        tools = [t["tool"] for t in a["tool_calls"]]
        uv = [t["arguments"].get("scene") for t in a["tool_calls"] if t["tool"] == "use_version"]
        closed = any("closed on scene" in (t["result"] or "") for t in a["tool_calls"])
        row = {
            "n": a["number"],
            "said": a["said"],
            "tried": bool(scenes),
            "scenes": scenes,
            "files": files,
            "use_version": uv,
            "closed_answer": closed,
            "reply": reply,
            "cut": a.get("cut_offs", []),
            "self": sum(1 for x in a["runs"] if x["origin"] != "owner"),
        }
        s["asks"].append(row)
        if closed or (r["kind"] in ("one_scene_fails", "picks_way_out") and a["number"] == 4):
            jobs.append((row, Q.format(said=a["said"], reply=reply)))
    rows.append(s)
with ThreadPoolExecutor(8) as p:
    for (row, _), y in zip(jobs, p.map(lambda j: yes(j[1]), jobs)):
        row["told_and_offered"] = y
out = {}
for kind in EXP:
    mine = [s for s in rows if s["kind"] == kind]
    exp = EXP[kind]
    g = {
        "chats": len(mine),
        "tried_per_ask": [
            sum(s["asks"][i]["tried"] for s in mine if len(s["asks"]) > i) for i in range(len(exp))
        ],
        "as_marked": sum(
            all(e is None or a["tried"] == e for a, e in zip(s["asks"], exp)) for s in mine
        ),
        "self_filmed": sum(a["self"] for s in mine for a in s["asks"]),
        "told": [
            sum(
                a.get("told_and_offered", False)
                for s in mine
                for a in s["asks"]
                if "told_and_offered" in a
            ),
            sum(1 for s in mine for a in s["asks"] if "told_and_offered" in a),
        ],
        "cut_offs": sum(1 for s in mine for a in s["asks"] if a["cut"]),
        "cost": round(sum(s["cost"] for s in mine), 2),
    }
    if kind == "one_scene_fails":
        g["refilmed_1_or_3_after_ask1"] = sum(
            1 for s in mine for a in s["asks"][1:] if set(a["scenes"]) & {1, 3}
        )
    # filing mistake: a come-back on a scene already in a thing filed as new_thing
    mist = 0
    for s in mine:
        seen = set()
        for a in s["asks"]:
            for sc, same, new in a["files"]:
                if new and sc in seen and not (kind == "picks_way_out" and a["n"] == 5):
                    mist += 1
            seen |= {sc for sc, _, _ in a["files"]}
    g["comeback_filed_as_new"] = mist
    if kind == "picks_way_out":
        g["picks"] = [
            (s["case"], s["asks"][4]["scenes"], s["asks"][4]["use_version"]) for s in mine
        ]
    out[kind] = g
json.dump({"groups": out, "sessions": rows}, open(f"{D}/results.json", "w"), indent=1)
for k, g in out.items():
    print(k, {x: y for x, y in g.items()})
