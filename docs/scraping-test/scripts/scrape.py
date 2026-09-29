"""Job B: run both scrapers on every page and save everything.

Run inside the backend container:  python /tmp/st/scrape.py pages.json out-dir
Writes <out-dir>/current.json and <out-dir>/firecrawl.json, {key: record}. Re-runnable: pages
already done are skipped.

Record shapes:
  current:   {"status": ok|blocked|error|empty, "final_url", "http_status", "text", "photo_urls", "error", "seconds"}
  firecrawl: {"status": ..., "http_status", "markdown", "images", "metadata", "json_ld", "warning", "error", "seconds"}
"""

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

BLOCK_WORDS = re.compile(
    r"access denied|verify you are human|verify you're human|are you a robot|robot check|"
    r"captcha|unusual traffic|request blocked|pardon our interruption|bot detection|"
    r"press & hold|checking your browser|just a moment|enable javascript and cookies to continue|"
    r"api-services-support@amazon\.com|reference #\d|incapsula|perimeterx|akamai|"
    r"human verification|security check",
    re.I,
)


def classify(http_status, text: str, error: str | None) -> str:
    head = (text or "")[:20_000]
    if http_status in (403, 429, 503) or BLOCK_WORDS.search(head):
        return "blocked"
    if error:
        return "error"
    if len((text or "").strip()) < 500:
        return "empty"
    return "ok"


def run_current(url: str) -> dict:
    from adforge.retry import OutsideServiceDown
    from jobs import page

    t = time.time()
    try:
        d = page.download(url, max_bytes=page.MAX_PAGE_BYTES, what="product page")
        p = page.parse(d)
        rec = {"final_url": d.final_url, "http_status": 200, "text": p.text, "photo_urls": p.photo_urls, "error": None}
    except (page.PageUnreadable, OutsideServiceDown) as e:
        m = re.search(r"answered (\d{3})", str(e))
        rec = {"final_url": None, "http_status": int(m.group(1)) if m else None, "text": "", "photo_urls": [], "error": f"{type(e).__name__}: {e}"}
    except Exception as e:  # noqa: BLE001
        rec = {"final_url": None, "http_status": None, "text": "", "photo_urls": [], "error": f"{type(e).__name__}: {e}"}
    rec["seconds"] = round(time.time() - t, 1)
    rec["status"] = classify(rec["http_status"], rec["text"], rec["error"])
    return rec


def run_firecrawl(url: str, key: str) -> dict:
    import os

    t = time.time()
    headers = {"Authorization": f"Bearer {os.environ['FIRECRAWL_API_KEY']}", "Content-Type": "application/json"}
    body = {"url": url, "formats": ["markdown", "images", "rawHtml"]}
    rec: dict = {"http_status": None, "markdown": "", "images": [], "metadata": {}, "json_ld": [], "warning": None, "error": None}
    for attempt in range(6):
        try:
            r = httpx.post("https://api.firecrawl.dev/v2/scrape", headers=headers, json=body, timeout=180)
        except httpx.HTTPError as e:
            rec["error"] = f"{type(e).__name__}: {e}"
            time.sleep(5)
            continue
        if r.status_code == 429:
            time.sleep(15 * (attempt + 1))
            continue
        if r.status_code != 200:
            rec["error"] = f"firecrawl {r.status_code}: {r.text[:300]}"
            if r.status_code >= 500:
                time.sleep(10)
                continue
            break
        data = r.json().get("data") or {}
        md = data.get("metadata") or {}
        raw = data.get("rawHtml") or ""
        json_ld = []
        if raw:
            soup = BeautifulSoup(raw, "html.parser")
            json_ld = [s.get_text() for s in soup.find_all("script", type="application/ld+json")]
        rec.update({
            "http_status": md.get("statusCode"), "markdown": data.get("markdown") or "",
            "images": data.get("images") or [], "metadata": md, "json_ld": json_ld,
            "warning": data.get("warning"), "error": None, "raw_html_len": len(raw),
        })
        break
    rec["seconds"] = round(time.time() - t, 1)
    rec["status"] = classify(rec["http_status"], rec["markdown"], rec["error"])
    return rec


def main(pages_file: str, out_dir: str) -> None:
    pages = json.loads(Path(pages_file).read_text())
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {"current": out / "current.json", "firecrawl": out / "firecrawl.json"}
    done = {k: (json.loads(f.read_text()) if f.exists() else {}) for k, f in files.items()}

    def save(tool: str) -> None:
        files[tool].write_text(json.dumps(done[tool], indent=1, ensure_ascii=False))

    todo_c = [(k, u) for k, u in pages.items() if k not in done["current"]]
    todo_f = [(k, u) for k, u in pages.items() if k not in done["firecrawl"]]
    print(f"current: {len(todo_c)} to do, firecrawl: {len(todo_f)} to do", flush=True)

    with ThreadPoolExecutor(6) as pool_c, ThreadPoolExecutor(3) as pool_f:
        fut_c = {pool_c.submit(run_current, u): k for k, u in todo_c}
        fut_f = {pool_f.submit(run_firecrawl, u, k): k for k, u in todo_f}
        for fut in as_completed(list(fut_c) + list(fut_f)):
            if fut in fut_c:
                k = fut_c[fut]; done["current"][k] = fut.result(); save("current")
                r = done["current"][k]; print(f"current   {k}: {r['status']} ({r['http_status']}, {len(r['text'])} chars, {len(r['photo_urls'])} photos, {r['seconds']}s)", flush=True)
            else:
                k = fut_f[fut]; done["firecrawl"][k] = fut.result(); save("firecrawl")
                r = done["firecrawl"][k]; print(f"firecrawl {k}: {r['status']} ({r['http_status']}, {len(r['markdown'])} chars, {len(r['images'])} images, {r['seconds']}s)", flush=True)


if __name__ == "__main__":
    import os

    import django

    sys.path.insert(0, "/app")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
    django.setup()
    main(sys.argv[1], sys.argv[2])
