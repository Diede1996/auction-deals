"""One-off test (thread 'Uitzoeken waarom zo weinig kavels matchen'): does a search with the product type in it
('makita dhr264 boorhamer') find more comparable private listings than the bot's own searches?"""
import json, re, sys
from types import SimpleNamespace
import yaml
from scanner.http import Http
from scanner.models import WatchItem, Lot
from scanner.identify import plan_for, model_in
from scanner import marktplaats as mp
from scanner.util import normalize
sys.path.insert(0, "scripts")
from diag_unpriced_lib import reasons

# key, good query, model as sellers write it, product-type words (regex)
TESTS = [
 ("onlineveilingmeester:1921779", "husqvarna 572 xp kettingzaag", "572 xp", "kettingzaag|zaag"),
 ("onlineveilingmeester:1923933", "makita dhs710 cirkelzaag", "dhs710", "cirkelzaag|zaag"),
 ("openbareverkopen:763491", "eureka j15 robotstofzuiger", "j15", "robot|stofzuiger"),
 ("hnvi:198087", "milwaukee m12 bd schroefboormachine", "m12 bd", "boor|schroef"),
 ("onlineveilingmeester:1923937", "makita dj182 decoupeerzaag", "dj182", "decoupeer|zaag"),
 ("hnvi:198186", "makita dml805 bouwlamp", "dml805", "lamp"),
 ("onlineveilingmeester:1923948", "makita rp1111c bovenfrees", "rp1111c", "frees"),
 ("onlineveilingmeester:1923932", "makita dga504 haakse slijper", "dga504", "slijp"),
 ("onlineveilingmeester:1924103", "stihl se 62 stofzuiger", "se 62", "zuig"),
 ("onlineveilingmeester:1926149", "husqvarna k4000 doorslijper", "k4000", "slijp|zaag"),
 ("proveiling:3297167", "karcher b 40 schrobzuigmachine", "b 40", "schrob"),
 ("proveiling:3297061", "bosch ggs 30 rechte slijper", "ggs 30", "slijp"),
 ("hnvi:198110", "metabo bz 9.6 sp schroefboormachine", "bz 9 6", "boor|schroef"),
 ("onlineveilingmeester:1923968", "makita dhr264 boorhamer", "dhr264", "hamer|boor"),
 ("onlineveilingmeester:1921704", "husqvarna automower 305e", "305e", "automower|robot|maaier"),
 ("proveiling:3297269", "bosch gws 660 haakse slijper", "gws 660", "slijp"),
 ("onlineveilingmeester:1924074", "husqvarna 525rxt bosmaaier", "525rxt", "bosmaaier|maaier|trimmer"),
 ("hnvi:198051", "hp zbook fury 16 g9", "fury 16 g9", "zbook|laptop"),
 ("onlineveilingmeester:1920793", "dewalt dch263 combihamer", "dch263", "hamer|boor"),
 ("hnvi:198074", "makita rp1800 bovenfrees", "rp1800", "frees"),
 ("proveiling:3297053", "bosch grl 150 hv rotatielaser", "grl 150", "laser"),
 ("hnvi:198128", "makita hg5012 heteluchtpistool", "hg5012", "hetelucht|fohn|foehn|fohn|pistool"),
 ("onlineveilingmeester:1924024", "stihl ht 75 stokzaag", "ht 75", "zaag|snoeier"),
 ("hnvi:198142", "makita dcl180 stofzuiger", "dcl180", "zuig|kruimel"),
 ("onlineveilingmeester:1921693", "husqvarna 110il kantenmaaier", "110il", "maaier|trimmer|blazer"),
]
wl = yaml.safe_load(open("watchlist.yml"))
items = {i.name: i for i in (WatchItem.from_dict(d) for d in wl["items"]) if i.keywords}
desc = json.load(open("data/state.json")).get("descriptions") or {}
lots = {l["key"]: l for l in json.load(open("data/lots.json"))["lots"]}
http = Http(delay=6.0, jitter=2.0)
cache = {}
def search(q):
    if q not in cache:
        cache[q] = mp.search(http, q)
    return cache[q]

class R:
    def __init__(self, label, fn): self.label, self.fn, self.new_ok, self.parts_ok, self.model = label, fn, False, False, label
    def matches(self, n): return self.fn(n)

tot = {"bot": 0, "type": 0, "rough": 0}; got = {"bot": 0, "type": 0, "rough": 0}
for key, good, model, typ in TESTS:
    l = lots.get(key)
    if not l: print("MISSING", key); continue
    it = items[l["item"]]
    site, lid = key.split(":", 1)
    lot = Lot(site=site, lot_id=lid, title=l["title"], url=l["url"], current_bid=l["bid"], closes_at=None,
              description=(desc.get(key) or {}).get("d", ""))
    plan = plan_for(it, lot)
    brand = good.split()[0]
    try:
        bot_res = []
        for q in plan.searches[:3 if plan.options or plan.kind == "exact" else 2]:
            bot_res += search(q)
        bot_best = max((reasons(bot_res, r, it.exclude)[0]["BRUIKBAAR"] for r in plan.rules), default=0)
        type_res = search(good) + search(f"{brand} {model}")
    except Exception as e:
        print("STOP", type(e).__name__, key); break
    mnorm = normalize(model)
    exact = R(f"{brand} {model}", lambda n: model_in(mnorm, n) or mnorm.replace(" ", "") in n.replace(" ", ""))
    typed, _ = reasons(type_res, exact, it.exclude)
    rough_rule = R(f"{brand} {typ}", lambda n: brand in n and re.search(typ, n) and not exact.matches(n))
    rough, _ = reasons(type_res, rough_rule, it.exclude)
    need = 2
    a, b, c = bot_best, typed["BRUIKBAAR"], rough["BRUIKBAAR"]
    for k, v in (("bot", a), ("type", b), ("rough", c)):
        tot[k] += v; got[k] += v >= (2 if k != "rough" else 4)
    print(f"LOT | {l['title'][:55]} | bot '{plan.searches[0]}' ({plan.model}) -> {a} | met type '{good}' -> {b} | "
          f"zelfde merk+type zonder typenr -> {c}")
print("\nTOTAL usable listings:", tot)
print("lots that get a price (>=2 exact, >=4 rough):", got, "of", len(TESTS))
