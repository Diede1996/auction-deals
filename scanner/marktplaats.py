"""Estimate resale value from current Marktplaats listings.

Marktplaats does not publish sold prices, so this uses asking prices of comparable
listings, removes outliers and takes the median. What counts as comparable is decided by a Rule
from identify.py: the same model number for an exact comparison, or the same words for a rough one.
"""
from __future__ import annotations

import html
import logging
import re
import statistics
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

from .identify import BRANDS, MULTI_BRANDS, Rule
from .util import normalize, token_in

log = logging.getLogger(__name__)

API = "https://www.marktplaats.nl/lrp/api/search"
PRICED_TYPES = {"FIXED", "MIN_BID"}  # FAST_BID / SEE_DESCRIPTION / FREE carry no usable price
ALWAYS_EXCLUDE = ["gezocht", "gevraagd", "zoek", "defect", "kapot", "onderdelen", "reparatie", "repair",
                  "ruilen", "huur", "verhuur", "hoesje", "case", "cover", "sticker", "skin",
                  "veiling",  # "Online Veiling: ..." = auction houses advertising their own lots
                  # accessories and parts sold under the device's name ("Apple Smart Keyboard iPad Pro 10.5")
                  "hoes", "hoezen", "sleeve", "keyboard", "tempered", "screenprotector", "screen protector",
                  "protector", "beschermglas", "glasfolie", "pencil", "stylus", "folio", "bookcase", "book case",
                  "dock", "houder", "digitizer", "behuizing", "moederbord", "logic board",
                  "logicboard", "geschikt voor", "compatible", "compatibel", "converter",
                  # cases and keyboards for tablets
                  "incipio", "otterbox", "spigen", "zagg", "logitech", "tech21", "uag",
                  # not the original brand: "Makita BL1815N ... 18V accu, 123accu huismerk"
                  "huismerk", "vervangend", "vervangende", "vervangt", "replacement", "namaak", "imitatie"]
# "zonder accu", "excl. lader": what a listing does NOT include says nothing about what it is
_WITHOUT = re.compile(r"(?<![a-z0-9])(?:zonder|excl|exclusief|geen|without|excluding)\s+[a-z0-9]+")
# "... voor Einhell", "... for Makita": something made for a product of that brand
_FOR_BRAND = "|".join(sorted({re.escape(b) for b in BRANDS + MULTI_BRANDS}, key=len, reverse=True))


MAX_LISTINGS = 30  # cheapest comparable listings kept for the dashboard


@dataclass
class PriceEstimate:
    median: float
    low: float  # 25th percentile
    high: float  # 75th percentile
    count: int
    query: str
    prices: list = field(default_factory=list)  # every comparable asking price (outliers removed), sorted
    outliers: list = field(default_factory=list)  # prices left out as outliers
    listings: list = field(default_factory=list)  # cheapest comparable listings: {price, title, url, kind}
    as_of: str | None = None  # when Marktplaats was checked (ISO time)
    kind: str = "general"  # "exact" (same model), "custom" (your own search) or "general" (rough)
    model: str | None = None  # the type number compared, e.g. "S27C366EAU"
    search: str | None = None  # what was typed into Marktplaats

    @property
    def url(self) -> str:
        return search_url(self.search or self.query)


def search_url(text: str) -> str:
    return f"https://www.marktplaats.nl/q/{quote(normalize(text).replace(' ', '+'), safe='+')}/"


def comparable_listings(listings: list[dict], rule: Rule, exclude: list[str]) -> list[dict]:
    """Listings that meet the rule and contain none of the excluded words."""
    label = normalize(rule.label)
    excl = [x for x in (exclude or []) if x] + [w for w in ALWAYS_EXCLUDE if not token_in(w, label)]
    # "Hoes voor iPad 6", "Accu voor Makita", "Adapter voor Einhell": something for a product, not the product
    first = label.split()[0] if label else ""
    targets = (re.escape(first) + "|" if first else "") + _FOR_BRAND
    made_for = re.compile(rf"(?<![a-z0-9])(?:voor|for)(?: de| het| apple)? (?:{targets})(?![a-z0-9])")
    out, seen = [], set()
    for li in listings:
        info = li.get("priceInfo") or {}
        if info.get("priceType") not in PRICED_TYPES or not info.get("priceCents"):
            continue
        title = html.unescape(li.get("title") or "")  # "iPad Pro 10.5&quot;" -> 'iPad Pro 10.5"'
        norm = _WITHOUT.sub(" ", normalize(title))
        if not rule.matches(norm) or any(token_in(x, norm) for x in excl) or (made_for and made_for.search(norm)):
            continue
        vip = li.get("vipUrl") or ""
        key = (li.get("itemId") or vip, title, info.get("priceCents"))
        if key in seen:  # the same listing found by both searches
            continue
        seen.add(key)
        out.append({
            "price": round(info["priceCents"] / 100, 2),
            "title": title.strip()[:90],
            "url": ("https://www.marktplaats.nl" + vip) if vip.startswith("/") else (vip or None),
            "kind": "bid" if info.get("priceType") == "MIN_BID" else "fixed",  # "bieden vanaf" vs asking price
        })
    return out


def summarize(found: list, query: str, min_count: int = 4) -> PriceEstimate | None:
    """found: comparable listings (dicts with a "price") or plain prices."""
    items = [x if isinstance(x, dict) else {"price": float(x)} for x in found]
    if len(items) < min_count:
        return None
    prices = sorted(x["price"] for x in items)
    q1, _, q3 = statistics.quantiles(prices, n=4)
    iqr = q3 - q1
    lo_fence, hi_fence = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    kept = [p for p in prices if lo_fence <= p <= hi_fence]
    if not kept:
        kept, lo_fence, hi_fence = prices, float("-inf"), float("inf")
    if len(kept) < min_count:
        return None
    outliers = [p for p in prices if not lo_fence <= p <= hi_fence]
    lo, _, hi = statistics.quantiles(kept, n=4)
    shown = sorted((x for x in items if lo_fence <= x["price"] <= hi_fence and x.get("title")),
                   key=lambda x: x["price"])[:MAX_LISTINGS]
    return PriceEstimate(median=round(statistics.median(kept), 2), low=round(lo, 2), high=round(hi, 2),
                         count=len(kept), query=query, prices=[round(p, 2) for p in kept],
                         outliers=[round(p, 2) for p in outliers], listings=shown)


def cache_key(query: str, exclude: list[str]) -> str:
    return normalize(query) + "|" + ",".join(sorted(normalize(x) for x in exclude or []))


def search(http, query: str) -> list[dict]:
    """One Marktplaats search (titles only, 100 results)."""
    params = {"query": query, "limit": 100, "offset": 0, "searchInTitleAndDescription": "false",
              "viewOptions": "list-view"}
    return http.json(f"{API}?{urlencode(params)}").get("listings") or []


def best_estimate(listings: list[dict], rules: list[Rule], exclude: list[str],
                  min_count: int = 4) -> tuple[PriceEstimate, Rule] | None:
    """Try the rules from most to least specific on the same search results."""
    for rule in rules:
        est = summarize(comparable_listings(listings, rule, exclude), rule.label, min_count)
        if est:
            return est, rule
    return None
