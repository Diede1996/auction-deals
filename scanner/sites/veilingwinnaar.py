"""Veilingwinnaar (veilingwinnaar.nl): closing restaurants, bakeries, butchers and gyms ("stopzetting",
"wegens bedrijfsbeëindiging"), mostly horeca equipment.

Plain pages (the "Four Auctions" software):
1. /auctions/ lists the auctions as cards: "384: <name>", "Sluitingsdatum: 6 oktober 2026 17:00".
2. /auctions/<id>/ has the description (why it's sold, "Locatie Amsterdam"), the pickup days and the lots,
   24 per page (?page=2, ...). Each lot card carries its current bid, number of bids and closing time
   as data attributes.

Buyer's costs: "De biedprijs is excl. 21 % BTW en 18% Veilingkosten".
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from ..models import Auction, Lot
from ..util import parse_dutch_datetime, parse_local_iso, parse_money
from .base import SiteContext, short_when

log = logging.getLogger(__name__)

SITE = "veilingwinnaar"
BASE = "https://veilingwinnaar.nl"
_TOWN = re.compile(r"\b(?:[Ll]ocatie|te)\s*:?\s+([A-Z][\w'’-]+(?:[- ](?:aan|op|den|de)?[- ]?[A-Z][\w'’-]+)?)")


def parse_auctions(html: str, now) -> list[Auction]:
    """Auctions that haven't closed yet."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, Auction] = {}
    for card in soup.select("div.auction-card"):
        link = card.select_one(".auction-card__title a[href]")
        m = re.search(r"/auctions/(\d+)/", link.get("href", "")) if link else None
        if not m or m.group(1) in out:
            continue
        text = card.get_text(" ", strip=True)
        closes = None
        cm = re.search(r"Sluitingsdatum:\s*(.+?\d{1,2}:\d{2})", text)
        if cm:
            closes = parse_dutch_datetime(cm.group(1))
        if closes and closes < now:
            continue
        out[m.group(1)] = Auction(
            site=SITE,
            auction_id=m.group(1),
            title=re.sub(r"^\d+:\s*", "", link.get_text(" ", strip=True)),
            url=f"{BASE}/auctions/{m.group(1)}/",
            closes_at=closes,
        )
    return list(out.values())


def parse_auction_page(html: str, auction: Auction) -> None:
    """Fill in the description, pickup day and town from the auction page."""
    soup = BeautifulSoup(html, "html.parser")
    desc = soup.select_one(".page-auction__description")
    auction.description = desc.get_text(" ", strip=True) if desc else ""
    for dt in soup.select(".page-auction__dates dt"):
        dd = dt.find_next_sibling("dd")
        if dd and re.match(r"(?i)ophaaldag", dt.get_text(strip=True)):
            auction.pickup_when = short_when(dd.get_text(" ", strip=True))
            break
    m = _TOWN.search(auction.description) or _TOWN.search(auction.title)
    if m:
        auction.town = m.group(1)
        auction.pickup = m.group(1)  # only the town is given: good enough for the trip estimate


def parse_lots(html: str, auction: Auction, now) -> list[Lot]:
    soup = BeautifulSoup(html, "html.parser")
    lots = []
    for card in soup.select("div.lot-card[data-lot-id]"):
        lot_id = card["data-lot-id"]
        link = card.select_one(".lot-card__title a[href]")
        info = card.select_one("[data-biddable-pk]")
        if not link or not lot_id.isdigit():
            continue
        closes = parse_local_iso(info.get("data-closing-date")) if info else None
        closes = closes or auction.closes_at
        if closes and closes < now:
            continue
        count = (info.get("data-bid-count") or "") if info else ""
        img = card.select_one("img[src]")
        href = link["href"]
        lots.append(Lot(
            site=SITE,
            lot_id=lot_id,
            title=link.get_text(" ", strip=True),
            url=href if href.startswith("http") else BASE + href,
            current_bid=parse_money(info.get("data-current-bid")) if info else None,
            closes_at=closes,
            auction_title=auction.title,
            image=img["src"] if img else None,
            location=auction.town or None,
            bids=int(count) if count.isdigit() else None,
        ).pickup_from(auction))
    return lots


def fetch_lots(ctx: SiteContext) -> list[Lot]:
    # Every running auction page is read each time (only a handful): announced auctions often say
    # "Informatie volgt zsm" at first and get their description and lots later.
    auctions = parse_auctions(ctx.http.text(f"{BASE}/auctions/"), ctx.now)
    ctx.cache.pop("bankrupt", None)
    lots: list[Lot] = []
    wanted = 0
    for a in auctions:
        html = ctx.http.text(a.url)
        parse_auction_page(html, a)
        if not ctx.is_bankruptcy(a.title, a.description):
            continue
        wanted += 1
        page, seen = 1, set()
        while True:
            new = [lot for lot in parse_lots(html, a, ctx.now) if lot.lot_id not in seen]
            seen.update(lot.lot_id for lot in new)
            lots.extend(new)
            page += 1
            if not new or page > ctx.max_pages or f"page={page}" not in html:
                break
            html = ctx.http.text(f"{a.url}?page={page}")
    log.info("veilingwinnaar: %d running auctions, %d closure/bankruptcy", len(auctions), wanted)
    return lots
