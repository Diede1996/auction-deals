"""Lot descriptions, for lots whose title has no type number.

"2 x Dell 24 inch monitor" says nothing about which Dell; the lot page does: "2 x Dell 24 inch monitor type
U2419 HC". For lots on your watchlist without a type number in the title, the bot reads the description
("Omschrijving") from the lot page once and remembers it, so the Marktplaats price is for that model.

- Only for lots that match your watchlist, at most `limit` new pages per scan, one site at a time with
  the usual pause between requests.
- Troostwijk is never visited; Onlineveilingmeester's and BellAuction's lot pages are apps without the text
  in the page (BellAuction's descriptions come with the lot list anyway).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

from .http import BlockedError
from .sites.base import text_lines

log = logging.getLogger(__name__)

SKIP_SITES = {"troostwijk", "onlineveilingmeester", "bellauction"}
_LABEL = re.compile(r"^(?:kavel\s*)?(?:omschrijving|beschrijving|description)\s*:?\s*(.*)$", re.I)
_END = re.compile(r"^(?:kijkdag|afhaaldag|ophaaldag|sluit|biedingen|bied|voorwaarden|retourneren|locatie|"
                  r"kavelnummer|verkocht door|veiling|opgeld|let op)\b", re.I)


def description_from_page(html: str) -> str:
    """The text under "Omschrijving" (or "Beschrijving") up to the next heading, at most 300 characters.
    The type number is looked for in the first part only (identify.plan_for)."""
    lines = text_lines(html)
    for i, line in enumerate(lines):
        m = _LABEL.match(line)
        if not m:
            continue
        parts = [m.group(1).strip()] if m.group(1).strip() else []
        for nxt in lines[i + 1:i + 8]:
            if _END.match(nxt) or nxt.endswith(":") or sum(len(p) for p in parts) > 300:
                break
            parts.append(nxt)
        text = " ".join(parts).strip()
        if text:
            return text[:300]
    return ""


def fill_descriptions(matched, http_factory, cache: dict, now: datetime, needs, limit: int = 40) -> int:
    """Set lot.description for matched lots where needs(lot) is true (no type number in the title).
    cache: lot key -> {"d": description, "at": iso date}, kept in the state file. Returns pages read."""
    clients: dict[str, object] = {}
    blocked: set[str] = set()
    read = 0
    for _, lot in matched:
        if lot.description or lot.site in SKIP_SITES or not needs(lot):
            continue
        entry = cache.get(lot.key)
        if entry is not None:
            lot.description = entry.get("d", "")
            continue
        host = urlparse(lot.url).netloc
        if read >= limit or not host or host in blocked:
            continue
        http = clients.setdefault(host, http_factory())
        try:
            html = http.text(lot.url)
        except BlockedError:
            blocked.add(host)
            continue
        except Exception as e:  # try again next scan
            log.info("no description for %s: %s", lot.key, type(e).__name__)
            continue
        read += 1
        lot.description = description_from_page(html)
        cache[lot.key] = {"d": lot.description, "at": now.isoformat()}
    for key in [k for k, v in cache.items() if now - datetime.fromisoformat(v["at"]) > timedelta(days=30)]:
        del cache[key]
    return read
