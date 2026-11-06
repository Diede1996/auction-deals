"""Temporary probe: what fields does a Marktplaats search listing have (newest first, near home)?"""
import json, os, re
from urllib.parse import urlencode
from scanner.http import Http
from scanner.marktplaats import API

http = Http(delay=3)
m = re.search(r"\b(\d{4})\s?[A-Za-z]{2}\b", os.environ.get("HOME_ADDRESS", ""))
pc = m.group(1) if m else ""
print("postcode found:", bool(pc))
for extra in ({}, {"sortBy": "SORT_INDEX", "sortOrder": "DECREASING"},
              {"sortBy": "SORT_INDEX", "sortOrder": "DECREASING", "postcode": pc, "distanceMeters": 25000}):
    params = {"query": "makita", "limit": 30, "offset": 0, "searchInTitleAndDescription": "false",
              "viewOptions": "list-view", **extra}
    data = http.json(f"{API}?{urlencode(params)}")
    ls = data.get("listings") or []
    print("\n=== params", {k: v for k, v in extra.items() if k != "postcode"}, "->", len(ls), "listings; top keys", sorted(data)[:30])
    if ls:
        li = ls[0]
        print("listing keys:", sorted(li))
        print("location:", {k: v for k, v in (li.get("location") or {}).items() if k not in ("latitude", "longitude")})
        si = li.get("sellerInformation") or {}
        print("seller keys:", sorted(si), {k: v for k, v in si.items() if isinstance(v, bool)})
        print("priceInfo:", li.get("priceInfo"), "| traits:", li.get("traits"), "| priorityProduct:", li.get("priorityProduct"))
        print("date:", li.get("date"), "| categoryId:", li.get("categoryId"), "| vipUrl:", li.get("vipUrl"))
        for x in ls[:15]:
            si = x.get("sellerInformation") or {}
            loc = x.get("location") or {}
            print(f"  {x.get('date')} | {x.get('priorityProduct')} | dist {loc.get('distanceMeters')} | {loc.get('cityName')} | "
                  f"{(x.get('priceInfo') or {}).get('priceCents')} {(x.get('priceInfo') or {}).get('priceType')} | "
                  f"web {si.get('showWebsiteUrl')} soi {si.get('showSoiUrl')} | traits {x.get('traits')} | {x.get('title')[:60]}")
