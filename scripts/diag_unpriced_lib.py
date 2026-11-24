import collections, html, re
from scanner.identify import is_part_listing, says_new
from scanner.matching import printed_matter
from scanner import marktplaats as mp
from scanner.util import normalize, token_in

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

