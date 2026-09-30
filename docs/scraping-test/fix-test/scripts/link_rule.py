"""Deterministic test: is a page image wrapped in a link to a different page? Uses the refetched
rawHtml. For every image on the review sheets, records the nearest enclosing <a href> and its alt."""
import json, glob, re, collections
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

truth = json.load(open("/tmp/st/v2/photo_truth.json"))
pages = json.load(open("/tmp/st/v2/pages.json"))
IMG_EXT = re.compile(r"\.(jpe?g|png|webp|gif|avif)(\?|$)", re.I)

def key_of(url):
    p = urlparse(url); name = p.path.rstrip("/").split("/")[-1]
    name = re.sub(r"\.[a-z0-9]{2,5}$", "", name, flags=re.I)  # extension
    name = re.sub(r"(_\d+x\d*|_\d+x|-\d+x\d+|\._[A-Z0-9_,]+_)$", "", name)  # size suffixes
    return name.casefold()

def page_path(u):
    p = urlparse(u); return (p.netloc.replace("www.", ""), p.path.rstrip("/"))

def product_id(u):
    """The bit of a product URL that names the product: an Amazon/Chewy /dp/<id>, else the last path segment."""
    p = urlparse(u).path.rstrip("/")
    m = re.search(r"/dp/([A-Za-z0-9]+)", p)
    return m.group(1).casefold() if m else p.split("/")[-1].casefold()

def link_kind(href, page_url):
    if not href: return "none"
    h = href.strip()
    if h.startswith("#") or h.lower().startswith("javascript:"): return "none"
    full = urljoin(page_url, h)
    if IMG_EXT.search(urlparse(full).path): return "image-file"
    if page_path(full) == page_path(page_url) or product_id(full) == product_id(page_url): return "same-page"
    return "other-page"

def scan(key):
    f = f"/tmp/st/v2/html/{key}.json"
    try: d = json.load(open(f))
    except FileNotFoundError: return None
    html = d.get("rawHtml") or ""
    if len(html) < 2000: return None
    soup = BeautifulSoup(html, "html.parser")
    page_url = d["url"]
    found = {}  # image key -> list of (link kind, href, alt, in-picture, order)
    order = 0
    for img in soup.find_all(["img", "source"]):
        urls = []
        for attr in ("src", "data-src", "data-srcset", "srcset", "data-original", "data-zoom-image", "data-image"):
            v = img.get(attr)
            if not v: continue
            for part in str(v).split(","):
                u = part.strip().split(" ")[0]
                if u and not u.startswith("data:"): urls.append(urljoin(page_url, u))
        if not urls: continue
        a = img.find_parent("a")
        kind = link_kind(a.get("href") if a else None, page_url)
        alt = (img.get("alt") or "").strip()
        order += 1
        for u in urls:
            found.setdefault(key_of(u), []).append({"kind": kind, "href": str((a.get("href") if a else "") or "")[:120], "alt": alt[:80], "order": order})
    return found

rows = []; nohtml = set()
for r in truth:
    if r["stage"] not in ("kept", "ai"): continue
    rows.append(r)
cache = {}
for r in rows:
    if r["key"] not in cache: cache[r["key"]] = scan(r["key"])
    found = cache[r["key"]]
    if found is None: r["link"] = "no-html"; nohtml.add(r["key"]); continue
    hits = found.get(key_of(r["url"])) or found.get(key_of(r.get("fetched_url", r["url"])))
    if not hits: r["link"] = "not-in-html"; continue
    kinds = {h["kind"] for h in hits}
    # an image shown in several places: linked elsewhere counts only if every occurrence is
    r["link"] = "other-page" if kinds == {"other-page"} else ("mixed" if "other-page" in kinds else "not-linked")
    r["alt"] = hits[0]["alt"]; r["href"] = hits[0]["href"]; r["order"] = min(h["order"] for h in hits)
print("pages without html:", sorted(nohtml))
c = collections.Counter((r["link"], r["truth"]) for r in rows)
for k in sorted(c): print(k, c[k])
json.dump(rows, open("/tmp/st/v2/link-rows.json", "w"))
