from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable

from bs4 import BeautifulSoup

from ..http import Http
from ..util import month_number


@dataclass
class SiteContext:
    http: Http
    now: datetime
    is_bankruptcy: Callable[[str], bool]
    search_terms: list[str] = field(default_factory=list)  # phrases from the watchlist
    settings: dict = field(default_factory=dict)  # this site's block from config.yml
    cache: dict = field(default_factory=dict)  # persisted between runs (per site)
    max_pages: int = 25


_NEXT_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', re.S)


def next_data(html: str) -> dict:
    """Extract the Next.js __NEXT_DATA__ JSON blob from a page."""
    m = _NEXT_RE.search(html)
    if not m:
        raise ValueError("no __NEXT_DATA__ found (page layout changed or request was blocked)")
    return json.loads(m.group(1))


# ---------------------------------------------------------------- pickup details ("ophaallocatie")

_STOP = re.compile(r"^(kijkdag|kijkdagen|sluitdag|sluiting|start|afhaaldag|afhaaldagen|ophaaldag|ophaaldagen|"
                   r"eigenschappen|type veiling|verkoopmeester|veiling|bied historie|omschrijving|datums|"
                   r"voorwaarden|contact|kavels?)\b", re.I)


_BLOCKS = ["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "td", "th", "dt", "dd", "section",
           "article", "header", "footer", "table", "ul", "ol", "dl", "address", "label"]


def text_lines(html: str) -> list[str]:
    """The visible text of a page, one tidy line per block element or <br>; inline elements (links,
    bold text) stay on their line, so "<a>donderdag 01 oktober 2026</a> van 10:00" reads as one line."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for br in soup.find_all("br"):
        br.insert_before("\n")
        br.unwrap()  # html.parser sometimes nests the following text inside a <br />: keep it
    for tag in soup.find_all(_BLOCKS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    lines = []
    for raw in soup.get_text().splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            lines.append(line)
    return lines


def line_index(lines: list[str], labels: list[str]) -> int:
    """Index of the first line that starts with one of the labels (0 if none): where the auction's own
    details start, so an address in the page header or footer isn't picked up by mistake."""
    pat = re.compile(rf"^(?:{'|'.join(labels)})\b", re.I)
    return next((i for i, line in enumerate(lines) if pat.match(line)), 0)


def labelled(lines: list[str], labels: list[str], max_lines: int = 2, check=None, start: int = 0) -> str | None:
    """The value after a label, written as "Adres: X" on one line or as a heading ("Locatie") followed by
    up to `max_lines` lines. `check` can reject a value (e.g. an address without any number)."""
    pat = re.compile(rf"^(?:{'|'.join(labels)})\s*(?::\s*(.*))?$", re.I)
    for i, line in enumerate(lines):
        if i < start:
            continue
        m = pat.match(line)
        if not m:
            continue
        if m.group(1):
            value = m.group(1).strip()
        else:
            out = []
            for nxt in lines[i + 1:i + 1 + max_lines]:
                if nxt.endswith(":") or _STOP.match(nxt) or len(nxt) > 60:
                    break
                out.append(nxt)
            value = ", ".join(out)
        if value and (check is None or check(value)):
            return value
    return None


def has_number(text: str) -> bool:
    return bool(re.search(r"\d", text or ""))


def clean_address(text: str | None) -> str | None:
    """"Produktieweg 9, 8304AV, Emmeloord" -> "Produktieweg 9, 8304AV Emmeloord"; "It Achterbosk 19B te Stiens
    (Friesland)" -> "It Achterbosk 19B, Stiens"; "n.v.t." -> None."""
    if not text:
        return None
    t = re.sub(r"\([^)]*\)", " ", text)
    t = re.sub(r"\s+te\s+", ", ", t)
    t = re.sub(r"(\b\d{4}\s?[A-Za-z]{2}),\s*", r"\1 ", t)
    t = re.sub(r"\s*,\s*", ", ", re.sub(r"\s+", " ", t)).strip(" ,")
    if not t or re.fullmatch(r"(?i)n\.?v\.?t\.?|onbekend|-", t):
        return None
    return t[:120]


def town_of(address: str | None) -> str | None:
    """"Ottolaan 12, 9207 JR Drachten" -> "Drachten"; "Kerkstraat 1, 9000 Gent, België" -> "Gent"."""
    if not address:
        return None
    parts = [p.strip() for p in address.split(",") if p.strip()]
    parts = [p for p in parts if p.lower() not in ("nederland", "the netherlands", "belgië", "belgie", "belgium",
                                                   "deutschland", "duitsland", "germany")]
    if not parts:
        return None
    town = re.sub(r"^\d{4}\s?[A-Za-z]{2}\b|^\d{4,5}\b", "", parts[-1]).strip()
    return town or None


_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def format_when(day: date, start: str | None = None, end: str | None = None) -> str:
    when = f"{_DAYS[day.weekday()]} {day.day} {_MONTHS[day.month - 1]}"
    if start and end:
        return f"{when}, {start}–{end}"
    return f"{when}, {start}" if start else when


def short_when(text: str | None) -> str | None:
    """"donderdag 01 oktober 2026 van 10:00 tot 12:00" or "05-10-2026 08:00-13:00" -> "Thu 1 Oct, 10:00–12:00"."""
    if not text:
        return None
    day = None
    m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})", text)
    if m:
        day = _safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    else:
        m = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})", text)
        if m and month_number(m.group(2)):
            day = _safe_date(int(m.group(3)), month_number(m.group(2)), int(m.group(1)))
    if not day:
        return re.sub(r"\s+", " ", text).strip()[:60] or None
    rest = text[m.end():]
    times = re.findall(r"\b(\d{1,2}[:.]\d{2})\b", rest)
    times = [t.replace(".", ":").zfill(5) for t in times]
    return format_when(day, times[0] if times else None, times[1] if len(times) > 1 else None)


def _safe_date(y: int, mo: int, d: int) -> date | None:
    try:
        return date(y, mo, d)
    except ValueError:
        return None
