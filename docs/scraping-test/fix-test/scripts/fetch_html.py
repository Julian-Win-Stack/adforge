"""Refetch the test pages with Firecrawl, keeping rawHtml + html + links (1 credit a page).
Writes /tmp/st/v2/html/<key>.json. Skips keys already fetched."""
import json, os, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
KEY = os.environ["FIRECRAWL_API_KEY"]
OUT = "/tmp/st/v2/html"
pages = json.load(open("/tmp/st/v2/pages.json"))

def fetch(item):
    key, url = item
    path = f"{OUT}/{key}.json"
    if os.path.exists(path):
        return key, "cached"
    body = json.dumps({"url": url, "formats": ["rawHtml", "html", "links", "markdown", "images"]}).encode()
    for attempt in range(4):
        req = urllib.request.Request("https://api.firecrawl.dev/v2/scrape", data=body,
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
        t = time.time()
        try:
            with urllib.request.urlopen(req, timeout=330) as r:
                data = json.loads(r.read())
            d = data.get("data", {})
            json.dump({"url": url, "rawHtml": d.get("rawHtml"), "html": d.get("html"), "links": d.get("links"),
                       "markdown": d.get("markdown"), "images": d.get("images"), "metadata": d.get("metadata"),
                       "seconds": round(time.time() - t)}, open(path, "w"))
            return key, f"ok {len(d.get('rawHtml') or '')} bytes {round(time.time()-t)}s"
        except urllib.error.HTTPError as e:
            msg = e.read()[:200]
            if e.code == 429:
                time.sleep(15 * (attempt + 1)); continue
            return key, f"HTTP {e.code} {msg!r}"
        except Exception as e:
            if attempt < 3: time.sleep(10); continue
            return key, f"ERR {type(e).__name__}: {str(e)[:100]}"
    return key, "gave up"

with ThreadPoolExecutor(3) as ex:
    for key, status in ex.map(fetch, sorted(pages.items())):
        print(key, status, flush=True)
print("FETCH DONE")
