"""Batches a built page's media for upload. Run on the VM from the folder holding build.py, after
`python3 build.py test` has written need.json and after media/ and m/ hold each file under its
hashed name. Writes abatches.json (25 per asset call), aname.json (hashed name -> media key) and
fbatch<N>.json (each publish of `files`: at most 60 MB and 240 files).

Consolidated from the inline commands used for round 8; check it on the first run."""
import hashlib
import json
import os

S = os.path.dirname(os.path.abspath(__file__))


def name(key):
    return hashlib.sha1(key.encode()).hexdigest()[:14] + os.path.splitext(key)[1].lower()


need = json.load(open(f"{S}/need.json"))
sizes = [(key, os.path.getsize(f"{S}/m/{name(key)}")) for key in need["files"]]
batches, batch, total = [], [], 0
for key, size in sizes:
    if total + size > 60e6 or len(batch) >= 240:
        batches.append(batch)
        batch, total = [], 0
    batch.append(key)
    total += size
batches.append(batch)
for number, batch in enumerate(batches):
    json.dump(["m/" + name(key) for key in batch], open(f"{S}/fbatch{number}.json", "w"))
paths = [f"{S}/media/{name(key)}" for key in need["assets"]]
print("over 15 MB:", [p for p in paths if os.path.getsize(p) > 15e6])
json.dump([paths[i : i + 25] for i in range(0, len(paths), 25)], open(f"{S}/abatches.json", "w"))
json.dump({name(key): key for key in need["assets"]}, open(f"{S}/aname.json", "w"))
