"""Estimate resale value from current Marktplaats listings.

Marktplaats does not publish sold prices, so this uses asking prices of comparable
listings (title must contain every word of the query), removes outliers and takes the median.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

from .util import normalize, phrase_in, token_in

log = logging.getLogger(__name__)

API = "https://www.marktplaats.nl/lrp/api/search"
PRICED_TYPES = {"FIXED", "MIN_BID"}  # FAST_BID / SEE_DESCRIPTION / FREE carry no usable price
ALWAYS_EXCLUDE = ["gezocht", "zoek", "defect", "kapot", "onderdelen", "reparatie", "repair",
                  "ruilen", "huur", "verhuur", "hoesje", "case", "cover", "sticker", "skin"]


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

    @property
    def url(self) -> str:
        return f"https://www.marktplaats.nl/q/{quote(self.query.replace(' ', '+'), safe='+')}/"


def comparable_listings(listings: list[dict], query: str, exclude: list[str]) -> list[dict]:
    """Listings whose title contains every word of the query and none of the excluded words."""
    excl = [x for x in (exclude or []) if x] + [w for w in ALWAYS_EXCLUDE if not token_in(w, normalize(query))]
    out = []
    for li in listings:
        info = li.get("priceInfo") or {}
        if info.get("priceType") not in PRICED_TYPES or not info.get("priceCents"):
            continue
        title = li.get("title") or ""
        norm = normalize(title)
        if not phrase_in(query, norm) or any(token_in(x, norm) for x in excl):
            continue
        vip = li.get("vipUrl") or ""
        out.append({
            "price": round(info["priceCents"] / 100, 2),
            "title": title.strip()[:90],
            "url": ("https://www.marktplaats.nl" + vip) if vip.startswith("/") else (vip or None),
            "kind": "bid" if info.get("priceType") == "MIN_BID" else "fixed",  # "bieden vanaf" vs asking price
        })
    return out


def comparable_prices(listings: list[dict], query: str, exclude: list[str]) -> list[float]:
    return [x["price"] for x in comparable_listings(listings, query, exclude)]


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


def best_estimate(listings: list[dict], queries: list[str], exclude: list[str],
                  min_count: int = 4) -> PriceEstimate | None:
    """Try the queries from most to least specific on the same search results."""
    for q in queries:
        est = summarize(comparable_listings(listings, q, exclude), q, min_count)
        if est:
            return est
    return None
