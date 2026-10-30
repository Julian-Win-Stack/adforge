"""Answer as the shop owner: python answer.py <run-dir> <key> "<text>". Logs to <run-dir>/owner-answers-live.log."""
import json, os, sys, urllib.request, datetime
B = "http://127.0.0.1:8000/api"
RUN = sys.argv[1]
ads = json.load(open(os.path.join(RUN, "ads.json")))
name, text = sys.argv[2], sys.argv[3]
sid = ads[name]
msgs = json.load(urllib.request.urlopen(f"{B}/sessions/{sid}/"))["messages"]
q = next((m.get("text", "") for m in reversed(msgs) if m.get("role") not in ("user", "notice")), "")
r = urllib.request.Request(f"{B}/sessions/{sid}/messages/", method="POST", data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"})
urllib.request.urlopen(r)
with open(os.path.join(RUN, "owner-answers-live.log"), "a") as f:
    f.write(f"## {name} {datetime.datetime.now(datetime.timezone.utc):%H:%M}Z\nQ: {q}\nA: {text}\n\n")
print("sent", name)
