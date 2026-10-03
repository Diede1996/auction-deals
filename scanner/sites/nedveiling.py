"""Nedveiling (nedveiling.nl): weekly "Diverse" and "Partijen" auctions of overstock and leftovers in
Hulten, Vroomshoop, Veldhoven, 's-Heerenberg, Sint Anthonis and Hamont (BE), and now and then a
bankruptcy auction. Only those with a word from auction_keywords in the name or description are read,
so most weeks this site adds nothing.

Plain PHP pages:
1. The homepage lists the auctions as cards: name, number of lots, place and country, closing time
   ("03-10-2026 20:30") and pickup day ("9-10-2026 9.00 - 12.00 uur").
2. The auction page (<id>,project_id,categories) has "Veiling informatie" with the description.
3. categories.php?start=N&project_id=<id>&viewtype=list lists 18 lots per page: name, current bid,
   number of bids and the remaining time.
4. A lot page has the pickup address under "Afhalen:"; one lot page is read per auction.

Buyer's costs: "Veilingkosten 15%" + 21% VAT.
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from ..models import Auction, Lot
from ..util import parse_money, parse_numeric_datetime, parse_relative_close
from .base import SiteContext, short_when, text_lines

log = logging.getLogger(__name__)

SITE = "nedveiling"
BASE = "https://www.nedveiling.nl"
_COUNTRY = {"nederland": None, "belgie": "België", "belgië": "België", "duitsland": "Deutschland"}


def lots_url(auction_id: str, start: int) -> str:
    return (f"{BASE}/categories.php?start={start}&limit=20&project_id={auction_id}"
            "&order_field=a.auction_no&order_type=ASC&viewtype=list")


def parse_home(html: str, now) -> list[Auction]:
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, Auction] = {}
    for link in soup.select("a[href*=',project_id,categories']"):
        m = re.search(r"/(\d+),project_id,categories", link["href"])
        title_el = link.select_one("h4")
        if not m or m.group(1) in out or not title_el:
            continue
        info = {}
        for li in link.select("li"):
            icon = li.select_one("i[title]")
            if icon:
                info[icon["title"].strip().lower()] = li.get_text(" ", strip=True)
        closes = parse_numeric_datetime(info.get("eindtijd", ""))
        if closes and closes < now:
            continue
        place, _, country = info.get("land", "").partition(" - ")
        country = _COUNTRY.get(country.strip().lower(), country.strip() or None)
        pickup_day = info.get("ophaaldag", "")
        out[m.group(1)] = Auction(
            site=SITE,
            auction_id=m.group(1),
            title=title_el.get_text(" ", strip=True),
            url=f"{BASE}/{m.group(1)},project_id,categories",
            closes_at=closes,
            pickup=", ".join(p for p in (place.strip(), country) if p) or None,  # until a lot page says more
            pickup_when=short_when(pickup_day) if re.search(r"\d", pickup_day) else None,
            delivery=any(re.search(r"gratis\s+verzend", li.get_text(" "), re.I) for li in link.select("li")),
            town=place.strip(),
        )
    return list(out.values())


def parse_description(html: str) -> str:
    """The "Veiling informatie" block of the auction page, as one line."""
    soup = BeautifulSoup(html, "html.parser")
    block = soup.select_one(".auction-description")
    return re.sub(r"\s+", " ", block.get_text(" ", strip=True)) if block else ""


def parse_pickup_address(html: str) -> str | None:
    """"Broekdijk 38A, 5125NE Hulten" from a lot page's "Afhalen:" block (with ", België" for Belgium)."""
    lines = text_lines(html)
    for i, line in enumerate(lines):
        if not re.match(r"^Afhalen\s*:", line, re.I):
            continue
        parts = []
        for nxt in lines[i + 1:i + 6]:
            if re.match(r"^\d{1,2}-\d{1,2}-\d{4}", nxt):  # the pickup day comes after the address
                break
            parts.append(nxt)
        if not parts or not re.search(r"\d", parts[0]):
            return None
        country = _COUNTRY.get(parts[-1].lower(), "") if len(parts) > 1 else ""
        if parts[-1].lower() in _COUNTRY:
            parts = parts[:-1]
        return ", ".join(parts + ([country] if country else []))
    return None


def parse_lots(html: str, auction: Auction, now) -> list[Lot]:
    soup = BeautifulSoup(html, "html.parser")
    lots = []
    for link in soup.select("a[href*=',auction_id,auction_details']"):
        m = re.search(r",(\d+),auction_id,auction_details", link["href"])
        title_el = link.select_one("h3")
        if not m or not title_el:
            continue
        fields = {}
        for li in link.select("li"):
            label = li.select_one("b")
            if label:
                fields[label.get_text(strip=True).rstrip(":").lower()] = \
                    li.get_text(" ", strip=True)[len(label.get_text(" ", strip=True)):].strip()
        left = fields.get("resterende tijd", "")
        closes = parse_relative_close(left, now) if left else None
        closes = closes or auction.closes_at
        if closes and closes < now:
            continue
        nbids = fields.get("biedingen", "")
        img = link.select_one("img[src]")
        image = img["src"] if img else None
        if image and not image.startswith("http"):
            image = f"{BASE}/{image.lstrip('/')}"
        href = link["href"]
        lots.append(Lot(
            site=SITE,
            lot_id=m.group(1),
            title=title_el.get_text(" ", strip=True),
            url=href if href.startswith("http") else f"{BASE}/{href.lstrip('/')}",
            current_bid=parse_money(fields.get("huidig bod")),
            closes_at=closes,
            auction_title=auction.title,
            image=image,
            location=auction.town or None,
            bids=int(nbids) if nbids.isdigit() else None,
        ).pickup_from(auction))
    return lots


def fetch_lots(ctx: SiteContext) -> list[Lot]:
    auctions = parse_home(ctx.http.text(f"{BASE}/"), ctx.now)
    known = ctx.cache.setdefault("bankrupt", {})  # auction id -> True/False: descriptions don't change
    for key in [k for k in known if k not in {a.auction_id for a in auctions}]:
        del known[key]
    wanted = []
    for a in auctions:
        if a.auction_id not in known:
            known[a.auction_id] = ctx.is_bankruptcy(f"{a.title} {parse_description(ctx.http.text(a.url))}")
        if known[a.auction_id]:
            wanted.append(a)
    log.info("nedveiling: %d running auctions, %d bankruptcy/closure/estate", len(auctions), len(wanted))
    lots: list[Lot] = []
    for a in wanted:
        seen: set[str] = set()
        start = 0
        for _ in range(ctx.max_pages):
            html = ctx.http.text(lots_url(a.auction_id, start))
            new = [lot for lot in parse_lots(html, a, ctx.now) if lot.lot_id not in seen]
            if not new:
                break
            if not seen and not a.delivery:  # the exact pickup address is on the lot pages
                try:
                    address = parse_pickup_address(ctx.http.text(new[0].url))
                    if address:
                        a.pickup = address
                        for lot in new:
                            lot.pickup = address
                except Exception as e:
                    log.warning("nedveiling: no pickup address for %s: %s", a.auction_id, e)
            seen.update(lot.lot_id for lot in new)
            lots.extend(new)
            start += len(set(re.findall(r",(\d+),auction_id,auction_details", html)))  # 18 per page
    return lots
