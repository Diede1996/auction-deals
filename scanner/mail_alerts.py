"""Troostwijk lots from Troostwijk's own search-alert emails.

Troostwijk refuses automated visitors, so the bot never visits troostwijkauctions.com. Instead you save
searches on Troostwijk, forward their alert emails to a separate mailbox, and the bot reads that
mailbox (IMAP, e.g. a Gmail address with an app password). Every lot link in those emails becomes a
lot on the dashboard, with a Marktplaats price and a max bid like any other lot.

- Only emails that contain troostwijkauctions.com links are used; everything else is ignored.
- Links hidden behind a mail tracker are resolved by asking the tracker where it points (without
  loading the Troostwijk page itself).
- Lots are remembered in data/state.json until they close, or `keep_days` after the last alert when the
  email doesn't say when they close. Emails themselves are never stored (the repository is public).
"""
from __future__ import annotations

import email
import hashlib
import imaplib
import logging
import os
import re
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

from .models import Lot
from .util import parse_dutch_datetime, parse_money

log = logging.getLogger(__name__)

SITE = "troostwijk"
BASE = "https://www.troostwijkauctions.com"
_LOT_RE = re.compile(r"https?://(?:www\.)?troostwijkauctions\.com/[a-z]{2}/l/([^\s\"'<>?#&]+)", re.I)
_DISPLAY_ID = re.compile(r"([A-Z]\d{1,2}-\d+-\d+)$")
_GENERIC = re.compile(r"^(bekijk|bied|bieden|view|bid|see|more|meer|lees|open|kavel|lot|klik|click|hier|here)\b", re.I)


def _decode(value) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return str(value or "")


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


def lot_url(href: str) -> str | None:
    """The Troostwijk lot link in a href, also when it's URL-encoded inside a tracking link."""
    candidate = href or ""
    for _ in range(3):
        m = _LOT_RE.search(candidate)
        if m:
            return f"{BASE}/nl/l/{m.group(1)}"
        decoded = unquote(candidate)
        if decoded == candidate:
            break
        candidate = decoded
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


def _context(link) -> str:
    """Text of the smallest block around a link that holds only this lot (bid, closing time, place)."""
    node = link
    for _ in range(6):
        parent = node.parent
        if parent is None or parent.name in ("body", "html", "[document]"):
            break
        lots_inside = {lot_url(a.get("href", "")) for a in parent.find_all("a", href=True)} - {None}
        if len(lots_inside) > 1:
            break
        node = parent
    lines = (re.sub(r"\s+", " ", t).strip() for t in node.get_text("\n").splitlines())
    return "\n".join(t for t in lines if t)


def parse_email_html(html: str, resolve=None) -> list[dict]:
    """Lots in one alert email: [{url, lot_id, title, bid, closes, location}]. `resolve(href)` turns a
    tracking link into the URL it redirects to (or None)."""
    soup = BeautifulSoup(html or "", "html.parser")
    found: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        url = lot_url(href)
        if not url and resolve and is_tracker(href):
            target = resolve(href)
            url = lot_url(target or "")
        if not url:
            continue
        entry = found.setdefault(url, {"url": url, "lot_id": lot_id_of(url), "titles": [], "context": ""})
        text = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        img = a.find("img")
        if not text and img is not None:
            text = (img.get("alt") or "").strip()
            entry.setdefault("image", img.get("src"))
        if text and not _GENERIC.match(text) and len(text) > 3:
            entry["titles"].append(text)
        ctx = _context(a)
        if len(ctx) > len(entry["context"]):
            entry["context"] = ctx
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
        loc = re.search(r"(?:locatie|location|plaats)\s*:?\s*([A-Za-zÀ-ÿ' -]{2,40}?)\s*(?:$|[€|·,\d])", ctx, re.I | re.M)
        out.append({"url": e["url"], "lot_id": e["lot_id"], "title": title[:200], "bid": bid,
                    "closes": closes.isoformat() if closes else None,
                    "location": loc.group(1).strip().title() if loc else None, "image": e.get("image")})
    return out


def from_troostwijk(msg: Message, html: str) -> bool:
    sender = _decode(msg.get("From")).lower()
    return "troostwijk" in sender or "troostwijkauctions.com" in (html or "").lower()


class TrackerResolver:
    """Asks a mail tracker where a link points, without following it to the Troostwijk website."""

    def __init__(self, http, cache: dict, limit: int = 60):
        self.http = http
        self.cache = cache  # sha256(tracking link) -> lot URL or "" (the links themselves may identify you)
        self.left = limit

    def __call__(self, href: str) -> str | None:
        key = hashlib.sha256(href.encode()).hexdigest()[:24]
        if key in self.cache:
            return self.cache[key] or None
        url, hops = href, 0
        while hops < 4 and self.left > 0 and is_tracker(url):
            self.left -= 1
            hops += 1
            try:
                resp = self.http.get(url, allow_redirects=False, timeout=15)
            except Exception as e:
                log.info("tracking link not resolved: %s", type(e).__name__)
                break
            location = (getattr(resp, "headers", {}) or {}).get("location") or (getattr(resp, "headers", {}) or {}).get("Location")
            if not location:
                m = _LOT_RE.search(getattr(resp, "text", "") or "")  # some trackers use a meta refresh
                url = m.group(0) if m else url
                break
            url = location
            if lot_url(url):
                break
        found = lot_url(url)
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


def collect(mailbox: Mailbox, http, state: dict, now: datetime, keep_days: int = 14) -> tuple[list[Lot], dict]:
    """Read the alert emails, update the remembered Troostwijk lots and return the running ones.
    The report says how many emails and lots were found, for the digest and the dashboard."""
    store = state.setdefault("troostwijk_alerts", {})
    lots_seen = store.setdefault("lots", {})
    resolver = TrackerResolver(http, store.setdefault("links", {}))
    report = {"emails": 0, "troostwijk_emails": 0, "unreadable": 0, "new": 0}
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
        found = parse_email_html(html, resolver)
        if not found:
            report["unreadable"] += 1
            continue
        for f in found:
            rec = lots_seen.get(f["lot_id"])
            if rec is None:
                report["new"] += 1
                rec = lots_seen[f["lot_id"]] = {"first": now.isoformat()}
            if sent.tzinfo is None:
                sent = sent.replace(tzinfo=now.tzinfo)
            if not rec.get("mail") or sent >= datetime.fromisoformat(rec["mail"]):  # the newest email wins
                rec.update({k: v for k, v in f.items() if v is not None and k != "lot_id"})
                rec["mail"] = sent.isoformat()
            rec["seen"] = now.isoformat()
    # forget closed lots, and lots without a closing time that no alert mentioned for keep_days
    for lot_id, rec in list(lots_seen.items()):
        closes = datetime.fromisoformat(rec["closes"]) if rec.get("closes") else None
        last = datetime.fromisoformat(rec.get("seen") or rec["first"])
        if (closes and closes < now) or (not closes and now - last > timedelta(days=keep_days)):
            del lots_seen[lot_id]
    lots = []
    for lot_id, rec in lots_seen.items():
        mailed = datetime.fromisoformat(rec["mail"]) if rec.get("mail") else None
        lots.append(Lot(
            site=SITE, lot_id=lot_id, title=rec.get("title") or lot_id, url=rec["url"],
            current_bid=rec.get("bid"),
            closes_at=datetime.fromisoformat(rec["closes"]) if rec.get("closes") else None,
            auction_title="Troostwijk alert" + (f" of {mailed:%d %b}" if mailed else ""),
            image=rec.get("image"), location=rec.get("location"),
        ))
    report["lots"] = len(lots)
    return lots, report
