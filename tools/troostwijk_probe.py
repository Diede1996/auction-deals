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
slugs = sorted(set(re.findall(r'/a/([a-z0-9-]+-A1-\d+|[a-z0-9-]{8,})', text)))
print("\nauction links on lot page:", slugs[:10])
if slugs:
    time.sleep(10)
    get(f"https://www.troostwijkauctions.com/nl/a/{slugs[0]}")
