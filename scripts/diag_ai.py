"""One-off test of scanner/ai_query.py on real lots without a price (thread 'Uitzoeken waarom zo weinig kavels
matchen'): what Claude names, and how many lots then get a Marktplaats price."""
import json, random
from datetime import datetime, timezone
import yaml
from scanner.http import Http
from scanner.models import WatchItem, Lot
from scanner.pricing import PriceFinder
from scanner.claude_check import client_from_env
from scanner import ai_query

wl = yaml.safe_load(open("watchlist.yml"))
items = {i.name: i for i in (WatchItem.from_dict(d) for d in wl["items"]) if i.keywords}
desc = json.load(open("data/state.json")).get("descriptions") or {}
rows = []
for l in json.load(open("data/lots.json"))["lots"]:
    it = items.get(l["item"])
    if l["market"] is not None or not it or it.market_price is not None or not it.price_check or it.always_show:
        continue
    site, lid = l["key"].split(":", 1)
    rows.append((it, Lot(site=site, lot_id=lid, title=l["title"], url=l["url"], current_bid=l["bid"], closes_at=None,
                         description=(desc.get(l["key"]) or {}).get("d", "")), None, None))
random.seed(11)
rows = random.sample(rows, min(40, len(rows)))
now = datetime.now(timezone.utc)
client = client_from_env()
print("client:", bool(client), "lots:", len(rows))
cache = {}
answers = ai_query.answers_for([r[1] for r in rows], cache, now, client, 40)
for it, lot, _, _ in rows:
    a = answers.get(lot.key)
    p = ai_query.plan_from(a)
    print(f"NAME | {lot.title[:60]} -> {a and (a['brand'], a['model'], a['type'], a['part'])} | search {p and p.searches[0]}")
finder = PriceFinder(Http(delay=6.0, jitter=2.0), {}, now, max_lookups=80)
out, plans = ai_query.reprice(rows, finder, cache, now, client, lambda i, l, e: "priced", lambda l: True)
for it, lot, v, est in out:
    if est:
        print(f"PRICED | {lot.title[:60]} | {plans[lot.key].searches[0]} | median {est.median} from {est.count}")
print("lookups", finder.lookups, "blocked", finder.blocked, "priced", len(plans), "of", len(rows))
