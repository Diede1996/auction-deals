"""Telegram commands that edit the watchlist.

/add playstation 5 | ps5 -controller -game max=250
/remove 2            (or /remove playstation 5)
/list
/sellat 50
/minprofit 25
/scan                (handled in bot.py: starts an extra scan)
/status
/dashboard
/help
"""
from __future__ import annotations

import re

from .models import WatchItem
from .telegram import attr, esc
from .util import fmt_eur, normalize

HELP = """<b>Auction deal bot</b>
Every morning I check 4 auction sites for bankruptcy, business-closure and Domeinen lots that match your watchlist and send you a summary.

<b>/add</b> <i>item</i> – watch an item. Words after <b>-</b> are excluded, options go at the end:
<code>/add playstation 5 | ps5 -controller -game max=250</code>
  • <b>|</b> separates alternative search phrases
  • <b>max=</b> never suggest paying more than this in total (bid + premium + VAT)
  • <b>price=</b> your own resale value (skips the Marktplaats lookup)
  • <b>profit=</b> minimum profit for this item
  • <b>mp=</b> custom Marktplaats search, e.g. <code>mp="ps5 console"</code>

<b>/list</b> – show the watchlist
<b>/remove</b> <i>number or name</i> – stop watching an item
<b>/sellat</b> <i>percent</i> – what you expect to sell for, as % of the Marktplaats median, e.g. <code>/sellat 50</code>
<b>/minprofit</b> <i>amount</i> – the max bid always leaves at least this much profit, e.g. <code>/minprofit 25</code>
<b>/scan</b> – check the auction sites now instead of waiting for tomorrow (max 3 times a day)
<b>/dashboard</b> – link to your list of lots
<b>/status</b> – when the last scan ran and which sites worked
<b>/help</b> – this message"""

_OPT_RE = re.compile(r'\b(max|price|profit|mp)=("([^"]*)"|\S+)', re.I)


def parse_add(args: str) -> WatchItem | None:
    options = {}
    for m in _OPT_RE.finditer(args):
        options[m.group(1).lower()] = m.group(3) if m.group(3) is not None else m.group(2)
    rest = _OPT_RE.sub(" ", args)
    exclude = [w[1:] for w in rest.split() if w.startswith("-") and len(w) > 1]
    rest = " ".join(w for w in rest.split() if not w.startswith("-"))
    phrases = [p.strip() for p in re.split(r"[|,;]", rest) if p.strip()]
    if not phrases:
        return None

    def num(key):
        try:
            return float(str(options[key]).replace("€", "").replace(",", "."))
        except (KeyError, ValueError):
            return None

    return WatchItem(name=phrases[0], keywords=phrases, exclude=exclude, max_price=num("max"),
                     market_price=num("price"), min_profit=num("profit"),
                     marktplaats_query=options.get("mp"))


def describe(i: int, item: WatchItem) -> str:
    parts = [f"<b>{i}. {esc(item.name)}</b>"]
    if len(item.keywords) > 1:
        parts.append("   also: " + esc(", ".join(item.keywords[1:])))
    if item.exclude:
        parts.append("   excluding: " + esc(", ".join(item.exclude)))
    extra = []
    if item.max_price is not None:
        extra.append(f"max {fmt_eur(item.max_price)}")
    if item.market_price is not None:
        extra.append(f"resale {fmt_eur(item.market_price)}")
    if item.min_profit is not None:
        extra.append(f"min profit {fmt_eur(item.min_profit)}")
    if item.marktplaats_query:
        extra.append(f'Marktplaats: "{esc(item.marktplaats_query)}"')
    if extra:
        parts.append("   " + " · ".join(extra))
    return "\n".join(parts)


def _number(args: str) -> float | None:
    try:
        return float(args.replace("€", "").replace("%", "").replace(",", ".").strip())
    except ValueError:
        return None


def handle(text: str, watchlist: dict, status_text: str, dashboard_url: str | None = None) -> tuple[str, bool]:
    """Apply one command. Returns (reply, watchlist_changed)."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return ("Send /help to see what I can do.", False)
    cmd, _, args = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    args = args.strip()
    items = [WatchItem.from_dict(d) for d in watchlist.get("items") or []]
    settings = watchlist.get("settings") or {}
    next_scan = "It's included in tomorrow morning's scan, or send /scan to check now."

    if cmd in ("/start", "/help"):
        return (HELP, False)

    if cmd == "/list":
        if not items:
            return ("Your watchlist is empty. Add something with /add", False)
        sell = float(settings.get("resale_factor", 0.85))
        head = (f"<b>Watchlist</b> (max bids for selling at {sell:.0%} of the Marktplaats median with at least "
                f"{fmt_eur(float(settings.get('min_profit', 25)))} profit)\n\n")
        return (head + "\n".join(describe(i + 1, it) for i, it in enumerate(items)), False)

    if cmd == "/add":
        item = parse_add(args)
        if not item:
            return ("Tell me what to watch, e.g. <code>/add iphone 15 pro -hoesje max=450</code>", False)
        existing = next((i for i, it in enumerate(items) if normalize(it.name) == normalize(item.name)), None)
        if existing is not None:
            items[existing] = item
            verb = "Updated"
        else:
            items.append(item)
            verb = "Added"
        watchlist["items"] = [it.to_dict() for it in items]
        return (f"✅ {verb}:\n{describe(len(items) if existing is None else existing + 1, item)}\n\n{next_scan}", True)

    if cmd in ("/remove", "/delete", "/del"):
        if not args:
            return ("Which one? Send /list and then e.g. <code>/remove 2</code>", False)
        idx = int(args) - 1 if args.isdigit() else next(
            (i for i, it in enumerate(items) if normalize(it.name) == normalize(args)), None)
        if idx is None or not 0 <= idx < len(items):
            return (f"I couldn't find “{esc(args)}” in your watchlist. Send /list to see the numbers.", False)
        removed = items.pop(idx)
        watchlist["items"] = [it.to_dict() for it in items]
        return (f"🗑 Removed <b>{esc(removed.name)}</b>", True)

    if cmd in ("/sellat", "/sell"):
        value = _number(args)
        if value is None or not 5 <= value <= 200:
            return ("Send a percentage, e.g. <code>/sellat 50</code> if you expect to get half the Marktplaats median.", False)
        watchlist.setdefault("settings", {})["resale_factor"] = round(value / 100, 4)
        for old in ("target_return", "min_margin"):
            watchlist["settings"].pop(old, None)
        return (f"✅ Max bids and margins now assume you sell at <b>{value:g}% of the Marktplaats median</b>. "
                "The dashboard uses it from the next scan; you can also try other values there right away.", True)

    if cmd in ("/return", "/target"):
        return ("The target return setting is gone. Max bids now leave your minimum profit (/minprofit) when you "
                "sell at your chosen share of the median (/sellat).", False)

    if cmd == "/minprofit":
        value = _number(args)
        if value is None or value < 0:
            return ("Send an amount, e.g. <code>/minprofit 25</code>", False)
        watchlist.setdefault("settings", {})["min_profit"] = value
        return (f"✅ Minimum profit per lot is now {fmt_eur(value)}", True)

    if cmd == "/dashboard":
        if dashboard_url:
            return (f'📊 <a href="{attr(dashboard_url)}">Open the dashboard</a>', False)
        return ("The dashboard link isn't known yet; it appears after the first scan.", False)

    if cmd == "/status":
        return (status_text, False)

    return ("I don't know that command. Send /help", False)
