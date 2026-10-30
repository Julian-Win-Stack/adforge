"""Emit a line when an ad goes quiet with the agent speaking last (likely waiting on the owner),
or posts a red notice: python watch.py <run-dir>. Quiet = a question unanswered for 60 s, or nothing new for 10 min (stall)."""
import json, os, sys, time, urllib.request
B = "http://127.0.0.1:8000/api"; RUN = sys.argv[1]
seen, reported, red = {}, set(), set()
done= set()
STATE = os.path.join(RUN, "watch-state.json")
if os.path.exists(STATE):
    st = json.load(open(STATE)); red = {tuple(x) for x in st["red"]}; done = set(st["done"])
def save():
    json.dump({"red": sorted(red), "done": sorted(done)}, open(STATE, "w"))
while True:
    try:
        ads = json.load(open(os.path.join(RUN, "ads.json")))
        for name, sid in ads.items():
            ms = json.load(urllib.request.urlopen(f"{B}/sessions/{sid}/", timeout=20))["messages"]
            for m in ms:
                if m["role"] == "notice" and m.get("level") == "problem" and (sid, m["seq"]) not in red:
                    red.add((sid, m["seq"])); print(f"RED {name}: {m['text'][:200]}", flush=True)
            last = ms[-1]; key = (sid, last["seq"])
            if seen.get(sid, (None,))[0] != last["seq"]:
                seen[sid] = (last["seq"], time.time())
            elif key not in reported and last["role"] != "user" and sid not in done and (
                (time.time() - seen[sid][1] > 60 and last.get("text", "").rstrip().endswith("?"))
                or time.time() - seen[sid][1] > 600):
                reported.add(key); print(f"QUIET {name} [{last['role']}]: {last.get('text','')[:160]!r}", flush=True)
    except Exception as e:
        print(f"watch error {e}", flush=True)
    save()
    time.sleep(15)
