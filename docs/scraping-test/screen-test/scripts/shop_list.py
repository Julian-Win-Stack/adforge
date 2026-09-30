"""The shop's own list of the product's photos, no model: Shopify's <product-link>.json, Amazon's
gallery data in the page, and the JSON-LD Product images. Writes /tmp/sp/shop/<key>.json."""
import html as htmllib, json, re
from pathlib import Path
from urllib.parse import urlparse, urljoin
import httpx
from bs4 import BeautifulSoup

ROOT = Path("/tmp/sp")
OUT = ROOT / "shop"; OUT.mkdir(exist_ok=True)
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"}


def products(data):
    if isinstance(data, list): return [p for i in data for p in products(i)]
    if not isinstance(data, dict): return []
    if "@graph" in data: return products(data["@graph"])
    t = data.get("@type"); t = t if isinstance(t, list) else [t]
    return [data] if ("Product" in t or "ProductGroup" in t) else []


def image_urls(image):
    if isinstance(image, str): return [image]
    if isinstance(image, dict): return image_urls(image.get("url") or image.get("contentUrl"))
    if isinstance(image, list): return [u for i in image for u in image_urls(i)]
    return []


def shopify(url, raw):
    if not re.search(r"cdn\.shopify\.com|/cdn/shop/|Shopify\.theme", raw or ""):
        return None
    p = urlparse(url)
    m = re.search(r"(/products/[^/?#]+)", p.path)
    if not m:
        return {"error": "no /products/ path"}
    try:
        r = httpx.get(f"{p.scheme}://{p.netloc}{m.group(1)}.json", headers=HEADERS, timeout=20, follow_redirects=True)
        prod = r.json()["product"]
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    text = BeautifulSoup(prod.get("body_html") or "", "html.parser").get_text("\n", strip=True)
    return {"title": prod.get("title"), "photos": [i["src"] for i in prod.get("images") or []],
            "text": text, "options": prod.get("options")}


def amazon(raw):
    m = re.search(r"'colorImages':\s*\{\s*'initial':\s*(?:A\.\$\.parseJSON\(')?(\[\{.*?\}\])", raw or "", re.S)
    if not m:
        return None
    try:
        items = json.loads(m.group(1).replace("\\'", "'"))
    except json.JSONDecodeError:
        return {"error": "colorImages not JSON"}
    return {"photos": [i.get("hiRes") or i.get("large") for i in items if i.get("hiRes") or i.get("large")]}


def json_ld(url, raw):
    soup = BeautifulSoup(raw or "", "html.parser")
    found, desc = [], ""
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.get_text())
        except json.JSONDecodeError:
            continue
        for p in products(data):
            found += image_urls(p.get("image"))
            for v in p.get("hasVariant") or []:
                found += image_urls(v.get("image"))
            desc = desc or htmllib.unescape(str(p.get("description") or ""))
    urls = []
    for u in found:
        u = urljoin(url, u.strip())
        if u not in urls: urls.append(u)
    return {"photos": urls, "text": desc}


for f in sorted((ROOT / "pages").glob("*.json")):
    page = json.load(open(f))
    raw = page.get("rawHtml") or ""
    res = {"shopify": shopify(page["url"], raw), "amazon": amazon(raw), "json_ld": json_ld(page["url"], raw)}
    json.dump(res, open(OUT / f.name, "w"), indent=1)
    print(f.stem, {k: (len(v["photos"]) if v and "photos" in v else v) for k, v in res.items()}, flush=True)
print("SHOP DONE")
