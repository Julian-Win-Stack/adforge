"""Score Job A's filter output against the hand-found gallery answer key.
Usage: python score_filter.py <filter.json> <gallery.json>"""
import json, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from score import photo_key

out = json.loads(Path(sys.argv[1]).read_text())
gallery = json.loads(Path(sys.argv[2]).read_text())
tot_g = tot_got = tot_kept = tot_junk = 0
rows = ["| Page | Firecrawl images | Kept | Gallery photos kept | Non-gallery kept | Gallery photos lost (why) |", "|---|---|---|---|---|---|"]
for key, r in out.items():
    wanted = gallery.get(key) or []
    wanted_keys = {photo_key(u) for u in wanted}
    kept_keys = {photo_key(x["url"]) for x in r["kept"]}
    got = len(wanted_keys & kept_keys)
    junk = len(kept_keys - wanted_keys)
    lost = []
    for wk in wanted_keys - kept_keys:
        why = [x.get("drop") + ("" if not x.get("reason") else f" ({x['reason']})") for x in r["dropped"] if photo_key(x["url"]) == wk]
        lost.append(f"{wk[:30]}: {'; '.join(why) if why else 'not in Firecrawl list'}")
    tot_g += len(wanted_keys); tot_got += got; tot_kept += len(r["kept"]); tot_junk += junk
    rows.append(f"| {r['product'][:40]} | {r['images_in']} | {len(r['kept'])} | {got}/{len(wanted_keys)} | {junk} | {'<br>'.join(lost) or '-'} |")
rows.append(f"| **Total** | | **{tot_kept}** | **{tot_got}/{tot_g}** | **{tot_junk}** | |")
print("\n".join(rows))
print()
drops = Counter()
for r in out.values():
    for x in r["dropped"]:
        d = x["drop"]
        drops["smaller-copy" if d.startswith("smaller-copy") else d.split("-")[0] if d.startswith(("tiny", "banner", "download")) else d] += 1
print("Drop reasons:", dict(drops))
calls = sum(r["api_calls"] for r in out.values()); it = sum(r["input_tokens"] for r in out.values()); ot = sum(r["output_tokens"] for r in out.values())
print(f"API calls {calls}, tokens in {it} out {ot}, cost ${(it*0.25+ot*2.0)/1e6:.3f}")
