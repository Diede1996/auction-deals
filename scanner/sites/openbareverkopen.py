"""Openbare-verkopen.be (Wielsbeke, Belgium): curator sales, business closures and liquidations in Flanders.

1. /auctions lists the auctions as cards ("div.auction-teaser": end time, title, place, number of lots).
   Running auctions come first, then the headings "upcoming" and "ended".
2. The auction page (/auction/<id>) has the description, which often says why it's sold
   ("uit diverse falingen" = from several bankruptcies). Only bankruptcy and closure auctions are read.
3. /lot-loader/auction/<id>?page=N is the JSON the page loads its lots from: titles, highest bid,
   number of bids, closing time and photo per lot, 50 lots per page.
4. /auction/<id>/viewing-and-collection-days has the pickup days and address.

Buyer's costs: 17% or 19% (stated per lot) + 21% VAT; 19% is used to be safe.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from ..models import Auction, Lot
from ..util import parse_money
from .base import SiteContext, short_when, text_lines

log = logging.getLogger(__name__)

SITE = "openbareverkopen"
BASE = "https://www.openbare-verkopen.be"
_DATE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")
_POSTCODE_TOWN = re.compile(r"^\d{4}\s+\S")


def parse_auctions(html: str) -> list[Auction]:
    """The running auctions (cards before the "upcoming" and "ended" headings)."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, Auction] = {}
    for card in soup.select("div.auction-teaser"):
        if card.find_previous(["h2", "h3"], id=re.compile(r"^(upcoming|ended)$")):
            continue
        link = card.select_one("a.link-overlay[href*='/auction/']") or card.select_one("a[href*='/auction/']")
        m = re.search(r"/auction/(\d+)", link.get("href", "")) if link else None
        if not m or m.group(1) in out:
            continue
        title_el = card.select_one(".field--name-title")
        time_el = card.select_one("time[datetime]")
        places = [li.get_text(" ", strip=True) for li in card.select(".location-lots li")]
        place = places[0] if places else ""
        out[m.group(1)] = Auction(
            site=SITE,
            auction_id=m.group(1),
            title=title_el.get_text(" ", strip=True) if title_el else "",
            url=f"{BASE}/auction/{m.group(1)}",
            closes_at=_iso(time_el["datetime"]) if time_el else None,
            pickup=f"{place}, België" if place else None,  # until the pickup page says more
            town=re.sub(r"^\d{4}\s+", "", place),
        )
    return list(out.values())


def parse_description(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one(".field--name-body")
    return body.get_text(" ", strip=True) if body else ""


def parse_pickup(html: str) -> tuple[str | None, str | None]:
    """(address, first pickup day) from the viewing and collection days page."""
    lines = text_lines(html)
    start = next((i for i, line in enumerate(lines) if re.match(r"^(ophaaldag|afhaaldag)", line, re.I)), None)
    if start is None:
        return None, None
    when = None
    address: list[str] = []
    for line in lines[start + 1:start + 15]:
        dates = list(_DATE.finditer(line))
        if dates:
            if when is None:  # "13/10/2026 - 10:00 tot 12:0014/10/2026 - ..." -> the first day only
                first = line[dates[0].start():dates[1].start() if len(dates) > 1 else None]
                when = short_when(first.replace("/", "-"))
            continue
        if address and _POSTCODE_TOWN.match(line):
            address.append(line)
            break
        if re.search(r"\d", line) and re.search(r"[A-Za-z]", line) and len(line) < 60:
            address = [line]  # street and number
        elif address:
            address = []  # not followed by "8790 Waregem": not an address
    full = f"{address[0]}, {address[1]}, België" if len(address) == 2 else None
    return full, when


def parse_lots(data: dict, auction: Auction, now: datetime) -> list[Lot]:
    """Lots from one page of the lot-loader JSON that are still running."""
    bids = data.get("lot_number_of_bids") or {}
    lots = []
    for lot_id, lot in (data.get("lots") or {}).items():
        if not isinstance(lot, dict):
            continue
        ends = lot.get("lot_end_time")
        closes = datetime.fromtimestamp(int(ends), tz=timezone.utc) if str(ends or "").isdigit() else auction.closes_at
        if closes and closes < now:
            continue
        titles = lot.get("titles") or {}
        title = (titles.get("nl") or next(iter(titles.values()), "") or "").strip()
        if not title:
            continue
        bid = parse_money(lot.get("amount")) or parse_money(lot.get("start_amount"))
        image = lot.get("lot_image") or ""
        n = bids.get(str(lot_id)) if isinstance(bids, dict) else None
        descriptions = lot.get("descriptions") or {}
        desc = descriptions.get("nl") or next(iter(descriptions.values()), "") or ""
        desc = re.sub(r"\s+", " ", BeautifulSoup(desc, "html.parser").get_text(" ")).strip()[:300]
        lots.append(Lot(
            site=SITE,
            lot_id=str(lot.get("lot_id") or lot_id),
            title=title,
            url=f"{BASE}/lot/{lot.get('lot_id') or lot_id}",
            current_bid=bid,
            closes_at=closes,
            auction_title=auction.title,
            image=(BASE + image) if image.startswith("/") else (image or None),
            bids=int(n) if isinstance(n, (int, float)) or str(n or "").isdigit() else None,
            location=auction.town or None,
            description=desc,
        ).pickup_from(auction))
    return lots


def fetch_lots(ctx: SiteContext) -> list[Lot]:
    auctions = [a for a in parse_auctions(ctx.http.text(f"{BASE}/auctions"))
                if a.closes_at is None or a.closes_at > ctx.now]
    known = ctx.cache.setdefault("bankrupt", {})  # auction id -> True/False: descriptions don't change
    for key in [k for k in known if k not in {a.auction_id for a in auctions}]:
        del known[key]
    wanted = []
    for a in auctions:
        if a.auction_id not in known:
            known[a.auction_id] = ctx.is_bankruptcy(f"{a.title} {parse_description(ctx.http.text(a.url))}")
        if known[a.auction_id]:
            wanted.append(a)
    log.info("openbareverkopen: %d running auctions, %d bankruptcy/closure", len(auctions), len(wanted))
    lots: list[Lot] = []
    for a in wanted:
        try:
            address, when = parse_pickup(ctx.http.text(f"{a.url}/viewing-and-collection-days"))
            a.pickup = address or a.pickup
            a.pickup_when = when
        except Exception as e:  # the lots matter more than the pickup details
            log.warning("openbareverkopen: no pickup details for %s: %s", a.auction_id, e)
        page, pages = 1, 1
        while page <= min(pages, ctx.max_pages):
            data = ctx.http.json(f"{BASE}/lot-loader/auction/{a.auction_id}?page={page}")
            lots.extend(parse_lots(data, a, ctx.now))
            pages = int(data.get("pages") or 1)
            page += 1
    return lots


def _iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
