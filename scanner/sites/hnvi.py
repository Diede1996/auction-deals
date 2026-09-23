"""HNVI veilingen (hnvi.nl) - server-rendered pages.

1. Homepage lists running auctions (title, "Einddatum", description mentioning the curator).
2. Auction page lists lots, 40 per page, more pages at <auction-url>/page:N.
   Full lot titles are in the link's title attribute; exact closing times sit in
   countdown scripts: `var countdown_196542s_timer = new Date('Oct 5, 2026 19:30:00 +0200');`
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..models import Auction, Lot
from ..util import parse_dutch_datetime, parse_money
from .base import SiteContext

log = logging.getLogger(__name__)

SITE = "hnvi"
BASE = "https://www.hnvi.nl"

_TIMER_RE = re.compile(r"countdown_(\d+)s_timer\s*=\s*new Date\('([^']+)'\)")


def parse_home(html: str) -> list[Auction]:
    soup = BeautifulSoup(html, "html.parser")
    auctions: dict[str, Auction] = {}
    for h in soup.select("h2.ui-h3-title a[href*='/online-veiling/']"):
        href = h.get("href", "")
        m = re.search(r"/online-veiling/[^/]+/(\d+)", href)
        if not m or m.group(1) in auctions:
            continue
        box = h.find_parent(class_="ui-auction-data-container") or h.parent
        desc = _text(box.select_one("p.ui-par-01")) if box else ""
        meta = _text(box.select_one("span.ui-title-01")) if box else ""
        ends = re.search(r"Einddatum\s*:\s*(.+)$", meta)
        auctions[m.group(1)] = Auction(
            site=SITE,
            auction_id=m.group(1),
            title=h.get_text(" ", strip=True),
            url=href if href.startswith("http") else BASE + href,
            description=desc,
            closes_at=parse_dutch_datetime(ends.group(1)) if ends else None,
        )
    return list(auctions.values())


def parse_timers(html: str) -> dict[str, datetime]:
    out = {}
    for lot_id, value in _TIMER_RE.findall(html):
        try:
            out[lot_id] = datetime.strptime(value, "%b %d, %Y %H:%M:%S %z")
        except ValueError:
            continue
    return out


def parse_lots(html: str, auction: Auction) -> list[Lot]:
    soup = BeautifulSoup(html, "html.parser")
    timers = parse_timers(html)
    lots = []
    for box in soup.select("div.ui-product-box-list-item"):
        link = box.select_one("p.ui-product-box-title a") or box.select_one("a[href*='/veiling-kavel/']")
        if not link:
            continue
        href = link.get("href", "")
        m = re.search(r"/veiling-kavel/[^/]+/(\d+)", href)
        if not m:
            continue
        lot_id = m.group(1)
        img = box.select_one("img")
        price = _text(box.select_one("p.ui-product-box-price"))
        lots.append(Lot(
            site=SITE,
            lot_id=lot_id,
            title=(link.get("title") or link.get_text(" ", strip=True)).strip(),
            url=href if href.startswith("http") else BASE + href,
            current_bid=parse_money(price.replace("Prijs:", "")),
            closes_at=timers.get(lot_id) or auction.closes_at,
            auction_title=auction.title,
            image=img.get("src") if img else None,
        ))
    return lots


def last_page(html: str) -> int:
    nums = [int(n) for n in re.findall(r"/page:(\d+)", html)]
    return max(nums) if nums else 1


def fetch_lots(ctx: SiteContext) -> list[Lot]:
    auctions = parse_home(ctx.http.text(BASE + "/"))
    bankrupt = [a for a in auctions if ctx.is_bankruptcy(f"{a.title} {a.description}")]
    log.info("hnvi: %d auctions, %d bankruptcy", len(auctions), len(bankrupt))
    lots: list[Lot] = []
    for a in bankrupt:
        html = ctx.http.text(a.url)
        lots.extend(parse_lots(html, a))
        pages = min(last_page(html), ctx.max_pages)
        page = 2
        while page <= pages:
            html = ctx.http.text(f"{a.url.rstrip('/')}/page:{page}")
            found = parse_lots(html, a)
            if not found:
                break
            lots.extend(found)
            pages = min(max(pages, last_page(html)), ctx.max_pages)  # pager shows a sliding window
            page += 1
    return lots


def _text(el) -> str:
    return el.get_text(" ", strip=True) if el else ""
