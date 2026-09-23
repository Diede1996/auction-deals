"""Troostwijk Auctions (troostwijkauctions.com).

The site is a Next.js app: every page embeds its data as JSON in __NEXT_DATA__.
1. List running auctions per country and keep the bankruptcy ones (by name).
2. Use the site's own lot search for every watchlist phrase and keep lots that
   belong to one of those bankruptcy auctions (lot displayId "A1-50096-12" -> auction "A1-50096").
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from ..models import Auction, Lot
from ..util import from_epoch
from .base import SiteContext, next_data

log = logging.getLogger(__name__)

SITE = "troostwijk"
BASE = "https://www.troostwijkauctions.com"


def list_auctions(ctx: SiteContext, country: str) -> list[Auction]:
    auctions: list[Auction] = []
    page = 1
    while page <= ctx.max_pages:
        url = f"{BASE}/nl/auctions?{urlencode({'countries': country, 'page': page})}"
        props = next_data(ctx.http.text(url))["props"]["pageProps"]
        items = props.get("listData") or []
        for a in items:
            auctions.append(parse_auction(a))
        total = props.get("totalSize") or 0
        size = props.get("pageSize") or len(items) or 48
        if not items or page * size >= total:
            break
        page += 1
    return auctions


def parse_auction(a: dict) -> Auction:
    return Auction(
        site=SITE,
        auction_id=a.get("displayId") or a.get("id"),
        title=a.get("name") or "",
        url=f"{BASE}/nl/a/{a.get('urlSlug', '')}",
        description=a.get("description") or "",
        closes_at=from_epoch(a.get("minEndDate") or a.get("endDate")),
    )


def auction_display_id(lot_display_id: str) -> str:
    """'A1-50096-100091' -> 'A1-50096'."""
    return lot_display_id.rsplit("-", 1)[0] if lot_display_id and lot_display_id.count("-") >= 2 else lot_display_id


def parse_lot(d: dict, auction_title: str = "") -> Lot:
    bid = (d.get("currentBidAmount") or {}).get("cents")
    loc = d.get("location") or {}
    return Lot(
        site=SITE,
        lot_id=d.get("displayId") or d.get("id"),
        title=(d.get("title") or "").strip(),
        url=f"{BASE}/nl/l/{d.get('urlSlug', '')}",
        current_bid=(bid / 100) if isinstance(bid, (int, float)) else None,
        closes_at=from_epoch(d.get("endDate")),
        auction_title=auction_title,
        image=(d.get("image") or {}).get("url"),
        location=(loc.get("city") or "").title() or None,
        bids=d.get("bidsCount"),
    )


def search(ctx: SiteContext, term: str, country: str, max_pages: int = 3) -> list[dict]:
    results: list[dict] = []
    for page in range(1, max_pages + 1):
        url = f"{BASE}/nl/search?{urlencode({'searchTerm': term, 'countries': country, 'page': page})}"
        props = next_data(ctx.http.text(url))["props"]["pageProps"]
        lots = props.get("lots") or []
        if isinstance(lots, dict):  # defensive: older layout used {results: [...]}
            lots = lots.get("results") or []
        results.extend(lots)
        total = props.get("searchTotalSize") or 0
        size = props.get("pageSize") or 48
        if not lots or page * size >= total:
            break
    return results


def fetch_lots(ctx: SiteContext) -> list[Lot]:
    countries = ctx.settings.get("countries") or ["nl"]
    bankrupt: dict[str, Auction] = {}
    for country in countries:
        for a in list_auctions(ctx, country):
            if ctx.is_bankruptcy(f"{a.title} {a.description}"):
                bankrupt[a.auction_id] = a
    log.info("troostwijk: %d bankruptcy auctions", len(bankrupt))
    if not bankrupt or not ctx.search_terms:
        return []

    lots: dict[str, Lot] = {}
    pages = int(ctx.settings.get("search_pages", 3))
    for term in ctx.search_terms:
        for country in countries:
            for d in search(ctx, term, country, pages):
                auction = bankrupt.get(auction_display_id(d.get("displayId", "")))
                if not auction or d.get("biddingStatus") not in (None, "BIDDING_OPEN"):
                    continue
                lot = parse_lot(d, auction.title)
                lots[lot.key] = lot
    return list(lots.values())
