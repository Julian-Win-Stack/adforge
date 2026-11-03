"""Turns upload results into media-map.json and <prior>-map.json, so nobody retypes hundreds of
ids. The Artifact tool prints each upload and copy result only in the session transcript.

  python3 map_uploads.py <transcript.jsonl> <prior-ids.json>

<transcript.jsonl> is the session's file, or the subagent's under
~/.claude/projects/<project>/<session>/subagents/agent-<id>.jsonl when one did the uploads.
<prior-ids.json> is a list of [name, "/_blob/<old id>"]; copies get NEW ids in this artifact.
Run from the folder holding aname.json. The regexes match the tool's wording in Nov 2026."""
import json
import re
import sys

uploaded, copied = {}, {}
for line in open(sys.argv[1]):
    content = (json.loads(line).get("message") or {}).get("content")
    if not isinstance(content, list):
        continue
    for item in content:
        if item.get("type") != "tool_result":
            continue
        text = item.get("content")
        if not isinstance(text, str):
            text = " ".join(part.get("text", "") for part in text if isinstance(part, dict))
        for name, url in re.findall(r'media/([0-9a-f]+\.\w+)" \([^)]*\) → "(/_blob/[0-9a-f]+)"', text):
            uploaded[name] = url
        for new, old in re.findall(r"(/_blob/[0-9a-f]{32})[^\n]*?source id ([0-9a-f]{32})", text):
            copied[old] = new
names = json.load(open("aname.json"))
json.dump({names[name]: url for name, url in uploaded.items()}, open("media-map.json", "w"))
prior = json.load(open(sys.argv[2]))
mapped = {key: copied[url.split("/")[-1]] for key, url in prior if url.split("/")[-1] in copied}
json.dump(mapped, open("prior-map.json", "w"))
print(len(uploaded), "uploaded;", len(mapped), "of", len(prior), "copied")
