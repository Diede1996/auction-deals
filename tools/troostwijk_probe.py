"""Learn Troostwijk's page structure: sitemap index, an auctions sitemap, and one auction page's lots (a few
requests, 10 s apart, honest user agent; robots.txt allows these pages, never /search)."""
import json, re, sys, time
from collections import Counter
import requests

UA = "auction-deals personal bot (+https://github.com/diede1996/auction-deals)"
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "nl-NL,nl;q=0.9"})
B = "https://www.troostwijkauctions.com"


def next_data(text):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', text, re.S)
    return json.loads(m.group(1))["props"]["pageProps"] if m else {}


def get(url):
    time.sleep(8)
    r = S.get(url, timeout=30)
    print(f"\n=== {url}  HTTP {r.status_code}, {len(r.text)} chars")
    return r


def shape(o, depth=0, maxd=5):
    if depth > maxd:
        return "..."
    if isinstance(o, dict):
        return {k: shape(v, depth + 1, maxd) for k, v in list(o.items())[:50]}
    if isinstance(o, list):
        return [f"list[{len(o)}]", shape(o[0], depth + 1, maxd)] if o else "list[0]"
    if isinstance(o, str):
        return o[:100]
    return o


for page in ("", "?page=2"):
    r = get(f"{B}/nl/auctions{page}")
    pp = next_data(r.text)
    print("keys:", list(pp.keys()))
    for k, v in pp.items():
        if k in ("translations", "layout", "appLocaleData", "featureFlags", "menuCategories", "seoTags"):
            continue
        print(k, json.dumps(shape(v), ensure_ascii=False)[:4000])
