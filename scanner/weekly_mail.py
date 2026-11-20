"""The Sunday mail (Diede, 2026-11-20): which favorites close in the coming week, where and when to pick them
up, and whether the bot works. Nothing about the best lots (their price still rises) or profit (that's in the
app). Sent by the Sunday run of the daily Bot check (scanner/health.py), only by email (MAIL_TO).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import quote_plus

from .favorites import closing_between
from .telegram import attr, esc
from .util import AMS, fmt_eur

DAYS = ["ma", "di", "wo", "do", "vr", "za", "zo"]
MONTHS = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]


def nl_time(t: datetime) -> str:
    t = t.astimezone(AMS)
    return f"{DAYS[t.weekday()]} {t.day} {MONTHS[t.month - 1]} {t:%H:%M}"


def is_sunday(now: datetime) -> bool:
    return now.astimezone(AMS).weekday() == 6


def _place(fav: dict, lot: dict) -> str:
    return (lot.get("pickup") or fav.get("pickup") or lot.get("location") or "").strip()


def _groups(favs: list[dict], lots_by_key: dict[str, dict], now: datetime) -> list[tuple[str, list]]:
    """Favorites closing in the coming 7 days per pickup address; the address whose first lot closes first comes first."""
    groups: dict[str, list] = {}
    for f, c in closing_between(favs, lots_by_key, now, now + timedelta(days=7)):
        lot = lots_by_key.get(f["key"]) or {}
        groups.setdefault(_place(f, lot).lower() or "?", []).append((f, c, lot))
    return [(_place(items[0][0], items[0][2]), items) for _, items in sorted(groups.items(), key=lambda g: g[1][0][1])]


def _coords(place: str, geo: dict | None) -> tuple[float, float] | None:
    e = (geo or {}).get(place) if place else None
    if isinstance(e, dict) and isinstance(e.get("lat"), (int, float)) and isinstance(e.get("lon"), (int, float)):
        return e["lat"], e["lon"]
    return None


def map_points(favs: list[dict], lots_by_key: dict[str, dict], now: datetime, geo: dict | None) -> list[tuple[float, float]]:
    """Coordinates of the pickup addresses, in the order of their numbers in the mail."""
    return [xy for place, _ in _groups(favs, lots_by_key, now) if (xy := _coords(place, geo))]


def message(favs: list[dict], lots_by_key: dict[str, dict], problems: list[str], now: datetime,
            url: str | None = None, geo: dict | None = None, with_map: bool = False) -> str:
    """geo: the scan's address cache (address -> {"lat", "lon"}); addresses found there get a number that
    matches their pin on the map (with_map: the image is attached to the mail as cid:map)."""
    groups = _groups(favs, lots_by_key, now)
    n = sum(len(items) for _, items in groups)
    head = (f"🗓 <b>Weekoverzicht veilingbot: {n} favoriet{'en' if n != 1 else ''} "
            f"sluit{'en' if n != 1 else ''} deze week</b>")
    lines = [head, ""]
    if with_map:
        lines += ['<img src="cid:map" alt="Kaart met de ophaaladressen" width="640" '
                  'style="max-width:100%;height:auto;border-radius:6px">', ""]

    pin = 0
    for place, items in groups:
        f0, _, lot0 = items[0]
        trip = lot0.get("trip") or {}
        km = f" · {trip['km']:.0f} km" if isinstance(trip.get("km"), (int, float)) else ""
        count = f" · {len(items)} kavels" if len(items) > 1 else ""
        label = "📍"
        if _coords(place, geo):
            pin += 1
            label = f"📍 {pin}."
        if place:
            maps = "https://www.google.com/maps/search/?api=1&query=" + quote_plus(place)
            name = f'<a href="{attr(maps)}"><b>{esc(place)}</b></a>'
        else:
            name = "<b>Ophaaladres nog onbekend</b>"
        lines.append(f"{label} {name}{km}{count}")
        when = next((x[2].get("pickupWhen") or x[0].get("pickupWhen") for x in items
                     if x[2].get("pickupWhen") or x[0].get("pickupWhen")), None)
        lines.append(f"🚚 Ophalen: {esc(when)}" if when else "🚚 Ophalen: staat nog niet bij de veiling")
        for f, c, lot in items:
            max_bid = lot.get("maxBid", f.get("maxBid"))
            bits = [f"sluit {nl_time(c)}"]
            if max_bid is not None:
                bits.append(f"je max bod {fmt_eur(max_bid)}")
            lines.append(f'• <a href="{attr(f["url"])}">{esc(f["title"][:80])}</a>\n   ' + " · ".join(bits))
        lines.append("")
    if not groups:
        lines += ["Er sluiten deze week geen favorieten.", ""]

    if problems:
        lines.append(f"🩺 <b>Bot check: {len(problems)} ding{'en' if len(problems) != 1 else ''} werkt niet</b>")
        lines += [f"• {esc(p)}" for p in problems]
        lines.append("Vraag Claude in het project om ernaar te kijken.")
    else:
        lines.append("🩺 <b>Bot check:</b> alles werkt ✅")
    if url:
        lines += ["", f'📊 <a href="{attr(url)}">Dashboard</a>']
    return "\n".join(lines)
