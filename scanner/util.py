"""Small parsing helpers: text normalisation, money, Dutch dates."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

AMS = ZoneInfo("Europe/Amsterdam")

_MONTHS = {
    "jan": 1, "feb": 2, "mrt": 3, "maa": 3, "mar": 3, "apr": 4, "mei": 5, "may": 5,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "okt": 10, "oct": 10, "nov": 11, "dec": 12,
}


def normalize(text: str) -> str:
    """Lowercase, strip accents, keep only letters/digits separated by single spaces."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return text.strip()


def token_in(token: str, text_norm: str) -> bool:
    """True if `token` starts a word in normalised text (numbers must match a whole word)."""
    token = normalize(token)
    if not token:
        return False
    if token.isdigit():
        pattern = rf"(?<![a-z0-9]){re.escape(token)}(?![0-9])"
    else:
        pattern = rf"(?<![a-z0-9]){re.escape(token)}"
    return re.search(pattern, text_norm) is not None


def phrase_in(phrase: str, text_norm: str) -> bool:
    """All words of `phrase` appear in the text (any order), or the phrase appears with spaces removed."""
    words = normalize(phrase).split()
    if not words:
        return False
    if all(token_in(w, text_norm) for w in words):
        return True
    if len(words) == 1:
        return False
    # "iphone 15" should also match a title written as "iPhone15Pro", but not "iPhone150"
    squashed = "".join(words)
    for tok in text_norm.split():
        if tok.startswith(squashed):
            nxt = tok[len(squashed):len(squashed) + 1]
            if not (squashed[-1].isdigit() and nxt.isdigit()):
                return True
    return False


def parse_money(text) -> float | None:
    """'€ 1.250,00' -> 1250.0, '110,00' -> 110.0, '€ 60' -> 60.0."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = re.sub(r"[^0-9.,]", "", str(text))
    if not s or not re.search(r"\d", s):
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def month_number(word: str) -> int | None:
    return _MONTHS.get(normalize(word)[:3])


def parse_dutch_datetime(text: str) -> datetime | None:
    """Parse things like '28 sep. 2026 20:00:00 (CEST)', '28 September 2026' or
    'maandag 28 september 2026 vanaf 20:00'. Returns an Amsterdam-aware datetime."""
    if not text:
        return None
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})(?:\D{0,12}?(\d{1,2}):(\d{2})(?::(\d{2}))?)?", text)
    if not m:
        return None
    month = month_number(m.group(2))
    if not month:
        return None
    hour = int(m.group(4)) if m.group(4) else 23
    minute = int(m.group(5)) if m.group(5) else 59
    try:
        return datetime(int(m.group(3)), month, int(m.group(1)), hour, minute, tzinfo=AMS)
    except ValueError:
        return None


def parse_relative_close(text: str, now: datetime) -> datetime | None:
    """Proveiling style: 'morgen vanaf 20:25', 'vandaag vanaf 21:00', '6 dagen', '3 uur', '25 minuten'."""
    t = normalize(text)
    local_now = now.astimezone(AMS)
    m = re.search(r"(vandaag|morgen|overmorgen)?\s*(?:vanaf|om)?\s*\b(\d{1,2}) (\d{2})\b", t)
    if m and (m.group(1) or re.search(r"\b(vanaf|om)\b", t)):
        day_offset = {"vandaag": 0, "morgen": 1, "overmorgen": 2}.get(m.group(1) or "vandaag", 0)
        day = (local_now + timedelta(days=day_offset)).date()
        return datetime(day.year, day.month, day.day, int(m.group(2)), int(m.group(3)), tzinfo=AMS)
    total = timedelta()
    found = False
    for num, unit in re.findall(r"(\d+)\s*(dag|dagen|d|uur|u|h|minuut|minuten|min|m)\b", t):
        found = True
        n = int(num)
        if unit.startswith("d"):
            total += timedelta(days=n)
        elif unit in ("uur", "u", "h"):
            total += timedelta(hours=n)
        else:
            total += timedelta(minutes=n)
    if found:
        return now + total
    return None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def from_epoch(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_eur(amount: float | None) -> str:
    if amount is None:
        return "?"
    return "€" + f"{amount:,.0f}".replace(",", ".")


def fmt_close(closes_at: datetime | None, now: datetime) -> str:
    if not closes_at:
        return "unknown"
    local = closes_at.astimezone(AMS)
    delta = closes_at - now
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][local.weekday()]
    when = f"{days} {local.day} {local.strftime('%b')} {local:%H:%M}"
    secs = int(delta.total_seconds())
    if secs <= 0:
        return f"{when} (closing now)"
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    left = f"{d}d {h}h" if d else (f"{h}h {m}m" if h else f"{m}m")
    return f"{when} (in {left})"
