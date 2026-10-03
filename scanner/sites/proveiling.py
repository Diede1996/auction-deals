"""ProVeiling (proveiling.nl) - classic ASP.NET WebForms site.

1. The homepage lists every running auction ("a.home-link") with a "Betreft: <type>" label.
2. Lots are on /Alle-kavels/<id>/Veiling; further pages need an ASP.NET postback
   (__EVENTTARGET = the pager control, __EVENTARGUMENT = page number).
3. The auction info page holds the exact closing date ("Sluiting: maandag 28 september 2026 vanaf 20:00"),
   the pickup day ("Ophaaldag(en): donderdag 01 oktober 2026 van 10:00 tot 12:00") and the address under
   the heading "Locatie" ("Produktieweg 9" / "8304AV, Emmeloord").

Vlavem (vlavem.com, Belgium) runs on the same software; its lot pages use a "Volgende" (next) link instead
of the page selector. fetch_from() reads either site.
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from ..models import Auction, Lot
from ..util import parse_dutch_datetime, parse_money, parse_relative_close
from .base import SiteContext, clean_address, has_number, labelled, line_index, short_when, text_lines

log = logging.getLogger(__name__)

SITE = "proveiling"
BASE = "https://www.proveiling.nl"
PAGER_TARGET = "ctl00$myCenterContentPanel$ALCItems$AspNetPager1"
_NEXT_LINK = re.compile(r"__doPostBack\((?:'|&#39;)([^'&]+)(?:'|&#39;),\s*(?:''|&#39;&#39;)\)\"[^>]*>\s*Volgende\s*<",
                        re.I)
_DELIVERY = re.compile(r"gratis\s+(?:levering|verzending|bezorging)|verzendveiling", re.I)


def parse_home(html: str, site: str = SITE, base: str = BASE) -> list[Auction]:
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
        title = re.sub(rf"\s*\({aid}\)\s*$", "", a.get_text(" ", strip=True))  # Vlavem: "... (6229)"
        auctions[aid] = Auction(
            site=site,
            auction_id=aid,
            title=title,
            url=(info if info.startswith("http") else base + info) if info else f"{base}/Alle-kavels/{aid}/Veiling",
            kind=kind,
            delivery=bool(_DELIVERY.search(title)),
        )
    return list(auctions.values())


def parse_closing(html: str):
    text = re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" "))
    m = re.search(r"Sluiting:\s*\w*\s*(\d{1,2}\s+\w+\s+\d{4})\s*vanaf\s*(\d{1,2}:\d{2})", text)
    if not m:
        return None
    return parse_dutch_datetime(f"{m.group(1)} {m.group(2)}")


def parse_pickup(html: str) -> tuple[str | None, str | None]:
    """(address, pickup day) from the auction info page."""
    lines = text_lines(html)
    start = line_index(lines, ["Datums", "Start", "Sluiting"])
    address = clean_address(labelled(lines, ["Locatie", "Ophaallocatie", "Afgifteadres"], check=has_number, start=start))
    when = short_when(labelled(lines, [r"Ophaaldag\(en\)", "Ophaaldagen", "Ophaaldag", "Afgifte", "Afhalen"], 2,
                               start=start))
    return address, when


def parse_lot_rows(html: str, auction: Auction, now, base: str = BASE) -> list[Lot]:
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
            site=auction.site,
            lot_id=lot_id,
            title=name.get_text(" ", strip=True),
            url=link["href"] if link["href"].startswith("http") else base + link["href"],
            current_bid=bid,
            closes_at=closes,
            auction_title=auction.title,
            image=image,
            location=_text(row.select_one("p.location strong")) or None,
            bids=int(nbids) if nbids.isdigit() else None,
        ).pickup_from(auction))
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


def next_link(html: str) -> str | None:
    """The postback target of the "Volgende" (next page) link, if there is one (Vlavem)."""
    m = _NEXT_LINK.search(html)
    return m.group(1) if m else None


def fetch_auction_lots(ctx: SiteContext, auction: Auction, base: str = BASE) -> list[Lot]:
    url = f"{base}/Alle-kavels/{auction.auction_id}/Veiling"
    html = ctx.http.text(url)
    lots = parse_lot_rows(html, auction, ctx.now, base)
    pages = page_count(html)
    if pages > 1:  # ProVeiling: page selector
        for page in range(2, min(pages, ctx.max_pages) + 1):
            form = postback_fields(html)
            form["__EVENTTARGET"] = PAGER_TARGET
            form["__EVENTARGUMENT"] = str(page)
            html = ctx.http.post(url, data=form, headers={"Referer": url}).text
            lots.extend(parse_lot_rows(html, auction, ctx.now, base))
        return lots
    seen = {lot.lot_id for lot in lots}
    for _ in range(ctx.max_pages - 1):  # Vlavem: "Volgende" link until the last page
        target = next_link(html)
        if not target:
            break
        form = postback_fields(html)
        form["__EVENTTARGET"] = target
        form["__EVENTARGUMENT"] = ""
        html = ctx.http.post(url, data=form, headers={"Referer": url}).text
        new = [lot for lot in parse_lot_rows(html, auction, ctx.now, base) if lot.lot_id not in seen]
        if not new:
            break
        seen.update(lot.lot_id for lot in new)
        lots.extend(new)
    return lots


def fetch_from(ctx: SiteContext, site: str, base: str, country: str | None = None) -> list[Lot]:
    """All lots of the bankruptcy, closure and estate auctions on a ProVeiling-style site.
    country: added to pickup addresses ("België") so they're looked up in the right country."""
    auctions = parse_home(ctx.http.text(base + "/"), site, base)
    bankrupt = [a for a in auctions if ctx.is_bankruptcy(a.title, a.kind)]
    log.info("%s: %d auctions, %d bankruptcy/closure/estate", site, len(auctions), len(bankrupt))
    lots: list[Lot] = []
    for a in bankrupt:
        if "AuctionGroup.aspx" in a.url:
            try:
                info = ctx.http.text(a.url)
                a.closes_at = parse_closing(info)
                a.pickup, a.pickup_when = parse_pickup(info)
                if a.pickup_when and _DELIVERY.search(a.pickup_when):  # "Ophaaldag(en): Gratis verzending."
                    a.delivery, a.pickup_when = True, None
                if a.pickup and country and country.lower() not in a.pickup.lower():
                    a.pickup = f"{a.pickup}, {country}"
            except Exception as e:  # closing time and pickup details are nice-to-have
                log.warning("%s: no auction details for %s: %s", site, a.auction_id, e)
        lots.extend(fetch_auction_lots(ctx, a, base))
    return lots


def fetch_lots(ctx: SiteContext) -> list[Lot]:
    return fetch_from(ctx, SITE, BASE)


def _text(el) -> str:
    return el.get_text(" ", strip=True) if el else ""
