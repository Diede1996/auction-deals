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


r = get(B + "/sitemap.xml")
locs = re.findall(r"<loc>(.*?)</loc>", r.text)
print(len(locs), "sitemaps;", Counter(re.sub(r"[0-9a-f-]{36}", "<id>", l) for l in locs).most_common(30))
auc = [l for l in locs if "auction" in l.lower()]
print("auction sitemaps:", auc[:10])
if auc:
    r = get(auc[0])
    urls = re.findall(r"<loc>(.*?)</loc>", r.text)
    print(len(urls), "urls; first:", urls[:8])
    print(r.text[:1500])
slug = sys.argv[1]
r = get(f"{B}/nl/a/{slug}")
lots = next_data(r.text).get("lots") or {}
res = lots.get("results") or []
print("lots keys:", list(lots.keys()), "n", len(res))
if res:
    print(json.dumps(res[0], ensure_ascii=False, indent=1)[:5000])
r = get(f"{B}/nl/a/{slug}?page=2")
l2 = next_data(r.text).get("lots") or {}
print("page 2:", l2.get("pageNumber"), len(l2.get("results") or []), [x.get("displayId") for x in (l2.get("results") or [])[:3]])
