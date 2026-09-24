"""Onlineveilingmeester (onlineveilingmeester.nl) - JSON REST API used by the site itself.

- /rest/nl/veilingen?status=open&domein=ONLINEVEILINGMEESTER -> running auctions with a `type`:
  bankruptcy auctions have type "FAILLISEMENT" (spelled like that), Domeinen Roerende Zaken "DRZ",
  local government "OVERHEID".
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


DEFAULT_TYPES = ["FAILLISEMENT", "DRZ"]  # bankruptcy (their spelling) + Domeinen Roerende Zaken

# Buyer's costs from onlineveilingmeester.nl/nl/content/veilingkosten-voor-de-koper (lots up to €10.000):
#   VAT items: 17% premium + 21% VAT on bid and premium; margin-scheme items: 21% premium incl. VAT, no VAT on bid.
#   Domeinen Roerende Zaken: 10% premium for VAT items, 12.1% for margin-scheme items.
RATES = {  # (auction is DRZ, lot has VAT) -> (premium, vat)
    (False, True): (0.17, 0.21), (False, False): (0.21, 0.0),
    (True, True): (0.10, 0.21), (True, False): (0.121, 0.0),
}


def wanted_auction(a: Auction, is_bankruptcy, types: list[str]) -> bool:
    kind = a.kind.upper()
    if any(t.upper()[:5] in kind for t in types):  # "FAILL" also catches the correct spelling
        return True
    return is_bankruptcy(f"{a.title} {a.description}")


def parse_lot(k: dict, auction: Auction) -> Lot:
    bid = k.get("hoogsteBod")
    if not isinstance(bid, (int, float)) or bid <= 0:
        bid = k.get("openingsBod")
    images = k.get("imageList") or []
    volg = k.get("volgNummer") or k.get("id")
    has_vat = k.get("btwPercentage") != 0  # 0 = margin scheme (no VAT on the bid)
    premium, vat = RATES[(auction.kind.upper() == "DRZ", has_vat)]
    return Lot(
        site=SITE,
        lot_id=str(k.get("id")),
        title=(k.get("naam") or "").strip(),
        url=f"{BASE}/nl/veilingen/{auction.auction_id}/kavels/{volg}",
        current_bid=float(bid) if isinstance(bid, (int, float)) else None,
        closes_at=from_iso(k.get("sluitingsDatumISO")) or auction.closes_at,
        auction_title=("Domeinen · " + auction.title) if auction.kind.upper() == "DRZ" else auction.title,
        image=f"{BASE}/images/original/{quote(images[0])}" if images else None,
        bids=k.get("aantalBiedingen"),
        extra_fee=float(k.get("handelingskosten") or 0),
        premium=premium,
        vat=vat,
    )


def fetch_lots(ctx: SiteContext) -> list[Lot]:
    data = ctx.http.json(f"{BASE}/rest/nl/veilingen?status=open&domein=ONLINEVEILINGMEESTER")
    auctions = parse_auctions(data)
    types = ctx.settings.get("auction_types") or DEFAULT_TYPES
    bankrupt = [a for a in auctions if wanted_auction(a, ctx.is_bankruptcy, types)]
    log.info("onlineveilingmeester: %d auctions, %d bankruptcy/Domeinen", len(auctions), len(bankrupt))
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
