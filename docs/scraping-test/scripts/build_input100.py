"""Build Job A's input for the 100 pages from Job B's Firecrawl output.
Usage: python build_input100.py <firecrawl.json> <pages100-meta.json> <input100.json>"""
import json, sys
from pathlib import Path
fc = json.loads(Path(sys.argv[1]).read_text())
meta = {m["key"]: m for m in json.loads(Path(sys.argv[2]).read_text())}
out = {}
for key, r in fc.items():
    if r.get("status") == "ok" and r.get("images"):
        out[key] = {"product": meta[key]["product"], "variant": None, "images": r["images"]}
Path(sys.argv[3]).write_text(json.dumps(out, indent=1))
print(len(out), "pages with images;", sum(len(v["images"]) for v in out.values()), "images total")
