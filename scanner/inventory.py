"""Your stock: what you bought, what you ask for it, what it sold for, and what that earned per hour.

Telegram commands (handled in bot.py):

/koop dell u2719d monitor | 45 tijd=60     you bought it for €45 (all in) and spent 60 min collecting it
/verkocht 3 80 tijd=20                     #3 sold for €80; 20 min for the listing, messages and handover
/prijs 3 70                                your asking price for #3 on Marktplaats
/voorraad                                  what's still for sale
/winst                                     profit, days to sell and profit per hour, per category
/weg 3                                     remove #3 (a mistake, or you keep it yourself)

The list holds what you paid and earned, so it is never stored readable: the repository is public. It is
saved encrypted in data/inventory.enc with a key made from the INVENTORY_KEY secret, or the Telegram bot
token when that secret isn't set. Without the key the file is unreadable noise.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .matching import matches
from .models import WatchItem
from .telegram import attr, esc
from .util import AMS, fmt_eur

OTHER = "Overig"


class InventoryKeyError(Exception):
    """The saved stock list can't be opened with this key (the token or INVENTORY_KEY changed)."""


def _fernet(secret: str):
    from cryptography.fernet import Fernet
    digest = hashlib.sha256(("auction-deals inventory v1|" + secret).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def load(path: Path, secret: str) -> dict:
    if not path.exists():
        return {"items": [], "next": 1}
    from cryptography.fernet import InvalidToken
    try:
        data = json.loads(_fernet(secret).decrypt(path.read_bytes()))
    except (InvalidToken, ValueError) as e:
        raise InventoryKeyError(str(e)) from e
    data.setdefault("items", [])
    data.setdefault("next", max((i["id"] for i in data["items"]), default=0) + 1)
    return data


def save(path: Path, data: dict, secret: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_fernet(secret).encrypt(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()))


# ---------------------------------------------------------------- prices and the listing text

def nice_price(amount: float) -> int:
    """Asking prices people expect: €38 -> 40, €123 -> 125, €487 -> 490, €1.234 -> 1.250."""
    step = 5 if amount < 300 else 10 if amount < 1000 else 50
    return max(step, int(round(amount / step) * step))


def suggest(paid: float, median: float | None, resale_factor: float) -> tuple[int | None, int | None]:
    """(asking price, lowest price to accept). You ask about the Marktplaats median and expect to end up near
    resale_factor x the median (what the max bids assume); never below what you paid."""
    if not median:
        return None, None
    ask = nice_price(median)
    floor = max(paid, median * resale_factor)
    return ask, min(ask, int(round(floor)))


def listing_text(name: str, ask: int | None) -> str:
    """A Marktplaats listing to paste and finish: the [brackets] are for you to fill in or remove."""
    title = name.strip()[:60]
    price = f"Vraagprijs € {ask}" if ask else "Vraagprijs € [..]"
    return (f"<b>Titel</b>\n<code>{esc(title)}</code>\n\n<b>Beschrijving</b>\n<code>"
            + esc(f"{title}\n\n"
                  "Staat: [nette staat / lichte gebruikssporen / nieuw in doos]\n"
                  "Werkt goed, getest op [datum].\n"
                  "Inclusief: [kabels, lader, doos]\n\n"
                  f"{price}. Redelijke biedingen worden bekeken.\n"
                  "Ophalen in [plaats], verzenden kan in overleg (kosten voor koper).")
            + "</code>")


# ---------------------------------------------------------------- commands

_OPT = re.compile(r"\b(tijd|time|min)=(\d+(?:[.,]\d+)?)\s*(u|uur|h|m|min)?\b", re.I)
_SOLD = re.compile(r"^#?(\d+)\s+(€?\s*[\d.,]+)")


def _minutes(text: str) -> tuple[str, float | None]:
    """("rest of the text", minutes) from "tijd=45", "tijd=1,5u", "tijd=2h"."""
    m = _OPT.search(text)
    if not m:
        return text, None
    value = float(m.group(2).replace(",", "."))
    if (m.group(3) or "").lower() in ("u", "uur", "h"):
        value *= 60
    return (text[:m.start()] + text[m.end():]).strip(), value


def _money(text: str) -> float | None:
    """"45", "€ 45,50", "1.250" or "1.250,00" -> a number."""
    s = text.replace("€", "").strip()
    if "," in s or re.fullmatch(r"\d{1,3}(\.\d{3})+", s):  # Dutch notation: dots group thousands
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def category(name: str, items: list[WatchItem]) -> str:
    """The watchlist item a thing belongs to ("Monitor"), or "Overig"."""
    return next((it.name for it in items if matches(it, name)), OTHER)


def _find(inv: dict, number: str) -> dict | None:
    number = number.lstrip("#")
    return next((i for i in inv["items"] if number.isdigit() and i["id"] == int(number)), None)


def _days(since: str, now: datetime) -> int:
    return max(0, (now - datetime.fromisoformat(since)).days)


def _line(it: dict, now: datetime) -> str:
    ask = f" · vraagprijs {fmt_eur(it['ask'])}" if it.get("ask") else " · nog geen vraagprijs"
    return (f"<b>#{it['id']}</b> {esc(it['name'][:60])} · betaald {fmt_eur(it['paid'])}{ask} · "
            f"{_days(it['bought'], now)} dagen")


@dataclass
class Context:
    items: list[WatchItem]
    resale_factor: float
    now: datetime
    price_of: object = None  # (name, category item) -> PriceEstimate | None, a Marktplaats lookup


HELP = """<b>Voorraad en verkoop</b>
<b>/koop</b> <i>naam</i> | <i>betaald</i> – iets wat je gekocht hebt (betaald = alles bij elkaar: bod, opgeld, btw), bijv. <code>/koop Dell U2719D monitor | 45 tijd=60</code>. Je krijgt een advertentietekst en een vraagprijs. <b>tijd=</b> is wat het ophalen kostte (minuten, of <code>1,5u</code>). Een hele veiling tegelijk? Zet elke aankoop op een eigen regel onder <code>/koop</code>.
<b>/verkocht</b> <i>nummer prijs</i> – bijv. <code>/verkocht 3 80 tijd=20</code> (tijd = advertentie, berichten, overdracht)
<b>/prijs</b> <i>nummer prijs</i> – je vraagprijs aanpassen
<b>/voorraad</b> – wat je nog te koop hebt
<b>/winst</b> – winst, verkooptijd en winst per uur per soort
<b>/weg</b> <i>nummer</i> – iets van de lijst halen
<i>Ik stuur een seintje als iets na 10 dagen niet verkocht is. Deze lijst is versleuteld opgeslagen; niemand anders kan hem lezen.</i>"""

COMMANDS = {"/koop", "/gekocht", "/verkocht", "/prijs", "/voorraad", "/winst", "/weg", "/voorraadhelp"}


def _parse_buy(args: str) -> tuple[str, float, int | None] | None:
    """"naam | betaald [tijd=..]" -> (name, paid, minutes), or None when it doesn't read like that."""
    rest, minutes = _minutes(args)
    name, _, paid_text = rest.rpartition("|")
    paid = _money(paid_text) if name.strip() else None
    return (name.strip(), paid, minutes) if paid is not None else None


def _add(inv: dict, name: str, paid: float, minutes: int | None, ctx: Context) -> tuple[dict, object]:
    cat = category(name, ctx.items)
    est = None
    if ctx.price_of:
        try:
            est = ctx.price_of(name, next((it for it in ctx.items if it.name == cat), None))
        except Exception:
            est = None
    median = est.median if est else None
    ask, floor = suggest(paid, median, ctx.resale_factor)
    now = ctx.now.isoformat()
    it = {"id": inv["next"], "name": name, "paid": round(paid, 2), "bought": now, "category": cat,
          "minutes": minutes or 0, "median": median, "ask": ask, "floor": floor, "listed": now}
    inv["next"] += 1
    inv["items"].append(it)
    return it, est


def _buy_many(args: str, inv: dict, ctx: Context) -> tuple[str, bool]:
    """A whole auction at once: one "naam | betaald" per line (a leading /koop on a line is fine)."""
    added, skipped = [], []
    for raw in args.splitlines():
        line = re.sub(r"^/(koop|gekocht)(@\S+)?\s*", "", raw.strip(), flags=re.I)
        if not line:
            continue
        parsed = _parse_buy(line)
        if not parsed:
            skipped.append(line)
            continue
        added.append(_add(inv, *parsed, ctx)[0])
    if not added:
        return ("Ik kon geen regel lezen. Eén aankoop per regel: <code>naam | betaald</code>.", False)
    lines = [f"✅ <b>{len(added)} aankopen</b> toegevoegd, samen {fmt_eur(sum(i['paid'] for i in added))}:"]
    for it in added:
        price = (f"vraag {fmt_eur(it['ask'])}, niet onder {fmt_eur(it['floor'])}" if it["ask"]
                 else f"geen Marktplaats-prijs, zet er een met <code>/prijs {it['id']} 80</code>")
        lines.append(f"#{it['id']} {esc(it['name'][:60])} · {fmt_eur(it['paid'])} · {price}")
    if skipped:
        lines.append("\n⚠️ Niet gelezen (zet er <code>| betaald</code> achter): " + "; ".join(esc(s[:40]) for s in skipped))
    lines.append("\n<i>Verkocht? Stuur <code>/verkocht nummer prijs</code>.</i>")
    return "\n".join(lines), True


def handle(text: str, inv: dict, ctx: Context) -> tuple[str, bool]:
    """Apply one stock command. Returns (reply, inventory changed)."""
    cmd, args = (text.strip().split(None, 1) + [""])[:2]
    cmd = cmd.split("@")[0].lower()
    args = args.strip()
    now = ctx.now

    if cmd == "/voorraadhelp":
        return HELP, False

    if cmd in ("/koop", "/gekocht") and "\n" in args:
        return _buy_many(args, inv, ctx)

    if cmd in ("/koop", "/gekocht"):
        parsed = _parse_buy(args)
        if not parsed:
            return ("Zo werkt het: <code>/koop Dell U2719D monitor | 45</code> (naam | wat je in totaal betaalde). "
                    "Optioneel <code>tijd=60</code> voor het ophalen.", False)
        name, paid, minutes = parsed
        it, est = _add(inv, name, paid, minutes, ctx)
        median, ask, floor, cat = it["median"], it["ask"], it["floor"], it["category"]
        lines = [f"✅ <b>#{it['id']}</b> {esc(name)} · betaald {fmt_eur(paid)} · soort: {esc(cat)}"]
        if est:
            lines.append(f"Marktplaats: mediaan {fmt_eur(median)} ({est.count} advertenties, "
                         f'<a href="{attr(est.url)}">bekijk</a>)')
            lines.append(f"💶 Vraag <b>{fmt_eur(ask)}</b>, ga niet onder <b>{fmt_eur(floor)}</b>"
                         + (f" (verwachte winst {fmt_eur(floor - paid)})" if floor > paid else ""))
        else:
            lines.append("Geen vergelijkbare advertenties gevonden op Marktplaats. Zet zelf een vraagprijs met "
                         f"<code>/prijs {it['id']} 80</code>.")
        lines.append("\n" + listing_text(name, ask))
        lines.append(f"\n<i>Verkocht? Stuur <code>/verkocht {it['id']} prijs</code>.</i>")
        return "\n".join(lines), True

    if cmd == "/prijs":
        parts = args.split()
        it = _find(inv, parts[0]) if parts else None
        price = _money(parts[1]) if len(parts) > 1 else None
        if not it or price is None:
            return "Zo werkt het: <code>/prijs 3 70</code> (nummer uit /voorraad, nieuwe vraagprijs).", False
        it["ask"], it["listed"] = round(price), now.isoformat()
        it.pop("nudged", None)
        return f"✅ #{it['id']} {esc(it['name'][:60])}: vraagprijs nu {fmt_eur(price)}.", True

    if cmd == "/verkocht":
        rest, minutes = _minutes(args)
        m = _SOLD.match(rest)
        it = _find(inv, m.group(1)) if m else None
        if not it:
            return "Zo werkt het: <code>/verkocht 3 80</code> (nummer uit /voorraad, verkoopprijs). Optioneel <code>tijd=20</code>.", False
        price = _money(m.group(2))
        if price is None:
            return "Zo werkt het: <code>/verkocht 3 80</code> (nummer uit /voorraad, verkoopprijs).", False
        it["sold"], it["sold_at"] = round(price, 2), now.isoformat()
        it["minutes"] = (it.get("minutes") or 0) + (minutes or 0)
        profit = price - it["paid"]
        days = _days(it["bought"], now)
        line = f"🎉 #{it['id']} {esc(it['name'][:60])} verkocht voor {fmt_eur(price)}: winst <b>{fmt_eur(profit)}</b> in {days} dagen"
        if it["minutes"]:
            line += f", {fmt_eur(profit / (it['minutes'] / 60))} per uur"
        if not minutes:
            line += ("\n<i>Hoeveel tijd kostte het (ophalen, advertentie, berichten)? Stuur het de volgende keer mee, "
                     "bijv. <code>tijd=30</code>: dan zie je in /winst wat je per uur verdient.</i>")
        return line + ".", True

    if cmd == "/weg":
        it = _find(inv, args.split()[0]) if args else None
        if not it:
            return "Welke? Stuur <code>/weg 3</code> met het nummer uit /voorraad.", False
        inv["items"].remove(it)
        return f"🗑 #{it['id']} {esc(it['name'][:60])} van de lijst gehaald.", True

    if cmd == "/voorraad":
        open_items = [i for i in inv["items"] if i.get("sold") is None]
        if not open_items:
            return "Je hebt niets te koop staan. Iets gekocht? <code>/koop naam | prijs</code>", False
        total = sum(i["paid"] for i in open_items)
        return (f"<b>Te koop</b> ({len(open_items)} dingen, {fmt_eur(total)} aan inkoop)\n"
                + "\n".join(_line(i, now) for i in sorted(open_items, key=lambda i: i["bought"]))), False

    if cmd == "/winst":
        return profit_report(inv, ctx.resale_factor, now), False

    return HELP, False


# ---------------------------------------------------------------- what it earned

def profit_report(inv: dict, resale_factor: float, now: datetime) -> str:
    sold = [i for i in inv["items"] if i.get("sold") is not None]
    if not sold:
        return "Nog niets verkocht. Stuur <code>/verkocht nummer prijs</code> als iets weg is."
    groups: dict[str, list[dict]] = {}
    for i in sold:
        groups.setdefault(i.get("category") or OTHER, []).append(i)

    def summary(name: str, rows: list[dict]) -> str:
        profit = sum(r["sold"] - r["paid"] for r in rows)
        paid = sum(r["paid"] for r in rows)
        days = statistics.median(_days(r["bought"], datetime.fromisoformat(r["sold_at"])) for r in rows)
        timed = [r for r in rows if r.get("minutes")]
        hour = ""
        if timed:
            per_hour = sum(r["sold"] - r["paid"] for r in timed) / (sum(r["minutes"] for r in timed) / 60)
            hour = f" · <b>{fmt_eur(per_hour)}/uur</b>" + (f" ({len(timed)} van {len(rows)} met tijd)" if len(timed) < len(rows) else "")
        margin = f" ({profit / paid:.0%})" if paid else ""
        return (f"<b>{esc(name)}</b>: {len(rows)} verkocht · winst {fmt_eur(profit)}{margin} · "
                f"{days:g} dagen tot verkoop{hour}")

    lines = ["💰 <b>Wat je verkopen opleverden</b>"]
    for name, rows in sorted(groups.items(), key=lambda g: -sum(r["sold"] - r["paid"] for r in g[1])):
        lines.append(summary(name, rows))
    if len(groups) > 1:
        lines.append(summary("Alles", sold))

    priced = [i for i in sold if i.get("median")]
    if priced:
        share = statistics.median(i["sold"] / i["median"] for i in priced)
        lines.append(f"\n📊 Je verkocht voor gemiddeld <b>{share:.0%} van de Marktplaats-mediaan</b> "
                     f"({len(priced)} verkopen); de max biedingen rekenen met {resale_factor:.0%}.")
        if len(priced) >= 5 and abs(share - resale_factor) >= 0.05:
            lines.append(f"<i>Wil je dat de bot daarmee rekent? Stuur <code>/sellat {round(share * 100)}</code>.</i>")
        elif len(priced) < 5:
            lines.append("<i>Na 5 verkopen met een Marktplaats-prijs zeg ik of je /sellat zou moeten aanpassen.</i>")
    unsold = [i for i in inv["items"] if i.get("sold") is None]
    if unsold:
        lines.append(f"\n📦 Nog te koop: {len(unsold)} dingen, {fmt_eur(sum(i['paid'] for i in unsold))} aan inkoop.")
    return "\n".join(lines)


# ---------------------------------------------------------------- lower the price of what doesn't sell

def due_nudges(inv: dict, now: datetime, after_days: int = 10, every_days: int = 7, drop: float = 0.1) -> list[dict]:
    """Unsold things whose asking price hasn't changed for after_days, at most one nudge per every_days.
    Each gets "next_ask": the asking price lowered by `drop`, never below the lowest price you'd accept."""
    out = []
    for it in inv["items"]:
        if it.get("sold") is not None or not it.get("ask"):
            continue
        if _days(it.get("listed") or it["bought"], now) < after_days:
            continue
        if it.get("nudged") and _days(it["nudged"], now) < every_days:
            continue
        floor = it.get("floor") or it["paid"]
        nxt = max(floor, nice_price(it["ask"] * (1 - drop)))
        out.append({**it, "next_ask": nxt if nxt < it["ask"] else None})
    return out


def nudge_text(due: list[dict], now: datetime) -> str:
    lines = ["🏷 <b>Nog niet verkocht</b>"]
    for it in due:
        days = _days(it.get("listed") or it["bought"], now)
        if it["next_ask"]:
            lines.append(f"#{it['id']} {esc(it['name'][:60])}: {days} dagen op {fmt_eur(it['ask'])}. Probeer "
                         f"<b>{fmt_eur(it['next_ask'])}</b>: <code>/prijs {it['id']} {it['next_ask']}</code>")
        else:
            lines.append(f"#{it['id']} {esc(it['name'][:60])}: {days} dagen op {fmt_eur(it['ask'])}, al op je "
                         "bodemprijs. Nieuwe foto's of een andere titel helpen vaak meer dan zakken.")
    lines.append("<i>Al verkocht? <code>/verkocht nummer prijs</code></i>")
    return "\n".join(lines)



