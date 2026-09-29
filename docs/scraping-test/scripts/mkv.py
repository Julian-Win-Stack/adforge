import json, sys
key = sys.argv[1]
over = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
d = json.load(open(f'/tmp/st/sheets/100/{key}.json'))
v = {}
for it in d['items']:
    i = it['id']
    if i in over:
        v[i] = {"verdict": over[i][0], "note": over[i][1]}
    else:
        v[i] = {"verdict": "correct", "note": ""}
for k in over:
    assert k in v, f"unknown id {k}"
json.dump({"key": key, "verdicts": v}, open(f'/tmp/st/verdicts/100/{key}.json', 'w'), indent=1)
from collections import Counter
print(key, Counter(x['verdict'] for x in v.values()))
