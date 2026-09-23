"""Onlineveilingmeester (onlineveilingmeester.nl) - JSON REST API used by the site itself.

- /rest/nl/veilingen?status=open&domein=ONLINEVEILINGMEESTER -> running auctions with a `type`
  (bankruptcy auctions have type "FAILLISEMENT", spelled like that).
- /rest/nl/v2/veilingen/<id>/kavels?page=N&size=100&status=OPEN -> lots (pages start at 1).
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote

from ..models import Auction, Lot
from ..util import from_iso
from .base import SiteContext

log = logging.getLogger(__name__)

SITE = "onlineveilingmeester"
BASE = "https://onlineveilingmeester.nl"
PER_PAGE = 100


def parse_auctions(data: dict) -> list[Auction]:
    out = []
    for v in data.get("veilingen") or []:
        out.append(Auction(
            site=SITE,
            auction_id=str(v.get("id")),
            title=(v.get("naam") or "").strip(),
            url=f"{BASE}/nl/veilingen/{v.get('id')}",
            description=re.sub(r"<[^>]+>", " ", v.get("omschrijving") or ""),
            kind=v.get("type") or "",
            closes_at=from_iso(v.get("sluitingsDatumISO")),
        ))
    return out


def is_bankruptcy_auction(a: Auction, is_bankruptcy) -> bool:
    return "FAILL" in a.kind.upper() or is_bankruptcy(f"{a.title} {a.description}")


def parse_lot(k: dict, auction: Auction) -> Lot:
    bid = k.get("hoogsteBod")
    if not isinstance(bid, (int, float)) or bid <= 0:
        bid = k.get("openingsBod")
    images = k.get("imageList") or []
    volg = k.get("volgNummer") or k.get("id")
    return Lot(
        site=SITE,
        lot_id=str(k.get("id")),
        title=(k.get("naam") or "").strip(),
        url=f"{BASE}/nl/veilingen/{auction.auction_id}/kavels/{volg}",
        current_bid=float(bid) if isinstance(bid, (int, float)) else None,
        closes_at=from_iso(k.get("sluitingsDatumISO")) or auction.closes_at,
        auction_title=auction.title,
        image=f"{BASE}/images/original/{quote(images[0])}" if images else None,
        bids=k.get("aantalBiedingen"),
        extra_fee=float(k.get("handelingskosten") or 0),
    )


def fetch_lots(ctx: SiteContext) -> list[Lot]:
    data = ctx.http.json(f"{BASE}/rest/nl/veilingen?status=open&domein=ONLINEVEILINGMEESTER")
    auctions = parse_auctions(data)
    bankrupt = [a for a in auctions if is_bankruptcy_auction(a, ctx.is_bankruptcy)]
    log.info("onlineveilingmeester: %d auctions, %d bankruptcy", len(auctions), len(bankrupt))
    lots: dict[str, Lot] = {}
    for a in bankrupt:
        for page in range(1, ctx.max_pages + 1):
            url = (f"{BASE}/rest/nl/v2/veilingen/{a.auction_id}/kavels"
                   f"?page={page}&size={PER_PAGE}&status=OPEN&sortBy=volgNummer&veiling={a.auction_id}")
            content = ctx.http.json(url).get("content") or []
            new = 0
            for k in content:
                lot = parse_lot(k, a)
                if lot.key not in lots:
                    new += 1
                lots[lot.key] = lot
            if len(content) < PER_PAGE or new == 0:
                break
    return list(lots.values())
