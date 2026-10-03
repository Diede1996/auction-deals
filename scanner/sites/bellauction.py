"""BellAuction (bellauction.be, Waregem, Belgium): business closures ("stopzetting"), shop and restaurant
contents and some stock clearances in Flanders.

The site is an app that gets its data as JSON from auction-prod.azurewebsites.net:
1. /auctions/ lists all auctions (also ended ones) with name, description ("comment"), start and end
   time, pickup days ("collectionDays") and the address of the goods ("productsLocation").
2. /auctionlots/auction/<id>/count and /auctionlots/auction/<id>/<page>/<size> give the lots: name,
   description, starting bid, highest bid, number of bids, end time and photo.
Lot pages on the site: https://www.bellauction.be/auctionlot/<id>/<name>.
The API serves more than one shop and answers "500" unless the request says it comes from bellauction.be
(the Origin header, as the site itself sends it).

Times have no time zone; they are Belgian (= Dutch) time.
Buyer's costs: "veilingkosten 17% en BTW" (FAQ).
"""
from __future__ import annotations

import logging
import math
import re

from bs4 import BeautifulSoup

from ..models import Auction, Lot
from ..util import normalize, parse_local_iso, parse_money
from .base import SiteContext, short_when, town_of

log = logging.getLogger(__name__)

SITE = "bellauction"
API = "https://auction-prod.azurewebsites.net"
WEB = "https://www.bellauction.be"
PAGE_SIZE = 100
HEADERS = {"Origin": WEB, "Referer": WEB + "/"}  # which shop the request is for


def slug(text: str) -> str:
    return normalize(text).replace(" ", "-")[:80] or "kavel"


def _address(text: str | None) -> str | None:
    """"Den Argos, Antwerpsesteenweg 550, 9040 Gent" -> "Antwerpsesteenweg 550, 9040 Gent, België"
    (the business name in front of a street and number is left out); "Noorderboomgaard, 8000 Koolkerke"
    stays as it is (a street without a number); "Wakken" -> "Wakken, België"."""
    if not text:
        return None
    parts = [p.strip() for p in re.split(r"[\r\n,]+", text) if p.strip()]
    while len(parts) > 2 and not re.search(r"\d", parts[0]) and re.search(r"\d", parts[1]) \
            and not re.match(r"^\d{4}\s", parts[1]):
        parts.pop(0)
    return ", ".join(parts + ["België"]) if parts else None


def parse_auctions(data: list, now) -> list[Auction]:
    """The auctions that are open for bidding now."""
    out = []
    for a in data if isinstance(data, list) else []:
        if not isinstance(a, dict) or not a.get("id"):
            continue
        ends, starts = parse_local_iso(a.get("endTime")), parse_local_iso(a.get("startTime"))
        if not ends or ends < now or (starts and starts > now):
            continue
        pickup = _address(a.get("productsLocation"))
        comment = BeautifulSoup(a.get("comment") or a.get("auctionDescription") or "", "html.parser").get_text(" ")
        out.append(Auction(
            site=SITE,
            auction_id=str(a["id"]),
            title=(a.get("description") or "").strip(),
            url=f"{WEB}/auction/{a['id']}",
            description=re.sub(r"\s+", " ", comment).strip(),
            closes_at=ends,
            pickup=pickup,
            pickup_when=short_when(a.get("collectionDays")),
            town=town_of(pickup) or "",
        ))
    return out


def parse_lots(items: list, auction: Auction, now) -> list[Lot]:
    lots = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or item.get("isActive") is False or not item.get("id"):
            continue
        title = (item.get("description") or "").strip()
        closes = parse_local_iso(item.get("endTime")) or auction.closes_at
        if not title or (closes and closes < now):
            continue
        bid = parse_money(item.get("highestBid")) or parse_money(item.get("startingBid"))
        n = item.get("nrOfBids")
        lots.append(Lot(
            site=SITE,
            lot_id=str(item["id"]),
            title=title,
            url=f"{WEB}/auctionlot/{item['id']}/{slug(title)}",
            current_bid=bid,
            closes_at=closes,
            auction_title=auction.title,
            description=re.sub(r"\s+", " ", item.get("shortDescription") or "").strip()[:300],
            image=item.get("thumbnailAbsoluteFileUrl") or None,
            location=auction.town or None,
            bids=int(n) if isinstance(n, (int, float)) else None,
        ).pickup_from(auction))
    return lots


def fetch_lots(ctx: SiteContext) -> list[Lot]:
    auctions = parse_auctions(ctx.http.json(f"{API}/auctions/", headers=HEADERS), ctx.now)
    wanted = [a for a in auctions if ctx.is_bankruptcy(a.title, a.description)]
    log.info("bellauction: %d running auctions, %d closure/bankruptcy/estate", len(auctions), len(wanted))
    lots: list[Lot] = []
    for a in wanted:
        try:
            count = int(str(ctx.http.text(f"{API}/auctionlots/auction/{a.auction_id}/count", headers=HEADERS))
                        .strip() or 0)
        except ValueError:
            count = PAGE_SIZE
        for page in range(1, min(math.ceil(count / PAGE_SIZE), ctx.max_pages) + 1):
            data = ctx.http.json(f"{API}/auctionlots/auction/{a.auction_id}/{page}/{PAGE_SIZE}", headers=HEADERS)
            lots.extend(parse_lots(data, a, ctx.now))
    return lots
