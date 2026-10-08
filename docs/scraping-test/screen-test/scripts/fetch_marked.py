"""Open each test page in Firecrawl's browser, scroll it so lazy photos load, run mark.js (numbered
boxes on every image and heading), then take a full-page screenshot. Also keeps rawHtml for the
shop's own photo list. Writes /tmp/sp/pages/<key>.json and /tmp/sp/shots/<key>.png.
Usage: python fetch_marked.py [keys...]"""
import json, os, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

KEY = os.environ["FIRECRAWL_API_KEY"]
ROOT = "/tmp/sp"
os.makedirs(f"{ROOT}/pages", exist_ok=True); os.makedirs(f"{ROOT}/shots", exist_ok=True)
pages = json.load(open(f"{ROOT}/pages.json"))
MARK = open(f"{ROOT}/mark.js").read()
ONLY = set(sys.argv[1:])

ACTIONS = [{"type": "wait", "milliseconds": 3000}]
for _ in range(10):
    ACTIONS += [{"type": "scroll", "direction": "down"}, {"type": "wait", "milliseconds": 500}]
ACTIONS += [
    {"type": "executeJavascript", "script": "window.scrollTo(0, 0)"},
    {"type": "wait", "milliseconds": 2000},
    {"type": "executeJavascript", "script": MARK},
    {"type": "wait", "milliseconds": 1000},
    {"type": "screenshot", "fullPage": True},
]


def fetch(item):
    key, page = item
    path = f"{ROOT}/pages/{key}.json"
    if os.path.exists(path):
        return key, "cached"
    body = json.dumps({"url": page["url"], "formats": ["rawHtml", "markdown"], "onlyMainContent": False,
                       "timeout": 300000, "actions": ACTIONS}).encode()
    for attempt in range(4):
        req = urllib.request.Request("https://api.firecrawl.dev/v2/scrape", data=body,
                                     headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
        t = time.time()
        try:
            with urllib.request.urlopen(req, timeout=330) as r:
                data = json.loads(r.read())
            d = data.get("data", {})
            acts = d.get("actions") or {}
            js = acts.get("javascriptReturns") or []
            marks = None
            for ret in js:
                v = ret.get("value") if isinstance(ret, dict) else ret
                if isinstance(v, str) and v.startswith("{"):
                    marks = json.loads(v)
            shots = acts.get("screenshots") or []
            if shots:
                with urllib.request.urlopen(shots[0], timeout=120) as r:
                    open(f"{ROOT}/shots/{key}.png", "wb").write(r.read())
            json.dump({"url": page["url"], "rawHtml": d.get("rawHtml"), "markdown": d.get("markdown"),
                       "metadata": d.get("metadata"), "marks": marks, "js_raw": [str(x)[:300] for x in js],
                       "seconds": round(time.time() - t)}, open(path, "w"))
            return key, (f"ok {round(time.time()-t)}s imgs={len(marks['imgs']) if marks else None} "
                         f"numbered={sum(1 for i in marks['imgs'] if i['n']) if marks else None} "
                         f"secs={len(marks['secs']) if marks else None} shot={bool(shots)}")
        except urllib.error.HTTPError as e:
            msg = e.read()[:300]
            if e.code == 429:
                time.sleep(15 * (attempt + 1)); continue
            return key, f"HTTP {e.code} {msg!r}"
        except Exception as e:  # noqa: BLE001
            if attempt < 2: time.sleep(10); continue
            return key, f"ERR {type(e).__name__}: {str(e)[:150]}"
    return key, "gave up"


items = [(k, v) for k, v in sorted(pages.items()) if not ONLY or k in ONLY]
with ThreadPoolExecutor(3) as ex:
    for key, status in ex.map(fetch, items):
        print(key, status, flush=True)
print("FETCH DONE")
