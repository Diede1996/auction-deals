"""Inventarisveilingen (inventarisveilingen.nl, Nieuwegein): IT gear, office and warehouse inventory "uit
een faillissement", sold for curators. No buyer's premium, only 21% VAT on the bid.

Plain pages:
1. /veiling/ lists the running auctions: "127: Cisco it apparatuur", a description ("Uit een faillissement
   bieden wij ...") and the closing time "07-10-2026 13:00:00". Auctions run for a long time and keep
   getting new lots.
2. /veiling/index/view/id/<id>/ has the pickup day ("Afhaaldag"), the pickup address ("Afhaaladres") and
   a table of lot groups (categories).
3. /veiling/index/productlist/id/<id>/catid/<cat>/ lists a group's lots, 12 per page (.../p/2/): title,
   description, last bid and closing time. A lot without bids shows €0,00; its starting price is only on
   the lot page.
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from ..models import Auction, Lot
from ..util import parse_money, parse_numeric_datetime
from .base import SiteContext, clean_address, short_when, town_of

log = logging.getLogger(__name__)

SITE = "inventarisveilingen"
BASE = "https://www.inventarisveilingen.nl"


def parse_auctions(html: str, now) -> list[Auction]:
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, Auction] = {}
    for link in soup.select("td.description h3 a[href*='/view/id/']"):
        m = re.search(r"/view/id/(\d+)", link["href"])
        row = link.find_parent("tr")
        if not m or m.group(1) in out or row is None:
            continue
        date_cell = row.select_one("td.date")
        closes = parse_numeric_datetime(date_cell.get_text(" ", strip=True)) if date_cell else None
        if closes and closes < now:
            continue
        desc = row.select_one("div.description")
        city = row.select_one("div.city")
        out[m.group(1)] = Auction(
            site=SITE,
            auction_id=m.group(1),
            title=re.sub(r"^\d+:\s*", "", link.get_text(" ", strip=True)),
            url=f"{BASE}/veiling/index/view/id/{m.group(1)}/",
            description=desc.get_text(" ", strip=True) if desc else "",
            closes_at=closes,
            town=city.get_text(strip=True) if city else "",
        )
    return list(out.values())


def parse_auction_page(html: str, auction: Auction) -> list[str]:
    """Fill in the pickup day and address; returns the URLs of the lot groups that have lots."""
    soup = BeautifulSoup(html, "html.parser")
    for title in soup.select("span.day-title"):
        text = title.find_next_sibling("span", class_="day-text")
        if text and title.get_text(strip=True).lower().startswith("afhaal"):
            when = text.get_text(" ", strip=True)
            auction.pickup_when = short_when(when) if re.search(r"\d", when) else None
    heading = soup.find(["h4", "h3"], string=re.compile(r"Afhaaladres", re.I))
    items = [li.get_text(" ", strip=True) for li in heading.find_next("ul").find_all("li")] if heading and \
        heading.find_next("ul") else []
    items = [i for i in items if i]
    while len(items) > 1 and not re.search(r"\d", items[0]):  # "Cisco apparatuur" is a name, not an address
        items.pop(0)
    if items and re.search(r"\d", items[0]):
        auction.pickup = clean_address(", ".join(i for i in items if i.lower() != "nederland"))
        auction.town = town_of(auction.pickup) or auction.town
    groups = []
    for a in soup.select("table.category-table a[href*='/productlist/']"):
        row = a.find_parent("tr")
        count = row.select_one("td.count") if row else None
        if count and count.get_text(strip=True) == "0":
            continue
        if a["href"] not in groups:
            groups.append(a["href"])
    return groups


def parse_lots(html: str, auction: Auction, now) -> list[Lot]:
    soup = BeautifulSoup(html, "html.parser")
    lots = []
    for link in soup.select("td.product h3 a[href*='/prodid/']"):
        m = re.search(r"/prodid/(\d+)", link["href"])
        row = link.find_parent("tr")
        if not m or row is None:
            continue
        date_cell = row.select_one("td.date")
        closes = (parse_numeric_datetime(date_cell.get_text(" ", strip=True)) if date_cell else None) or auction.closes_at
        if closes and closes < now:
            continue
        bid_cell = row.select_one("td.bid")
        bid = parse_money(bid_cell.get_text(" ", strip=True)) if bid_cell else None
        desc = row.select_one("div.description")
        img = row.select_one("td.image img[src]")
        lots.append(Lot(
            site=SITE,
            lot_id=m.group(1),
            title=link.get_text(" ", strip=True),
            url=link["href"] if link["href"].startswith("http") else BASE + link["href"],
            current_bid=bid or None,  # €0,00 = no bids yet: the starting price is on the lot page
            closes_at=closes,
            auction_title=auction.title,
            description=re.sub(r"\s+", " ", desc.get_text(" ", strip=True)).replace("klik hier voor meer info", "")
            .strip()[:300] if desc else "",
            image=img["src"] if img else None,
            location=auction.town or None,
        ).pickup_from(auction))
    return lots


def next_page(html: str) -> str | None:
    link = BeautifulSoup(html, "html.parser").select_one(".pager a.next[href]")
    return link["href"] if link else None


def fetch_lots(ctx: SiteContext) -> list[Lot]:
    auctions = parse_auctions(ctx.http.text(f"{BASE}/veiling/"), ctx.now)
    wanted = [a for a in auctions if ctx.is_bankruptcy(a.title, a.description)]
    log.info("inventarisveilingen: %d running auctions, %d bankruptcy", len(auctions), len(wanted))
    lots: list[Lot] = []
    for a in wanted:
        for url in parse_auction_page(ctx.http.text(a.url), a):
            for _ in range(ctx.max_pages):
                html = ctx.http.text(url)
                lots.extend(parse_lots(html, a, ctx.now))
                url = next_page(html)
                if not url:
                    break
    return lots
