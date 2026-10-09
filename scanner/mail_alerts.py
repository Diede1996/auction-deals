"""Troostwijk lots from Troostwijk's own search-alert emails.

Troostwijk refuses automated visitors, so the bot never visits troostwijkauctions.com. Instead you save
searches on Troostwijk, forward their alert emails to a separate mailbox, and the bot reads that
mailbox (IMAP, e.g. a Gmail address with an app password). Every lot link in those emails becomes a
lot on the dashboard, with a Marktplaats price and a max bid like any other lot.

- Only emails that contain troostwijkauctions.com links are used; everything else is ignored.
- Troostwijk's mailing service (Exponea/Bloomreach) hides every link behind a tracking link, but the
  destination is inside that link, compressed. The bot reads it from there, so it contacts nobody and
  no "clicks" are registered. Only a link that can't be read that way is looked up by asking the tracker
  where it points (never an unsubscribe or preferences link, and never the Troostwijk page itself).
- The auction block at the top of an alert (name, place, closing day) is copied onto its lots.
- The weekly saved-search email ("Je opgeslagen zoekopdrachten") holds no lots, only a link per search
  term to Troostwijk's search page. Those links go into the Telegram digest so you can tap through yourself.
- Notifications about one lot ("Je favoriete kavel sluit binnenkort: …", "Je bent overboden op: …") name a
  lot you follow or bid on, so they are never stored and their subject is never used. Lots you follow (the
  heart on Troostwijk) are passed on to the scan for the Telegram digest only.
- Lots are remembered in data/state.json until they close, or `keep_days` after the last alert when the
  email doesn't say when they close. Emails themselves are never stored (the repository is public).
"""
from __future__ import annotations

import base64
import email
import hashlib
import imaplib
import logging
import os
import re
import zlib
from datetime import date, datetime, timedelta
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, unquote, urlencode, urlparse

from bs4 import BeautifulSoup
from bs4.element import NavigableString

from .models import Lot
from .util import AMS, month_number, parse_dutch_datetime, parse_money

log = logging.getLogger(__name__)

SITE = "troostwijk"
BASE = "https://www.troostwijkauctions.com"
_LOT_RE = re.compile(r"https?://(?:www\.)?troostwijkauctions\.com(?:/[a-z]{2})?/l/([^\s\"'<>?#&/]+)", re.I)
_AUCTION_RE = re.compile(r"https?://(?:www\.)?troostwijkauctions\.com(?:/[a-z]{2})?/a/([^\s\"'<>?#&/]+)", re.I)
_DISPLAY_ID = re.compile(r"([A-Z]\d{1,2}-\d+-\d+)$")
_AUCTION_ID = re.compile(r"([A-Z]\d{1,2}-\d+)$")
_GENERIC = re.compile(r"^(?:(?:bekijk|bied|bieden|view|bid|see|more|meer|lees|open|kavel|lot|klik|click|hier|here)\b|"
                      r"(?:plaats|doe) (?:je|een|uw) bod\b|place (?:your|a) bid\b)", re.I)  # buttons, not titles
# links the bot must never "click", not even to see where they go
_NO_CLICK = re.compile(r"unsubscri|uitschrijv|afmeld|opt-?out|consent|preferen|voorkeur|privacy|manage", re.I)
_B64_CHUNK = re.compile(r"[A-Za-z0-9_-]{24,}")
_URL_START = re.compile(rb"https?://")
_URL_CHARS = re.compile(rb"https?://[\x21-\x7e]+")
_DATE_RE = re.compile(r"\b(\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4})(?:[^\d\n]{0,12}?(\d{1,2}:\d{2}))?")
_SEARCH_PATH = re.compile(r"^(?:/[a-z]{2})?/search/?$", re.I)
_SITE_URL = re.compile(r"https?://(?:www\.)?troostwijkauctions\.com[^\s\"'<>]*", re.I)
# Troostwijk's notifications about one lot you follow, bid on or bought. Their subject names that lot, so it is
# never used as an auction name or stored (the repository is public).
# Not "Laatste kans: fabriekssluiting ..." or "Laatste kans! Premium Duitse kavels ...": those are auctions.
_NOTICE = re.compile(r"^(?:gefeliciteerd!?\s*)?(?:je favoriete kavel|laatste kans om te bieden|je bent overboden|"
                     r"bod geplaatst|je hebt (?:een bod|kavel|gewonnen|betaald)|uw bod|de directe verkoop|je aankoop|"
                     r"bedankt, je hebt betaald|afhaalgegevens|laatste ophaalmogelijkheid|your favou?rite lot|"
                     r"last chance to bid|you(?:'ve| have) been outbid|you(?:'ve| have) won)", re.I)
_FOLLOWED = re.compile(r"^(?:je favoriete kavel|your favou?rite lot)", re.I)  # the heart on a lot
# "Sluiting: 6 okt om 20:33", "20 Jul 10:58 CEST": a closing time without a year
_CLOSE_NO_YEAR = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(?:om\s+|at\s+)?(\d{1,2}):(\d{2})\b")
_TOWN_LINE = re.compile(r"^([A-Za-zÀ-ÿ'’. -]{2,40}?),\s*(NL|BE|DE|LU|FR)$")
_COUNTRY = {"NL": "", "BE": "Belgium", "DE": "Germany", "LU": "Luxembourg", "FR": "France"}


def _decode(value) -> str:
    try:
        text = str(make_header(decode_header(value or "")))
    except Exception:
        text = str(value or "")
    return re.sub(r"\s+", " ", text).strip()  # a long subject is folded over several lines


def html_of(msg: Message) -> str:
    """The HTML body (or the plain text) of an email, forwarded ones included."""
    html, text = [], []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        try:
            payload = part.get_payload(decode=True)
            body = payload.decode(part.get_content_charset() or "utf-8", errors="replace") if payload else ""
        except Exception:
            continue
        if ctype == "text/html":
            html.append(body)
        elif ctype == "text/plain":
            text.append(body)
        elif ctype == "message/rfc822":  # forwarded as an attachment
            for inner in part.get_payload() or []:
                if isinstance(inner, Message):
                    html.append(html_of(inner))
    if html:
        return "\n".join(html)
    return "<pre>" + "\n".join(text).replace("<", "&lt;") + "</pre>"


def _find(pattern: re.Pattern, kind: str, href: str) -> str | None:
    candidate = href or ""
    for _ in range(3):
        m = pattern.search(candidate)
        if m:
            return f"{BASE}/nl/{kind}/{m.group(1)}"
        decoded = unquote(candidate)
        if decoded == candidate:
            break
        candidate = decoded
    return None


def lot_url(href: str) -> str | None:
    """The Troostwijk lot link in a href, also when it's URL-encoded inside a tracking link."""
    return _find(_LOT_RE, "l", href)


def auction_url(href: str) -> str | None:
    return _find(_AUCTION_RE, "a", href)


def _urls_in(data: bytes) -> list[str]:
    """URLs inside a decoded tracking payload. Exponea stores them length-prefixed (protobuf), so the
    length decides where a URL ends: the byte after it may look like part of the URL."""
    out = []
    for m in _URL_START.finditer(data):
        start, found = m.start(), None
        for n in (1, 2):  # the length as a 1- or 2-byte varint right before the URL
            if start < n:
                continue
            head = data[start - n:start]
            if n == 1 and head[0] < 0x80:
                size = head[0]
            elif n == 2 and head[0] >= 0x80 and head[1] < 0x80:
                size = (head[0] & 0x7F) | (head[1] << 7)
            else:
                continue
            chunk = data[start:start + size]
            if len(chunk) == size and _URL_CHARS.fullmatch(chunk):
                found = chunk.decode()
                break
        if found is None:  # plain text or JSON: the URL ends at the first character that can't be in one
            plain = re.match(rb"https?://[^\s\"'<>\\\x00-\x20\x7f-\xff]+", data[start:])
            found = plain.group(0).decode() if plain else None
        if found:
            out.append(found)
    return out


def _unpack(chunk: str) -> list[bytes]:
    try:
        raw = base64.urlsafe_b64decode(chunk + "=" * (-len(chunk) % 4))
    except (ValueError, TypeError):
        return []
    out = [raw]
    for wbits in (15, -15):  # zlib, raw deflate
        try:
            out.append(zlib.decompressobj(wbits).decompress(raw, 200_000))
        except zlib.error:
            pass
    return out


def tracker_target(href: str) -> tuple[bool, str | None]:
    """Where a tracking link points, read from the link itself (any URL, not only lots)."""
    for chunk in _B64_CHUNK.findall(urlparse(href or "").path + "?" + (urlparse(href or "").query or "")):
        for data in _unpack(chunk):
            urls = _urls_in(data)
            if urls:
                return True, urls[0]
    return False, None


def decode_tracker(href: str) -> tuple[bool, str | None]:
    """Read where a tracking link points without opening it. Returns (could read it, the lot or auction
    URL or None). Troostwijk's links look like cdn.eu1.exponea.com/troostwijk-prod/e/.<compressed>.<sig>/click"""
    readable, url = tracker_target(href)
    return readable, (lot_url(url) or auction_url(url)) if url else None


def search_link(url: str) -> dict | None:
    """A Troostwijk search page ("…/nl/search?countries=nl%2Cbe&searchTerm=festool") ->
    {"term": "festool", "url": the same link without tracking parameters}."""
    parsed = urlparse(url or "")
    if (parsed.hostname or "").lower() not in ("www.troostwijkauctions.com", "troostwijkauctions.com") \
            or not _SEARCH_PATH.match(parsed.path):
        return None
    query = [(k, v) for k, v in parse_qsl(parsed.query) if not k.lower().startswith("utm_")]
    term = next((v.strip() for k, v in query if k.lower() in ("searchterm", "query", "q")), "")
    if not term:
        return None
    return {"term": term, "url": BASE + parsed.path + (f"?{urlencode(query)}" if query else "")}


def _search_of(href: str) -> dict | None:
    """The search page a link (or the tracking link around it) points to. Never asks the tracker."""
    _, decoded = tracker_target(href) if is_tracker(href) else (False, None)
    for text in (decoded, href, unquote(href)):
        m = _SITE_URL.search(text or "")
        found = search_link(m.group(0)) if m else None
        if found:
            return found
    return None


def is_tracker(href: str) -> bool:
    """A link that may redirect to a lot: anything http(s) that isn't the Troostwijk website itself."""
    host = (urlparse(href).hostname or "").lower()
    return href.startswith("http") and host not in ("www.troostwijkauctions.com", "troostwijkauctions.com")


def title_from_url(url: str) -> str:
    slug = unquote(url.rsplit("/", 1)[-1])
    slug = _DISPLAY_ID.sub("", slug).strip("-")
    words = re.sub(r"[-_]+", " ", slug).strip()
    return words[:1].upper() + words[1:] if words else url


def lot_id_of(url: str) -> str:
    slug = url.rsplit("/", 1)[-1]
    m = _DISPLAY_ID.search(slug)
    return m.group(1) if m else hashlib.sha1(slug.encode()).hexdigest()[:12]


def auction_id_of(url: str) -> str | None:
    m = _AUCTION_ID.search(url.rsplit("/", 1)[-1])
    return m.group(1) if m else None


def _context(link, lot_of: dict, own: str | None) -> str:
    """Text of the smallest block around a link that holds no other lot (bid, closing time, place).
    `lot_of` maps each link to the lot it points to (tracking links included)."""
    node = link
    for _ in range(8):
        parent = node.parent
        if parent is None or parent.name in ("body", "html", "[document]"):
            break
        others = {lot_of.get(id(a)) for a in parent.find_all("a", href=True)} - {None, own}
        if others:
            break
        node = parent
    lines = (re.sub(r"\s+", " ", t).strip() for t in node.get_text("\n").splitlines())
    return "\n".join(t for t in lines if t)


def _town(ctx: str) -> str | None:
    """"Veldhoven, NL" -> "Veldhoven"; "Gent, BE" -> "Gent, Belgium" (so the map looks abroad)."""
    for line in ctx.splitlines():
        m = _TOWN_LINE.match(line.strip())
        if m:
            country = _COUNTRY.get(m.group(2).upper(), "")
            return m.group(1).strip() + (f", {country}" if country else "")
    m = re.search(r"(?:locatie|location|plaats)\s*:?\s*([A-Za-zÀ-ÿ' -]{2,40}?)\s*(?:$|[€|·,\d])", ctx, re.I | re.M)
    return m.group(1).strip().title() if m else None


def close_without_year(text: str, sent: datetime) -> datetime | None:
    """"Sluiting: 6 okt om 20:33" or "20 Jul 10:58 CEST" in an email sent on `sent`: the year is the email's
    (or the next one, for an email from late December about early January)."""
    sent_local = sent.astimezone(AMS)
    for m in _CLOSE_NO_YEAR.finditer(text or ""):
        month = month_number(m.group(2))
        if not month:
            continue
        for year in (sent_local.year, sent_local.year + 1):
            try:
                when = datetime(year, month, int(m.group(1)), int(m.group(3)), int(m.group(4)), tzinfo=AMS)
            except ValueError:
                break
            if when >= sent - timedelta(days=1):
                return when
    return None


def _visible(text) -> bool:
    """Text the reader sees: not an HTML comment ("<!-- ── DESKTOP SUB-HEADER ── -->"), style or script."""
    return type(text) is NavigableString and text.parent is not None and text.parent.name not in (
        "style", "script", "head", "title")


def _placename(text: str) -> bool:
    """"Loon op zand", "De Rips", "Veldhoven": a few words, no digits or punctuation."""
    return bool(re.fullmatch(r"[A-Za-zÀ-ÿ'’. -]{2,40}", text)) and len(text.split()) <= 4


def _auction_info(soup, ctx: str, aid: str) -> dict:
    """Name, place and closing day of an auction from the block at the top of an alert: the name, then
    "A1-50252 - Veldhoven" (or "Loon op zand" and "A1-38890" apart), then the viewing and closing day."""
    info: dict = {}
    hit = soup.find(string=lambda t: t and aid in t and _visible(t))
    if hit is not None:
        m = re.match(rf"{re.escape(aid)}\s*[-–|·]\s*(.+)$", re.sub(r"\s+", " ", hit).strip())
        if m:
            info["town"] = m.group(1).strip()
        before = []  # the visible texts right before the id: the name, and maybe the place
        for node in hit.find_all_previous(string=True, limit=80):
            text = re.sub(r"\s+", " ", node).strip()
            if (_visible(node) and len(text) > 2 and not _GENERIC.match(text) and not _DATE_RE.search(text)
                    and aid not in text and node.find_parent("a") is None):  # not a menu link ("Alle veilingen")
                before.append(text)
                if len(before) == 2:
                    break
        if "town" not in info and len(before) == 2 and _placename(before[0]) and len(before[1]) > len(before[0]):
            info["town"] = before.pop(0)
        if before and len(before[0]) > 8:
            info["name"] = before[0][:90]
    if "town" not in info:  # "A1-50252 -" and "Veldhoven" on separate lines
        m = re.search(rf"{re.escape(aid)}\s*[-–|·]\s*([A-Za-zÀ-ÿ'’. -]{{2,40}})$", ctx, re.M)
        if m:
            info["town"] = m.group(1).strip()
    # the last date in the block is the closing day (the first one, if there are two, is the viewing day)
    dates = []
    for m in _DATE_RE.finditer(ctx):
        when = parse_dutch_datetime(m.group(1) + (" " + m.group(2) if m.group(2) else ""))
        if when:
            dates.append((when, bool(m.group(2))))
    if dates:
        when, has_time = max(dates, key=lambda d: d[0])
        if has_time:
            info["closes"] = when.isoformat()
        else:
            info["closes_day"] = when.date().isoformat()
    return info


def _target(a, href: str, resolve) -> str | None:
    """The lot or auction a link points to: written in the link, read from a tracking link, or (last
    resort) by asking the tracker."""
    url = lot_url(href) or auction_url(href)
    if url:
        return url
    readable, url = decode_tracker(href)
    if readable:
        return url
    text = a.get_text(" ", strip=True)
    if resolve and is_tracker(href) and not _NO_CLICK.search(text) and not _NO_CLICK.search(unquote(href)):
        return resolve(href)
    return None


def parse_email_html(html: str, resolve=None, stats: dict | None = None) -> list[dict]:
    """Lots in one alert email: [{url, lot_id, title, bid, closes, closes_day, location, auction, image}].
    `resolve(href)` asks a tracking link where it points, for links whose destination can't be read.
    `stats` gets the number of auctions linked, also when the email has no lots."""
    soup = BeautifulSoup(html or "", "html.parser")
    links = [(a, _target(a, a["href"].strip(), resolve)) for a in soup.find_all("a", href=True)]
    lot_of = {id(a): url for a, url in links if url and lot_url(url)}
    found: dict[str, dict] = {}
    auctions: dict[str, dict] = {}
    for a, url in links:
        if not url:
            continue
        if not lot_url(url):  # an auction link: its block holds the auction's name, place and dates
            aid = auction_id_of(url)
            if aid and aid not in auctions:
                auctions[aid] = _auction_info(soup, _context(a, lot_of, None), aid)
            continue
        entry = found.setdefault(url, {"url": url, "lot_id": lot_id_of(url), "titles": [], "context": ""})
        text = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        img = a.find("img")
        if not text and img is not None:
            text = (img.get("alt") or "").strip()
            entry.setdefault("image", img.get("src"))
        if text and not _GENERIC.match(text) and len(text) > 3:
            entry["titles"].append(text)
        ctx = _context(a, lot_of, url)
        if len(ctx) > len(entry["context"]):
            entry["context"] = ctx
    if stats is not None:
        stats["auctions"] = len(auctions)
        searches: dict[str, dict] = {}  # the weekly saved-search email: one link per search term
        for a, url in links:
            found_search = None if url else _search_of(a["href"].strip())
            if found_search:
                searches.setdefault(found_search["term"].lower(), found_search)
        stats["searches"] = list(searches.values())
    out = []
    for e in found.values():
        title = max(e["titles"], key=len) if e["titles"] else title_from_url(e["url"])
        ctx = e["context"]
        bid = None
        m = re.search(r"(?:bod|bid|huidig|current|prijs|price)[^€\d]{0,25}€\s*([\d.,]+)", ctx, re.I) or \
            re.search(r"€\s*([\d.,]+)", ctx)
        if m:
            bid = parse_money(m.group(1))
        closes = None
        m = re.search(r"(?:sluit|sluiting|eindigt|closes|closing|ends?)\b[^0-9]{0,30}(.{0,40})", ctx, re.I)
        if m:
            closes = parse_dutch_datetime(m.group(1))
        auction = auctions.get(e["lot_id"].rsplit("-", 1)[0]) or {}
        out.append({"url": e["url"], "lot_id": e["lot_id"], "title": title[:200], "bid": bid,
                    "closes": closes.isoformat() if closes else auction.get("closes"),
                    "closes_day": None if closes else auction.get("closes_day"),
                    "location": _town(ctx) or auction.get("town"), "auction": auction.get("name"),
                    "image": e.get("image")})
    return out


def from_troostwijk(msg: Message, html: str) -> bool:
    sender = _decode(msg.get("From")).lower()
    return "troostwijk" in sender or "troostwijkauctions.com" in (html or "").lower()


class TrackerResolver:
    """Asks a mail tracker where a link points, without following it to the Troostwijk website.
    Only used for links whose destination can't be read from the link itself."""

    def __init__(self, http, cache: dict, limit: int = 60):
        self.http = http
        if len(cache) > 2000:  # old answers; the links in new emails are different anyway
            cache.clear()
        self.cache = cache  # sha256(tracking link) -> lot/auction URL or "" (the links themselves may identify you)
        self.left = limit

    def __call__(self, href: str) -> str | None:
        key = hashlib.sha256(href.encode()).hexdigest()[:24]
        if key in self.cache:
            return self.cache[key] or None
        url, hops, failed = href, 0, False
        while hops < 4 and self.left > 0 and is_tracker(url) and not _NO_CLICK.search(unquote(url)):
            self.left -= 1
            hops += 1
            try:
                resp = self.http.get(url, allow_redirects=False, timeout=15)
            except Exception as e:
                log.info("tracking link not resolved: %s", type(e).__name__)
                failed = True  # try again next time
                break
            headers = getattr(resp, "headers", {}) or {}
            location = headers.get("location") or headers.get("Location")
            if not location:
                m = _LOT_RE.search(getattr(resp, "text", "") or "")  # some trackers use a meta refresh
                url = m.group(0) if m else url
                break
            url = location
            if lot_url(url) or auction_url(url):
                break
        found = lot_url(url) or auction_url(url)
        if found or not failed:
            self.cache[key] = found or ""
        return found


class Mailbox:
    """IMAP access to the alerts mailbox (ALERTS_EMAIL / ALERTS_APP_PASSWORD / ALERTS_IMAP_HOST)."""

    def __init__(self, user: str, password: str, host: str = "imap.gmail.com", imap_cls=imaplib.IMAP4_SSL):
        self.user, self.password, self.host, self.imap_cls = user, password, host, imap_cls

    @classmethod
    def from_env(cls) -> "Mailbox | None":
        user = os.environ.get("ALERTS_EMAIL", "").strip()
        password = os.environ.get("ALERTS_APP_PASSWORD", "").replace(" ", "").strip()
        if not user or not password:
            return None
        return cls(user, password, os.environ.get("ALERTS_IMAP_HOST", "").strip() or "imap.gmail.com")

    def messages(self, since: datetime, limit: int = 60) -> list[Message]:
        imap = self.imap_cls(self.host)
        try:
            imap.login(self.user, self.password)
            imap.select("INBOX", readonly=True)
            status, data = imap.search(None, "SINCE", since.strftime("%d-%b-%Y"))
            ids = (data[0] or b"").split() if status == "OK" and data else []
            out = []
            for num in ids[-limit:]:
                status, parts = imap.fetch(num, "(RFC822)")
                raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and len(p) > 1), None)
                if status == "OK" and raw:
                    out.append(email.message_from_bytes(raw))
            return out
        finally:
            try:
                imap.logout()
            except Exception:
                pass


_FWD = re.compile(r"^\s*(?:(?:fwd?|fw|doorst|tr|wg|re)\s*:\s*)+", re.I)
_JUNK = re.compile(r"&#|[─━═]|<|>")


def _subject(msg: Message) -> str:
    return _FWD.sub("", _decode(msg.get("Subject"))).strip()


def collect(mailbox: Mailbox, http, state: dict, now: datetime, keep_days: int = 14,
            is_bankruptcy=None, only_bankruptcy: bool = False) -> tuple[list[Lot], dict]:
    """Read the alert emails, update the remembered Troostwijk lots and return the running ones.
    Saved-search emails (links to search pages, no lots) are passed on once each, for the digest, and lots
    you follow (reminders about a lot with a heart) are passed on as report["followed"], never stored.
    `is_bankruptcy(text)` tells bankruptcy/closure auctions by their name; with `only_bankruptcy`, lots of
    other auctions (Troostwijk also sells for businesses, e.g. "Computers, Tablets, ...") are left out.
    The report says how many emails and lots were found, for the digest and the dashboard."""
    store = state.setdefault("troostwijk_alerts", {})
    lots_seen = store.setdefault("lots", {})
    resolver = TrackerResolver(http, store.setdefault("links", {}))
    # emails already reported as unreadable: hash of the Message-ID -> when (no subjects: the repository is public)
    warned = store.setdefault("warned", {})
    told = store.setdefault("searches_told", {})  # the same for saved-search emails passed on to Telegram
    # lots stored from a notification before these were recognised carry its subject: forget them
    for lot_id in [k for k, rec in lots_seen.items() if _NOTICE.match(rec.get("auction") or "")]:
        del lots_seen[lot_id]
    followed: dict[str, dict] = {}  # lots you follow, from the emails still in the mailbox; never stored
    report = {"emails": 0, "troostwijk_emails": 0, "unreadable": 0, "no_lots": 0, "new": 0, "other_auctions": 0,
              "notices": 0,  # emails about one lot you follow, bid on or bought
              "unreadable_emails": [],  # subject + date of new unreadable emails, for Telegram only
              "saved_searches": [],  # date + search links of new saved-search emails, for Telegram only
              "followed": []}  # Lots you follow that are still running, for Telegram only
    for msg in mailbox.messages(now - timedelta(days=keep_days)):
        report["emails"] += 1
        html = html_of(msg)
        if not from_troostwijk(msg, html):
            continue
        report["troostwijk_emails"] += 1
        try:
            sent = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else now
        except (TypeError, ValueError):
            sent = now
        if sent.tzinfo is None:
            sent = sent.replace(tzinfo=now.tzinfo)
        subject_line = _subject(msg)
        key = hashlib.sha256((msg.get("Message-ID") or f"{msg.get('Date')}|{msg.get('Subject')}").encode()).hexdigest()[:16]
        stats: dict = {}
        if _NOTICE.match(subject_line):  # about one lot you follow, bid on or bought: never stored
            report["notices"] += 1
            if not _FOLLOWED.match(subject_line):
                continue  # not read at all, so none of its links is looked up either
            # links only read from the link itself (resolve=None): nothing about this lot is opened or saved
            found = parse_email_html(html, None, stats)
            closes = close_without_year(_text_of(html), sent)
            for f in found:
                old = followed.get(f["lot_id"])
                if old is None or sent >= old["mail"]:  # the newest reminder wins
                    followed[f["lot_id"]] = {**f, "closes_at": closes, "mail": sent}
            if not found:  # a reminder always names one lot: say so in Telegram
                report["unreadable"] += 1
                if key not in warned:
                    warned[key] = now.isoformat()
                    report["unreadable_emails"].append({"subject": subject_line[:90], "date": sent})
            continue
        found = parse_email_html(html, resolver, stats)
        if not found:
            if stats.get("searches"):  # "Je opgeslagen zoekopdrachten": links to search pages, no lots
                report["no_lots"] += 1
                if key not in told:
                    told[key] = now.isoformat()
                    report["saved_searches"].append({"date": sent, "searches": stats["searches"]})
                continue
            if stats.get("auctions"):  # an announcement of auctions without lots: nothing to price, nothing wrong
                report["no_lots"] += 1
                continue
            report["unreadable"] += 1
            if key not in warned:
                warned[key] = now.isoformat()
                report["unreadable_emails"].append({"subject": subject_line[:90], "date": sent})
            continue
        # an alert about one auction has the auction's name as its subject
        auctions = {f["lot_id"].rsplit("-", 1)[0] for f in found}
        subject = subject_line if len(auctions) == 1 else ""
        for f in found:
            if subject and (not f.get("auction") or _JUNK.search(f["auction"])):
                f["auction"] = subject[:90]
            if is_bankruptcy and f.get("auction"):
                f["bankrupt"] = bool(is_bankruptcy(f"{f['auction']} {subject}"))
            rec = lots_seen.get(f["lot_id"])
            if rec is None:
                report["new"] += 1
                rec = lots_seen[f["lot_id"]] = {"first": now.isoformat()}
            if not rec.get("mail") or sent >= datetime.fromisoformat(rec["mail"]):  # the newest email wins
                rec.update({k: v for k, v in f.items() if v is not None and k != "lot_id"})
                rec["mail"] = sent.isoformat()
            rec["seen"] = now.isoformat()
    for seen_before in (warned, told):
        for key in [k for k, at in seen_before.items() if now - datetime.fromisoformat(at) > timedelta(days=keep_days + 7)]:
            del seen_before[key]  # the email itself is out of the window by now
    # forget closed lots, and lots without a closing time that no alert mentioned for keep_days
    today = now.astimezone(AMS).date()
    for lot_id, rec in list(lots_seen.items()):
        closes = datetime.fromisoformat(rec["closes"]) if rec.get("closes") else None
        day = date.fromisoformat(rec["closes_day"]) if rec.get("closes_day") and not closes else None
        last = datetime.fromisoformat(rec.get("seen") or rec["first"])
        if ((closes and closes < now) or (day and day < today)
                or (not closes and not day and now - last > timedelta(days=keep_days))):
            del lots_seen[lot_id]
    lots = []
    for lot_id, rec in lots_seen.items():
        if only_bankruptcy and rec.get("bankrupt") is False:
            report["other_auctions"] += 1
            continue
        mailed = datetime.fromisoformat(rec["mail"]) if rec.get("mail") else None
        closes = datetime.fromisoformat(rec["closes"]) if rec.get("closes") else None
        day = date.fromisoformat(rec["closes_day"]) if rec.get("closes_day") and not closes else None
        lots.append(Lot(
            site=SITE, lot_id=lot_id, title=rec.get("title") or lot_id, url=rec["url"],
            current_bid=rec.get("bid"), bid_from_email=True, closes_at=closes,
            closes_day=day.isoformat() if day else None,  # Troostwijk lots close one after another that day
            auction_title=rec.get("auction") or ("Troostwijk alert" + (f" of {mailed:%d %b}" if mailed else "")),
            image=rec.get("image"), location=rec.get("location"),
        ))
    report["lots"] = len(lots)
    report["followed"] = followed_lots(followed, lots_seen, now)
    return lots, report


def followed_lots(followed: dict[str, dict], lots_seen: dict, now: datetime) -> list[Lot]:
    """The lots you follow that haven't closed. A reminder comes a day or a few hours before the lot closes,
    so one without a readable closing time counts for two days. The auction's name, place and closing day
    come from other lots of the same auction when an announcement email named them."""
    out = []
    for lot_id, f in followed.items():
        closes = f["closes_at"] or (datetime.fromisoformat(f["closes"]) if f.get("closes") else None)
        if (closes and closes < now) or (not closes and now - f["mail"] > timedelta(days=2)):
            continue
        auction = lot_id.rsplit("-", 1)[0]
        known = next((r for k, r in lots_seen.items() if k.rsplit("-", 1)[0] == auction), {})
        day = None if closes else (f.get("closes_day") or known.get("closes_day"))
        out.append(Lot(
            site=SITE, lot_id=lot_id, title=f.get("title") or lot_id, url=f["url"], current_bid=f.get("bid"),
            bid_from_email=True, closes_at=closes, closes_day=day,
            auction_title=known.get("auction") or f.get("auction") or "", image=f.get("image"),
            location=f.get("location") or known.get("location"),
        ))
    return sorted(out, key=lambda lot: lot.closes_at or now + timedelta(days=2))


def _text_of(html: str) -> str:
    return BeautifulSoup(html or "", "html.parser").get_text("\n")
