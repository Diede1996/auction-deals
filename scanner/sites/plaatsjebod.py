"""Plaats Je Bod (plaatsjebod.nl) - server-rendered pages.

1. /nl/auctions/ lists running and closed auctions with their end date and description.
2. /nl/lots/auction/<slug>?pagination=100&page=N lists the lots ("div.lot").
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from ..models import Auction, Lot
from ..util import parse_dutch_datetime, parse_money
from .base import SiteContext

log = logging.getLogger(__name__)

SITE = "plaatsjebod"
BASE = "https://www.plaatsjebod.nl"
PER_PAGE = 100


def parse_auctions(html: str) -> list[Auction]:
    soup = BeautifulSoup(html, "html.parser")
    auctions: dict[str, Auction] = {}
    for a in soup.select("h3 a.name[href*='/lots/auction/']"):
        href = a["href"]
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        if slug in auctions:
            continue
        block = a.find_parent("div", class_="col-sm-9") or a.parent.parent
        end = _text(block.select_one("div.endDate")).replace("Veiling eindigt:", "")
        auctions[slug] = Auction(
            site=SITE,
            auction_id=slug,
            title=a.get_text(" ", strip=True),
            url=href if href.startswith("http") else BASE + href,
            description=_text(block.select_one("div.description")),
            closes_at=parse_dutch_datetime(end),
        )
    return list(auctions.values())


def parse_lots(html: str, auction: Auction) -> list[Lot]:
    soup = BeautifulSoup(html, "html.parser")
    lots = []
    for box in soup.select("div.lot"):
        m = re.search(r"\blot-(\d+)\b", " ".join(box.get("class", [])))
        link = box.select_one("h3 a")
        if not m or not link:
            continue
        title = re.sub(r"^Kavel\s+[\w.-]+:\s*", "", link.get_text(" ", strip=True))
        bid = parse_money(_text(box.select_one("tr.currentBid big")) or _text(box.select_one("tr.currentBid td")))
        end = _text(box.select_one("tr.endDate span.value"))
        nbids = _text(box.select_one("tr.bidCount td.value"))
        img = box.select_one(".main-image img") or box.select_one("img")
        href = link["href"]
        lots.append(Lot(
            site=SITE,
            lot_id=m.group(1),
            title=title,
            url=href if href.startswith("http") else BASE + href,
            current_bid=bid,
            closes_at=parse_dutch_datetime(end) or auction.closes_at,
            auction_title=auction.title,
            image=(BASE + img["src"]) if img and img.get("src", "").startswith("/") else (img.get("src") if img else None),
            bids=int(nbids) if nbids.isdigit() else None,
        ))
    return lots


def fetch_lots(ctx: SiteContext) -> list[Lot]:
    auctions = parse_auctions(ctx.http.text(f"{BASE}/nl/auctions/"))
    running = [a for a in auctions if a.closes_at is None or a.closes_at > ctx.now]
    bankrupt = [a for a in running if ctx.is_bankruptcy(f"{a.title} {a.description}")]
    log.info("plaatsjebod: %d running auctions, %d bankruptcy", len(running), len(bankrupt))
    lots: list[Lot] = []
    for a in bankrupt:
        for page in range(1, ctx.max_pages + 1):
            html = ctx.http.text(f"{BASE}/nl/lots/auction/{a.auction_id}?pagination={PER_PAGE}&page={page}")
            found = parse_lots(html, a)
            lots.extend(found)
            if len(found) < PER_PAGE:
                break
    return lots


def _text(el) -> str:
    return el.get_text(" ", strip=True) if el else ""
