"""ProVeiling (proveiling.nl) - classic ASP.NET WebForms site.

1. The homepage lists every running auction ("a.home-link") with a "Betreft: <type>" label.
2. Lots are on /Alle-kavels/<id>/Veiling; further pages need an ASP.NET postback
   (__EVENTTARGET = the pager control, __EVENTARGUMENT = page number).
3. The auction info page holds the exact closing date ("Sluiting: maandag 28 september 2026 vanaf 20:00").
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from ..models import Auction, Lot
from ..util import parse_dutch_datetime, parse_money, parse_relative_close
from .base import SiteContext

log = logging.getLogger(__name__)

SITE = "proveiling"
BASE = "https://www.proveiling.nl"
PAGER_TARGET = "ctl00$myCenterContentPanel$ALCItems$AspNetPager1"


def parse_home(html: str) -> list[Auction]:
    soup = BeautifulSoup(html, "html.parser")
    info_links: dict[str, str] = {}
    for a in soup.select('a[href*="AuctionGroup.aspx"]'):
        m = re.search(r"/(\d+)/[^/]*/AuctionGroup\.aspx", a.get("href", ""))
        if m:
            info_links.setdefault(m.group(1), a["href"])
    auctions: dict[str, Auction] = {}
    for a in soup.select("a.home-link"):
        m = re.search(r"/Alle-kavels/(\d+)/", a.get("href", ""))
        if not m:
            continue
        aid = m.group(1)
        kind = ""
        label = a.parent.select_one("span.small") if a.parent else None
        if label:
            kind = re.sub(r"\s+", " ", label.get_text(" ", strip=True)).replace("Betreft:", "").strip()
        existing = auctions.get(aid)
        if existing:
            existing.kind = existing.kind or kind
            continue
        info = info_links.get(aid, "")
        auctions[aid] = Auction(
            site=SITE,
            auction_id=aid,
            title=a.get_text(" ", strip=True),
            url=(info if info.startswith("http") else BASE + info) if info else f"{BASE}/Alle-kavels/{aid}/Veiling",
            kind=kind,
        )
    return list(auctions.values())


def parse_closing(html: str):
    text = re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" "))
    m = re.search(r"Sluiting:\s*\w*\s*(\d{1,2}\s+\w+\s+\d{4})\s*vanaf\s*(\d{1,2}:\d{2})", text)
    if not m:
        return None
    return parse_dutch_datetime(f"{m.group(1)} {m.group(2)}")


def parse_lot_rows(html: str, auction: Auction, now) -> list[Lot]:
    soup = BeautifulSoup(html, "html.parser")
    lots: list[Lot] = []
    for row in soup.select('div.row[id^="tr"]'):
        lot_id = row["id"][2:]
        if not lot_id.isdigit():
            continue
        name = row.select_one(".editorName")
        link = row.select_one("a.article-link") or row.select_one('a[itemprop="url"]')
        if not name or not link:
            continue
        bid = parse_money(_text(row.select_one("#CurrentBid")))
        start = None
        bids_el = row.select_one("p.bids")
        if bids_el:
            m = re.search(r"Startbod:\s*([€\d.,\s]+)", bids_el.get_text(" "))
            start = parse_money(m.group(1)) if m else None
        if not bid and start:
            bid = start
        img = row.select_one("img")
        image = (img.get("original") or img.get("src")) if img else None
        endtext = _text(row.select_one("span.endtime")).replace("Kavel sluit:", "").strip()
        closes = None
        if endtext:
            closes = parse_relative_close(endtext, now)
            # "6 dagen" is vague; the auction's own closing time is more exact
            if auction.closes_at and ("dag" in endtext) and not re.search(r"\d:\d", endtext):
                closes = auction.closes_at
        closes = closes or auction.closes_at
        nbids = _text(row.select_one("#NumberOfBids"))
        lots.append(Lot(
            site=SITE,
            lot_id=lot_id,
            title=name.get_text(" ", strip=True),
            url=link["href"] if link["href"].startswith("http") else BASE + link["href"],
            current_bid=bid,
            closes_at=closes,
            auction_title=auction.title,
            image=image,
            location=_text(row.select_one("p.location strong")) or None,
            bids=int(nbids) if nbids.isdigit() else None,
        ))
    return lots


def page_count(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    sel = soup.find("select", attrs={"name": PAGER_TARGET + "_input"})
    if not sel:
        return 1
    nums = [int(o.get("value")) for o in sel.find_all("option") if (o.get("value") or "").isdigit()]
    return max(nums) if nums else 1


def postback_fields(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    return {i["name"]: i.get("value", "") for i in soup.select('input[type="hidden"]') if i.get("name")}


def fetch_auction_lots(ctx: SiteContext, auction: Auction) -> list[Lot]:
    url = f"{BASE}/Alle-kavels/{auction.auction_id}/Veiling"
    html = ctx.http.text(url)
    lots = parse_lot_rows(html, auction, ctx.now)
    pages = min(page_count(html), ctx.max_pages)
    for page in range(2, pages + 1):
        form = postback_fields(html)
        form["__EVENTTARGET"] = PAGER_TARGET
        form["__EVENTARGUMENT"] = str(page)
        html = ctx.http.post(url, data=form, headers={"Referer": url}).text
        lots.extend(parse_lot_rows(html, auction, ctx.now))
    return lots


def fetch_lots(ctx: SiteContext) -> list[Lot]:
    auctions = parse_home(ctx.http.text(BASE + "/"))
    bankrupt = [a for a in auctions if ctx.is_bankruptcy(f"{a.title} {a.kind}")]
    log.info("proveiling: %d auctions, %d bankruptcy", len(auctions), len(bankrupt))
    lots: list[Lot] = []
    for a in bankrupt:
        if "AuctionGroup.aspx" in a.url:
            try:
                a.closes_at = parse_closing(ctx.http.text(a.url))
            except Exception as e:  # closing time is nice-to-have
                log.warning("proveiling: no closing time for %s: %s", a.auction_id, e)
        lots.extend(fetch_auction_lots(ctx, a))
    return lots


def _text(el) -> str:
    return el.get_text(" ", strip=True) if el else ""
