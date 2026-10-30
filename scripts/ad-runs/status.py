"""python status.py <run-dir> [full]: last message of each ad, plus who spoke last."""
import json, os, sys, urllib.request
B = "http://127.0.0.1:8000/api"; RUN = sys.argv[1]
ads = json.load(open(os.path.join(RUN, "ads.json")))
n = 2000 if len(sys.argv) > 2 else 220
for name, sid in ads.items():
    ms = json.load(urllib.request.urlopen(f"{B}/sessions/{sid}/"))["messages"]
    last = ms[-1]
    print(f"== {name} msgs={len(ms)} last={last['role']}/{last.get('level','')}: {last.get('text','')[:n]!r}")
