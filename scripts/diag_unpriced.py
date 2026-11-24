"""One-off diagnosis (thread 'Uitzoeken waarom zo weinig kavels matchen'): for lots on the public dashboard
without a Marktplaats price, run the same searches and count why listings were not used as comparisons."""
import collections, html, json, os, random, re, sys
from datetime import datetime
import yaml
from scanner.http import Http
from scanner.models import WatchItem, Lot
from scanner.identify import plan_for, is_part_listing, says_new
from scanner.matching import printed_matter
from scanner import marktplaats as mp
from scanner.util import normalize, token_in

N = int(os.environ.get("N", "60"))
wl = yaml.safe_load(open("watchlist.yml"))
items = {i.name: i for i in (WatchItem.from_dict(d) for d in wl["items"]) if i.keywords}
st = json.load(open("data/state.json"))
desc = st.get("descriptions") or {}
lots = json.load(open("data/lots.json"))["lots"]
cand, seenq = [], set()
for l in lots:
    it = items.get(l["item"])
    if l["market"] is not None or not it or it.market_price is not None or not it.price_check:
        continue
    site, lid = l["key"].split(":", 1)
    lot = Lot(site=site, lot_id=lid, title=l["title"], url=l["url"], current_bid=l["bid"], closes_at=None,
              description=(desc.get(l["key"]) or {}).get("d", ""))
    plan = plan_for(it, lot)
    if not plan or not plan.rules or plan.searches[0] in seenq:
        continue
    seenq.add(plan.searches[0])
    cand.append((it, l, plan))
random.seed(7)
sample = random.sample(cand, min(N, len(cand)))
print(f"{len(cand)} distinct unpriced searches, checking {len(sample)}")
http = Http(delay=3.0)
cache = {}
def search(q):
    if q not in cache:
        cache[q] = mp.search(http, q)
    return cache[q]

def reasons(listings, rule, exclude):
    label = normalize(rule.label)
    excl = [x for x in (exclude or []) if x] + [w for w in mp.ALWAYS_EXCLUDE if not token_in(w, label)]
    first = label.split()[0] if label else ""
    targets = (re.escape(first) + "|" if first else "") + mp._FOR_BRAND
    made_for = re.compile(rf"(?<![a-z0-9])(?:voor|for)(?: de| het| apple)? (?:{targets})(?![a-z0-9])")
    c = collections.Counter(); words = collections.Counter(); seen = set()
    for li in listings:
        info = li.get("priceInfo") or {}
        title = html.unescape(li.get("title") or "")
        norm = mp._WITHOUT.sub(" ", normalize(title))
        if not rule.matches(norm):
            c["ander product/model"] += 1; continue
        if info.get("priceType") not in mp.PRICED_TYPES or not info.get("priceCents"):
            c["geen prijs (bieden/zie omschr.)"] += 1; continue
        hit = [x for x in excl if token_in(x, norm)]
        if hit:
            c["uitgesloten woord"] += 1; words.update(hit); continue
        if made_for.search(norm):
            c["'voor <merk>' (accessoire)"] += 1; continue
        cond = mp.condition_of(li)
        if mp.is_business(li):
            c["zakelijke verkoper"] += 1; continue
        if cond == "refurbished": c["refurbished"] += 1; continue
        if cond == "nieuw" and not rule.new_ok: c["conditie 'nieuw'"] += 1; continue
        if cond in ("niet werkend", "defect") and not rule.parts_ok: c["defect"] += 1; continue
        if not rule.parts_ok and is_part_listing(title, battery_ok=bool(rule.model)):
            c["onderdeel/accu"] += 1; continue
        if not rule.new_ok and says_new(norm):
            c["'nieuw' in titel"] += 1; continue
        if printed_matter(title): c["boek"] += 1; continue
        k = (normalize(title), info.get("priceCents"))
        if k in seen: c["dubbel"] += 1; continue
        seen.add(k); c["BRUIKBAAR"] += 1
    return c, words

tot = collections.Counter(); totw = collections.Counter(); zero_results = 0; would = collections.Counter()
for it, l, plan in sample:
    try:
        res = []
        for q in plan.searches[:3 if plan.options or plan.kind == "exact" else 2]:
            res += search(q)
    except Exception as e:
        print("ERR", l["title"][:60], type(e).__name__); continue
    if not res:
        zero_results += 1
    best = None
    for rule in plan.rules:
        c, w = reasons(res, rule, it.exclude)
        if best is None or c["BRUIKBAAR"] > best[0]["BRUIKBAAR"]:
            best = (c, w, rule)
    c, w, rule = best
    need = 2 if plan.exact else 4
    tot.update(c); totw.update(w)
    if c["BRUIKBAAR"] + c["zakelijke verkoper"] >= need: would["+zakelijk"] += 1
    if c["BRUIKBAAR"] + c["conditie 'nieuw'"] + c["'nieuw' in titel"] >= need: would["+nieuw"] += 1
    if c["BRUIKBAAR"] + c["zakelijke verkoper"] + c["conditie 'nieuw'"] + c["'nieuw' in titel"] >= need: would["+beide"] += 1
    if c["BRUIKBAAR"] + c["uitgesloten woord"] >= need: would["+uitgesloten"] += 1
    if c["BRUIKBAAR"] >= need: would["nu al genoeg?"] += 1
    print(f"LOT | {l['item']} | {l['title'][:70]} | {plan.kind} '{plan.searches[0]}' rule '{rule.label}' | "
          f"{len(res)} res | " + ", ".join(f"{k} {v}" for k, v in c.most_common()))
print("\nTOTAL listing reasons:", dict(tot.most_common()))
print("excluded words:", dict(totw.most_common(25)))
print("searches with 0 results:", zero_results, "of", len(sample))
print("lots that WOULD get a price if also counting:", dict(would))
