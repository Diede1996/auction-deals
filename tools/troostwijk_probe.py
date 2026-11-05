"""Print the structure of one Troostwijk lot page and its auction page (two requests, 10 s apart)."""
import json, re, sys, time
import requests

UA = "auction-deals personal bot (+https://github.com/diede1996/auction-deals)"
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "nl-NL,nl;q=0.9"})


def shape(o, depth=0, maxd=4):
    if depth > maxd:
        return "..."
    if isinstance(o, dict):
        return {k: shape(v, depth + 1, maxd) for k, v in list(o.items())[:40]}
    if isinstance(o, list):
        return [f"list[{len(o)}]", shape(o[0], depth + 1, maxd)] if o else "list[0]"
    if isinstance(o, str):
        return o[:80]
    return o


def get(url):
    r = S.get(url, timeout=30)
    print(f"\n=== {url}\nHTTP {r.status_code}, {len(r.text)} chars, server={r.headers.get('server')}")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        print("no __NEXT_DATA__; head:", r.text[:1500])
        return r.text, None
    data = json.loads(m.group(1))
    print(json.dumps(shape(data.get("props", {}).get("pageProps", {})), ensure_ascii=False, indent=1)[:12000])
    return r.text, data


lot = sys.argv[1]
text, data = get(f"https://www.troostwijkauctions.com/nl/l/{lot}")
slug = (((data or {}).get("props") or {}).get("pageProps") or {}).get("auction", {}).get("urlSlug")
print("\nauction urlSlug:", slug)
if slug:
    time.sleep(10)
    t2, d2 = get(f"https://www.troostwijkauctions.com/nl/a/{slug}")
    pp = ((d2 or {}).get("props") or {}).get("pageProps") or {}
    for k, v in pp.items():
        if isinstance(v, dict) and any(isinstance(x, list) and len(x) > 5 for x in v.values()):
            print("candidate", k, {kk: (len(vv) if isinstance(vv, list) else vv) for kk, vv in v.items() if not isinstance(vv, dict)})
time.sleep(10)
r = S.get("https://www.troostwijkauctions.com/sitemap.xml", timeout=30)
print("\n=== sitemap HTTP", r.status_code, len(r.text)); print(r.text[:3000])
