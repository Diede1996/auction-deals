"""Read all lots of one Troostwijk auction into the private repository (workflow "Auction lots").
Only counts are printed: the public log must not show Troostwijk's lots (see private_repo.py)."""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

from . import private_repo
from .http import BlockedError
from .sites.troostwijk import Reader, read_auction

_TAGS = re.compile(r"<[^>]+>")


def lot_details(props: dict) -> dict:
    lot = props.get("lot") or {}
    desc = lot.get("description") or {}
    if isinstance(desc, dict):
        desc = desc.get("description") or desc.get("text") or ""
    attrs = [{"name": a.get("name"), "value": a.get("value")} for a in lot.get("attributes") or [] if isinstance(a, dict)]
    return {"description": _TAGS.sub(" ", str(desc or "")).strip(), "attributes": attrs,
            "startBid": ((lot.get("startingBid") or lot.get("initialAmount") or {}) or {}).get("cents")}


def read_hnvi(url: str) -> int:
    """One HNVI auction (all listing pages): title, price and the box's own text (shows closed/sold)."""
    from bs4 import BeautifulSoup
    from .http import Http
    from .models import Auction
    from .sites import hnvi
    m = re.search(r"hnvi\.nl/online-veiling/[^/]+/(\d+)", url)
    if not m:
        print("not an HNVI auction link")
        return 1
    base = url.split("?")[0].rstrip("/")
    http = Http(delay=3.0)
    auction = Auction(site="hnvi", auction_id=m.group(1), title="", url=base)
    lots, page, pages = [], 1, 1
    while page <= pages and page <= 20:
        html = http.text(base if page == 1 else f"{base}/page:{page}")
        boxes = {}
        for box in BeautifulSoup(html, "html.parser").select("div.ui-product-box-list-item"):
            a = box.select_one("a[href*='/veiling-kavel/']")
            k = re.search(r"/veiling-kavel/[^/]+/(\d+)", a.get("href", "")) if a else None
            if k:
                boxes[k.group(1)] = box.get_text(" ", strip=True)[:400]
        found = hnvi.parse_lots(html, auction)
        if not found:
            break
        for lot in found:
            lots.append({"id": lot.lot_id, "title": lot.title, "bid": lot.current_bid, "url": lot.url,
                         "closes": lot.closes_at.isoformat() if lot.closes_at else None, "box": boxes.get(lot.lot_id, "")})
        pages = max(pages, hnvi.last_page(html))
        page += 1
    print(f"{len(lots)} lots read, {sum(1 for r in lots if r['bid'] is not None)} with price")
    if not lots:
        return 1
    data = {"slug": f"hnvi-{m.group(1)}", "url": base, "read": datetime.now(timezone.utc).isoformat(), "lots": lots}
    ok = private_repo.put_json(f"lookups/hnvi-{m.group(1)}.json", data, f"Lots of HNVI auction {m.group(1)}")
    print(f"saved to the private repository: {ok}")
    return 0 if ok else 1


def main() -> int:
    if "hnvi.nl" in (os.environ.get("SLUG") or ""):
        return read_hnvi(os.environ["SLUG"].strip())
    slug = (os.environ.get("SLUG") or "").strip().split("?")[0].rstrip("/").split("/")[-1]
    if not re.fullmatch(r"[A-Za-z0-9-]+", slug or ""):
        print("not an auction slug")
        return 1
    reader = Reader(SimpleNamespace(http=None), 8.0)
    try:
        lots, complete = read_auction(reader, slug, 40)
        if os.environ.get("DESCRIPTIONS", "true").lower() == "true":
            for i, rec in enumerate(lots):
                try:
                    props = reader.props(f"/nl/l/{rec['slug']}")
                    rec.update(lot_details(props))
                    rec["raw"] = props.get("lot")  # the page's own fields, in case the description sits elsewhere
                except BlockedError:
                    raise
                except Exception as e:  # one odd lot page shouldn't stop the rest
                    rec["error"] = type(e).__name__
                if (i + 1) % 25 == 0:
                    print(f"{i + 1} of {len(lots)} lot pages read")
    except BlockedError as e:
        print(f"stopped: {e}")
        complete = False
    if not lots:  # never overwrite an earlier reading with nothing
        try:
            props = reader.props(f"/nl/a/{slug}")
            info = props.get("lots")
            al = props.get("auctionListing")
            print("auctionListing:", type(al).__name__, sorted(al)[:30] if isinstance(al, dict) else len(al or []))
            if isinstance(al, dict):
                for k, v in al.items():
                    if isinstance(v, dict):
                        print(" ", k, "->", sorted(v)[:20])
                    elif isinstance(v, list):
                        print(" ", k, "-> list", len(v), sorted(v[0])[:30] if v and isinstance(v[0], dict) else "")
                    else:
                        print(" ", k, "->", type(v).__name__)
        except Exception as e:
            print("diagnosis failed:", type(e).__name__)
        print("0 lots read; earlier reading kept")
        return 1
    print(f"check: {sum(1 for r in lots if r.get('title'))} with title, {sum(1 for r in lots if r.get('bid') is not None)} with bid, "
          f"{sum(1 for r in lots if r.get('slug'))} with link")
    data = {"slug": slug, "read": datetime.now(timezone.utc).isoformat(), "complete": complete, "lots": lots}
    ok = private_repo.put_json(f"lookups/{slug}.json", data, f"Lots of auction {slug}")
    print(f"{len(lots)} lots read (complete: {complete}); saved to the private repository: {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
