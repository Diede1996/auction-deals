"""Troostwijk Auctions (troostwijkauctions.com).

What we may read (checked with Diede on 5 Nov 2026): Troostwijk's robots.txt allows everything except the search
page and the filter parameters (/search, categoryLevel=, totalSize=, countries=, brands=, auctions=, amounts=),
and their terms don't forbid reading the site; they forbid making it publicly accessible (deeplinks included),
so the scan keeps these lots off the public dashboard (see private_repo.py).

So this reader:
1. Reads the list of running auctions (/nl/auctions?page=N, no filters) and keeps the auctions in your countries
   (from their pickup places) that pass the same auction filter as the other sites.
2. Reads those auctions' own pages (/nl/a/<slug>?page=N, 48 lots a page) and keeps the lots whose title contains a
   watchlist phrase. An auction's lots are read once and then again only while it closes within a day and a
   half (for the current bids), with at most `max_pages` auction pages a run.
It identifies itself honestly as a bot, waits a few seconds between pages and stops at the first refusal.
Never the search page.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

import requests

from ..http import BlockedError, looks_blocked
from ..models import Auction, Lot
from ..util import from_epoch, normalize, phrase_in
from .base import SiteContext, next_data

log = logging.getLogger(__name__)

SITE = "troostwijk"
BASE = "https://www.troostwijkauctions.com"
USER_AGENT = "auction-deals personal bot (+https://github.com/diede1996/auction-deals)"


class Reader:
    """A plain, honest HTTP client for Troostwijk: our own bot name, a pause between pages, no retries after a
    refusal."""

    def __init__(self, ctx: SiteContext, delay: float, session=None):
        self.ctx, self.delay, self.last = ctx, delay, 0.0
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "nl-NL,nl;q=0.9"})
        self.pages = 0

    def props(self, path: str) -> dict:
        wait = self.last + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        r = self.session.get(BASE + path, timeout=45)
        self.last = time.monotonic()
        self.pages += 1
        if hasattr(self.ctx.http, "request_count"):
            self.ctx.http.request_count += 1
        if r.status_code in (403, 429) or looks_blocked(r.text):
            raise BlockedError(f"troostwijk refused the bot ({r.status_code})")
        r.raise_for_status()
        return next_data(r.text)["props"]["pageProps"]


def list_auctions(reader: Reader, max_pages: int) -> list[dict]:
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        props = reader.props("/nl/auctions" + (f"?page={page}" if page > 1 else ""))
        items = props.get("listData") or []
        out.extend(items)
        size = props.get("pageSize") or len(items) or 48
        if not items or page * size >= (props.get("totalSize") or 0):
            break
    return out


def countries_of(a: dict) -> set[str]:
    return {(d.get("countryCode") or "").lower() for d in a.get("collectionDays") or [] if isinstance(d, dict)}


def town_of(a: dict, countries: set[str]) -> str:
    for d in a.get("collectionDays") or []:
        if isinstance(d, dict) and (d.get("countryCode") or "").lower() in countries and d.get("city"):
            return d["city"].title()
    return ""


def parse_auction(a: dict) -> Auction:
    return Auction(
        site=SITE,
        auction_id=a.get("displayId") or a.get("id"),
        title=a.get("name") or "",
        url=f"{BASE}/nl/a/{a.get('urlSlug', '')}",
        description=a.get("description") or "",
        closes_at=from_epoch(a.get("minEndDate") or a.get("endDate")),
    )


def lot_record(d: dict) -> dict:
    """What we keep of a lot from an auction page."""
    bid = (d.get("currentBidAmount") or {}).get("cents")
    loc = d.get("location") or {}
    return {"id": d.get("displayId") or d.get("id"), "title": (d.get("title") or "").strip(),
            "slug": d.get("urlSlug") or "", "bid": (bid / 100) if isinstance(bid, (int, float)) else None,
            "bids": d.get("bidsCount"), "end": d.get("endDate"), "image": (d.get("image") or {}).get("url"),
            "city": (loc.get("city") or "").title() or None, "country": (loc.get("countryCode") or "").lower(),
            "open": d.get("biddingStatus") in (None, "BIDDING_OPEN")}


def to_lot(rec: dict, auction_title: str, regular: bool = False) -> Lot:
    return Lot(
        site=SITE, lot_id=rec["id"], title=rec["title"], url=f"{BASE}/nl/l/{rec['slug']}",
        current_bid=rec.get("bid"), closes_at=from_epoch(rec.get("end")), auction_title=auction_title,
        image=rec.get("image"), location=rec.get("city"), bids=rec.get("bids"), regular=regular,
    )


def read_auction(reader: Reader, slug: str, pages_left: int) -> tuple[list[dict], bool]:
    """(the lots of one auction, read completely?)"""
    out: list[dict] = []
    page = 1
    while pages_left > 0:
        props = reader.props(f"/nl/a/{slug}" + (f"?page={page}" if page > 1 else ""))
        pages_left -= 1
        lots = props.get("lots") or {}
        results = lots.get("results") if isinstance(lots, dict) else lots
        out.extend(lot_record(d) for d in results or [])
        if not results or not (isinstance(lots, dict) and lots.get("hasNext")):
            return out, True
        page += 1
    return out, False


def wanted(title: str, phrases: list[str]) -> bool:
    text = normalize(title)
    return any(phrase_in(p, text) for p in phrases)


def fetch_lots(ctx: SiteContext, session=None) -> list[Lot]:
    s = ctx.settings
    countries = {c.lower() for c in s.get("countries") or ["nl"]}
    reader = Reader(ctx, float(s.get("delay_seconds", 4)), session)
    max_pages = int(s.get("max_pages", 120))
    phrases = [normalize(t) for t in ctx.search_terms if normalize(t)]
    cache = ctx.cache.setdefault("auctions", {})  # displayId -> {read: iso, lots: [records of watchlist lots]}
    now = ctx.now

    auctions = []
    for a in list_auctions(reader, int(s.get("list_pages", 15))):
        if a.get("biddingStatus") not in (None, "BIDDING_OPEN") or not (countries_of(a) & countries):
            continue
        auction = parse_auction(a)
        if not ctx.wants(auction, ctx.is_bankruptcy(auction.title, auction.description)):
            continue
        auctions.append((a, auction))
    # closing soonest first: their bids matter most, and new auctions are read before they close
    auctions.sort(key=lambda p: p[1].closes_at or now + timedelta(days=365))
    running = {auction.auction_id for _, auction in auctions}
    for gone in [k for k in cache if k not in running]:
        del cache[gone]

    lots: list[Lot] = []
    read = skipped = 0
    for a, auction in auctions:
        entry = cache.get(auction.auction_id)
        soon = auction.closes_at is not None and auction.closes_at - now < timedelta(hours=36)
        fresh = entry and entry.get("complete") and (
            not soon or datetime.fromisoformat(entry["read"]) > now - timedelta(hours=12))
        if not fresh:
            pages_left = max_pages - reader.pages
            if pages_left <= 0:
                skipped += 1
            else:
                records, complete = read_auction(reader, a.get("urlSlug", ""), pages_left)
                entry = cache[auction.auction_id] = {
                    "read": now.isoformat(), "complete": complete,
                    "lots": [r for r in records if r["open"] and r["title"] and wanted(r["title"], phrases)]}
                read += 1
        if not entry:
            continue
        regular = auction.title in ctx.regular
        town = town_of(a, countries)
        for rec in entry["lots"]:
            if rec.get("country") and rec["country"] not in countries:
                continue
            lot = to_lot(rec, auction.title, regular)
            lot.location = lot.location or town or None
            lots.append(lot)
    log.info("troostwijk: %d auctions in %s, %d read now (%d pages), %d left for the next scan, %d watchlist lots",
             len(auctions), "/".join(sorted(countries)), read, reader.pages, skipped, len(lots))
    return lots
