"""The daily scan: scrape the auction sites, price the matches, build the dashboard, send a digest."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from . import dashboard
from .evaluate import Fees, Settings, Verdict, evaluate, lot_rates, market_value
from .favorites import FavoritesStore, closing_between
from .geo import DrivingCosts, TripPlanner, fuel_price
from .http import Http
from .bidding import fill_next_bids, parse_steps
from .details import fill_descriptions, fill_start_prices
from .identify import brand_in, mac_plan, model_code, phone_plan, plan_for, quantity, useful_tokens
from .mail_alerts import Mailbox, collect as collect_alert_lots
from .marktplaats import PriceEstimate, search_url
from .age import age_filter, too_old
from .condition import defect, defect_filter
from .matching import auction_filter, match_lots, search_terms
from .models import Lot, WatchItem
from .pricing import PriceFinder
from .sites import SITE_NAMES, SITES
from .sites.base import SiteContext
from .storage import dashboard_url, load_json, load_yaml, save_json
from .telegram import Telegram, attr, esc
from .util import AMS, fmt_eur, normalize, phrase_in

log = logging.getLogger("scanner")

Row = tuple[WatchItem, Lot, Verdict, "PriceEstimate | None"]


def units_of(lot: Lot, max_units: int = 0) -> int:
    """How many items the resale value counts: "40x Colbert" -> 40, so price, margin and max bid are for
    the whole lot. `max_units` (0 = no limit) makes bigger bulk lots count as one item instead."""
    n = quantity(lot.title)
    return n if n >= 2 and (max_units <= 0 or n <= max_units) else 1


# ---------------------------------------------------------------- scraping

def closes_by(lot: Lot, later: datetime) -> datetime:
    """When a lot closes, so the lots closing first get the Marktplaats searches first. A lot with only a
    closing day (Troostwijk emails) counts as closing at the end of that day."""
    if lot.closes_at:
        return lot.closes_at
    if lot.closes_day:
        day = date.fromisoformat(lot.closes_day)
        return datetime(day.year, day.month, day.day, 23, 59, tzinfo=AMS)
    return later


@contextmanager
def quiet_logs(*names: str):
    """No log lines from these modules for a while: the GitHub Actions log is public, and a failed search
    would otherwise name the lot you follow."""
    loggers = [logging.getLogger(n) for n in names]
    before = [lg.disabled for lg in loggers]
    for lg in loggers:
        lg.disabled = True
    try:
        yield
    finally:
        for lg, was in zip(loggers, before):
            lg.disabled = was


PRIVATE_LOGS = ("scanner.http", "scanner.pricing", "scanner.marktplaats", "scanner.geo")


def followed_item(title: str) -> WatchItem | None:
    """A watchlist item for a lot you follow on Troostwijk that isn't on your watchlist: its brand, or else its
    first real word, so the Marktplaats search is built from the title like for any other lot."""
    word = brand_in(title) or next((t for _, t in useful_tokens(title) if t.isalpha() and len(t) >= 3), None)
    return WatchItem(name="Troostwijk favourite", keywords=[word]) if word else None


def scan_sites(config: dict, items: list[WatchItem], state: dict, http_factory, now: datetime,
               only: list[str] | None = None) -> tuple[list[Lot], dict, int]:
    """Scrape all enabled sites in parallel (one HTTP client per site). Returns (lots, report, requests)."""
    is_bankruptcy = auction_filter(config)
    terms = search_terms(items)
    health = state.setdefault("health", {})
    site_cache = state.setdefault("site_cache", {})
    # sites remember per auction whether it passed the filter: start over when the filter words change
    words = json.dumps([config.get("auction_keywords"), config.get("extra_auctions")], sort_keys=True, default=str)
    fingerprint = hashlib.sha256(words.encode()).hexdigest()[:12]
    if state.get("filter_words") != fingerprint:
        for cache in site_cache.values():
            if isinstance(cache, dict):
                cache.pop("bankrupt", None)
        state["filter_words"] = fingerprint
    jobs = {}
    for site, fetch in SITES.items():
        settings = (config.get("sites") or {}).get(site) or {}
        if settings.get("enabled", True) is False or (only and site not in only):
            continue
        ctx = SiteContext(http=http_factory(), now=now, is_bankruptcy=is_bankruptcy, search_terms=terms,
                          settings=settings, cache=site_cache.setdefault(site, {}))
        jobs[site] = (fetch, ctx)

    lots: list[Lot] = []
    report = {}
    with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
        futures = {site: pool.submit(fetch, ctx) for site, (fetch, ctx) in jobs.items()}
    for site, fut in futures.items():
        h = health.setdefault(site, {})
        err = fut.exception()
        if err is not None:
            log.error("%s failed: %s: %s", site, type(err).__name__, err)
            h.update(ok=False, error=f"{type(err).__name__}: {err}"[:300], fails=h.get("fails", 0) + 1,
                     at=now.isoformat())
            report[site] = {"ok": False, "error": h["error"]}
            continue
        found = fut.result()
        h.update(ok=True, lots=len(found), fails=0, error="", at=now.isoformat(), last_ok=now.isoformat())
        report[site] = {"ok": True, "lots": len(found)}
        log.info("%s: %d lots", site, len(found))
        lots.extend(found)
    requests = sum(getattr(ctx.http, "request_count", 0) for _, ctx in jobs.values())
    return lots, report, requests


# ---------------------------------------------------------------- telegram

def health_messages(state: dict, warn_after: int = 2) -> list[str]:
    out = []
    for site, h in (state.get("health") or {}).items():
        name = SITE_NAMES.get(site, site)
        if not h.get("ok") and h.get("fails", 0) >= warn_after and not h.get("warned"):
            h["warned"] = True
            out.append(f"⚠️ <b>{name}</b> has failed {h['fails']} scans in a row, so lots there are missing.\n"
                       f"<i>{esc(h.get('error', '')[:200])}</i>\n" +
                       ("Check the ALERTS_EMAIL and ALERTS_APP_PASSWORD secrets on GitHub."
                        if h.get("error", "").startswith("alerts mailbox") else
                        "The site may have changed or be blocking automated requests."))
        elif h.get("ok") and h.get("warned"):
            h["warned"] = False
            out.append(f"✅ <b>{name}</b> works again.")
    return out


def _line(item: WatchItem, lot: Lot, v: Verdict, est: PriceEstimate | None = None,
          trips: dict | None = None) -> str:
    local = lot.closes_at.astimezone(AMS) if lot.closes_at else None
    when = local.strftime("%a %H:%M") if local else (closing_day(lot) or "?")
    margin = (f" (margin {fmt_eur(v.profit_at_max)} · {v.margin_at_max:.0%})"
              if v.profit_at_max is not None and v.margin_at_max is not None else "")
    rough = "≈" if est is not None and est.kind == "general" and item.market_price is None else ""
    trip = (trips or {}).get(pickup_place(lot) or "")
    drive = f" · 🚗 {trip.km:.0f} km" + (f" from {trip.origin}" if trip.origin and trip.origin != "home" else "") if trip else ""
    if lot.transport:
        drive = f" · 🚚 transport ≈ {fmt_eur(lot.trip_cost)}"
    if lot.bid_from_email:  # the current bid isn't known, only the max bid
        return (f'• <a href="{attr(lot.url)}">{esc(lot.title[:70])}</a>\n'
                f"   bid up to <b>{rough}{fmt_eur(v.max_bid)}</b>{margin} · Troostwijk · closes {when}{drive}")
    return (f'• <a href="{attr(lot.url)}">{esc(lot.title[:70])}</a>\n'
            f"   next bid {fmt_eur(v.bid)} → max <b>{rough}{fmt_eur(v.max_bid)}</b>{margin} · "
            f"{SITE_NAMES.get(lot.site, lot.site)} · {when}{drive}")


def _heart_line(item: WatchItem, lot: Lot, v: Verdict, est: PriceEstimate | None = None,
                trips: dict | None = None) -> str:
    if v.max_bid is None:
        local = lot.closes_at.astimezone(AMS) if lot.closes_at else None
        when = local.strftime("%a %H:%M") if local else (closing_day(lot) or "?")
        return f'• <a href="{attr(lot.url)}">{esc(lot.title[:70])}</a>\n   no Marktplaats price found · closes {when}'
    return _line(item, lot, v, est, trips)


def favorites_message(favs: list[dict], rows: list[Row], now: datetime, verb: str = "today") -> str | None:
    """⭐ section: favorites that close before midnight (Dutch time)."""
    lots_by_key = {lot.key: {"closes": lot.closes_at.isoformat() if lot.closes_at else None} for _, lot, _, _ in rows}
    local = now.astimezone(AMS)
    midnight = local.replace(hour=23, minute=59, second=59)
    today = closing_between(favs, lots_by_key, now, midnight)
    if not today:
        return None
    by_key = {lot.key: (lot, v) for _, lot, v, _ in rows}
    lines = [f"⭐ <b>Your favorites closing {verb}</b>"]
    for f, c in today:
        lot, v = by_key.get(f["key"], (None, None))
        bid = f"next bid {fmt_eur(v.bid)} · " if v else ""
        maxbid = f"your max <b>{fmt_eur(v.max_bid)}</b> · " if v and v.max_bid is not None else (
            f"your max <b>{fmt_eur(f['maxBid'])}</b> · " if f.get("maxBid") is not None else "")
        lines.append(f'• <a href="{attr(f["url"])}">{esc(f["title"][:70])}</a>\n'
                     f"   {bid}{maxbid}closes {c.astimezone(AMS):%H:%M}")
    lines.append("<i>I'll remind you again about an hour before each one closes.</i>")
    return "\n".join(lines)


def digest_message(rows: list[Row], new_keys: set[str], settings: Settings, now: datetime, url: str | None,
                   per_section: int = 8, notes: list[str] | None = None, favs: list[dict] | None = None,
                   trips: dict | None = None, kinds: str = "bankruptcy, closure, estate or Domeinen",
                   far: dict | None = None, far_rules: list | None = None, hearts: list[Row] | None = None,
                   heart_trips: dict | None = None) -> str:
    far = far or {}
    deals = [r for r in rows if r[2].is_deal and r[1].key not in far]
    day = now.astimezone(AMS).strftime("%a %d %b")
    head = [f"☀️ <b>Auction scan</b> · {day}",
            f"{len(rows)} matching lots · <b>{len(deals)} with room to bid</b> · "
            f"{sum(1 for r in rows if r[1].key in new_keys)} new",
            f"<i>Max bids for selling at {settings.resale_factor:.0%} of the Marktplaats median "
            f"with at least {settings.min_margin:.0%} margin</i>"]
    parts = ["\n".join(head)] + list(notes or [])
    if hearts:  # lots with a heart on Troostwijk that close soon (Troostwijk emails a reminder about them)
        parts.append("❤️ <b>Lots you follow on Troostwijk</b> · <i>check the current bid on the lot page</i>\n" +
                     "\n".join(_heart_line(i, l, v, e, {**(heart_trips or {}), **(trips or {})})
                               for i, l, v, e in hearts[:per_section]))
    fav_part = favorites_message(favs or [], rows, now)
    if fav_part:
        parts.append(fav_part)
    soon = [r for r in deals if r[1].closes_at and r[1].closes_at - now <= timedelta(hours=24)]
    fresh = [r for r in deals if r[1].key in new_keys and r not in soon]
    if soon:
        parts.append("⏰ <b>Closing within 24 hours</b>\n" +
                     "\n".join(_line(i, l, v, e, trips) for i, l, v, e in soon[:per_section]))
    if fresh:
        fresh.sort(key=lambda r: -(r[2].max_bid or 0) + (r[2].bid or 0))
        parts.append("🆕 <b>New with room to bid</b>\n" +
                     "\n".join(_line(i, l, v, e, trips) for i, l, v, e in fresh[:per_section]))
    check = [r for r in rows if r[1].bid_from_email and r[1].key in new_keys and (r[2].max_bid or 0) > 0
             and r[1].key not in far]
    if check:
        check.sort(key=lambda r: -(r[2].max_bid or 0))
        parts.append("🔎 <b>New from Troostwijk emails</b> · <i>check the current bid on the lot page</i>\n" +
                     "\n".join(_line(i, l, v, e, trips) for i, l, v, e in check[:per_section]))
    if not rows:
        parts.append(f"Nothing {'else ' if hearts else ''}on your watchlist is in a running {kinds} auction today.")
    elif not soon and not fresh and not check:
        parts.append("Nothing new or closing soon with room to bid.")
    over = sum(1 for r in rows if r[2].is_deal and r[1].key in far and far[r[1].key][2] is None)
    few = sum(1 for r in rows if r[2].is_deal and r[1].key in far and far[r[1].key][2] is not None)
    if far_rules and (few or over):
        why = []
        if over:
            why.append(f"{over} more than {duration(far_rules[-1][0])} drive away")
        if few:
            why.append(f"{few} where too few lots are worth that drive")
        parts.append(f"<i>🚗 Lots with room to bid left out: {'; '.join(why)}.</i>")
    if any(e is not None and e.kind == "general" for _, _, v, e in soon + fresh + check[:per_section]):
        parts.append("<i>≈ rough price: no type number in the lot title, compared with similar items.</i>")
    if url:
        parts.append(f'📊 <a href="{attr(url)}">Open the dashboard</a>')
    return "\n\n".join(parts)


# ---------------------------------------------------------------- dashboard data

def duration(minutes: float) -> str:
    """90 -> "1h 30m", 45 -> "45 min"."""
    m = round(minutes)
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m} min"


def auction_kinds(config: dict) -> str:
    """"bankruptcy, closure, estate, Domeinen or IT": which auctions the bot reads, for texts."""
    extra = config.get("extra_auctions") or {}
    it = extra.get("enabled", True) is not False and bool(extra.get("words"))
    return "bankruptcy, closure, estate, Domeinen or IT" if it else "bankruptcy, closure, estate or Domeinen"


def closing_day(lot: Lot) -> str | None:
    """"Wed 7 Oct" for lots whose closing time isn't known, only the day."""
    if not lot.closes_day:
        return None
    day = datetime.fromisoformat(lot.closes_day)
    return f"{day:%a} {day.day} {day:%b}"


def pickup_day(lot: Lot) -> str:
    """"Mon 19 Oct" from "Mon 19 Oct, 13:00–15:30": lots collected on the same day share one trip."""
    return (lot.pickup_when or "").split(",")[0].strip()


def worth_collecting(row: Row, favorite_keys: set[str]) -> bool:
    """Room to bid, a Troostwijk lot whose current bid still needs checking, or a favorite."""
    _, lot, v, _ = row
    return v.is_deal or lot.key in favorite_keys or (lot.bid_from_email and (v.max_bid or 0) > 0)


def share_trips(rows: list[Row], trips: dict, favorite_keys: set[str], reevaluate, cost_of=None) -> list[Row]:
    """Lots collected at the same address on the same day share one trip: each lot carries the trip cost
    divided by the lots worth collecting there (counting itself).
    Which lots are worth it depends on their share, so this starts as if every lot there is collected and
    then drops the ones that still have no room to bid, until nothing changes. That finds the largest set
    of lots that are worth it together: three lots that each can't pay for the trip alone can together.
    reevaluate(item, lot, estimate) -> Verdict with the lot's new trip_cost.
    cost_of(place, n) -> what the trip costs for n lots (a transporter charges per lot); default: the fuel."""
    cost_of = cost_of or (lambda place, n: trips[place].cost)
    groups: dict[tuple[str, str], list[int]] = {}
    for i, (_, lot, _, _) in enumerate(rows):
        place = pickup_place(lot)
        if trips.get(place or ""):
            groups.setdefault((place, pickup_day(lot)), []).append(i)
    rows = list(rows)

    def apply(i: int, n: int, cost: float) -> bool:
        item, lot, _, est = rows[i]
        share = round(cost / n, 2)
        if lot.trip_cost == share and lot.trip_lots == n:
            return False
        lot.trip_cost, lot.trip_lots = share, n
        rows[i] = (item, lot, reevaluate(item, lot, est), est)
        return True

    for (place, _), members in groups.items():
        for i in members:  # optimistic start: everything here is collected
            apply(i, len(members), cost_of(place, len(members)))
        for _ in range(len(members) + 1):  # the set of lots worth it only shrinks from here
            worth = {i for i in members if worth_collecting(rows[i], favorite_keys)}
            changed = False
            for i in members:
                n = len(worth - {i}) + 1
                changed |= apply(i, n, cost_of(place, n))
            if not changed:
                break
    return rows


def trip_rules(drv_cfg: dict) -> list[tuple[float, int]]:
    """[(up to this many minutes one way, lots worth collecting needed), ...], shortest first; further than
    the last one: never. From driving.trip_rules in config.yml, or the older long_trip_* settings."""
    rules = []
    for rule in drv_cfg.get("trip_rules") or []:
        try:
            rules.append((float(rule[0]), max(1, int(rule[1]))))
        except (TypeError, ValueError, IndexError):
            continue
    if not rules and drv_cfg.get("long_trip_minutes"):
        rules = [(float(drv_cfg["long_trip_minutes"]), 1),
                 (float(drv_cfg.get("max_minutes") or 10 ** 6), int(drv_cfg.get("long_trip_min_lots", 3) or 1))]
    return sorted(rules)


def transport_settings(drv_cfg: dict) -> dict | None:
    """{"first": €, "extra": €} for pickups too far to drive (driving.transport), or None when it's off."""
    t = drv_cfg.get("transport") or {}
    if t.get("enabled", True) is False or not t:
        return None
    try:
        return {"first": float(t.get("first_lot", 75)), "extra": float(t.get("extra_lot", 25))}
    except (TypeError, ValueError):
        return None


def transport_cost(transport: dict, lots: int) -> float:
    """A transporter's estimate for collecting `lots` lots at one address: the first lot plus each extra one."""
    return transport["first"] + transport["extra"] * max(0, lots - 1)


def lots_needed(minutes: float, rules: list[tuple[float, int]]) -> int | None:
    """How many lots worth collecting make a trip of this many minutes worth it; None: too far, always."""
    if not rules:
        return 1
    for up_to, lots in rules:
        if minutes <= up_to:
            return lots
    return None


def too_far(rows: list[Row], trips: dict, favorite_keys: set[str], rules: list[tuple[float, int]],
            transport: bool = False) -> dict:
    """Lots you won't drive for: the pickup is further than the last rule allows, or too few lots at that
    address and pickup day are worth collecting (room to bid, a Troostwijk lot to check, or a favorite) for
    the time it takes. Returns lot key -> (minutes, lots worth collecting there, lots needed or None when
    it's always too far). Favorites are never left out."""
    if not rules:
        return {}
    groups: dict[tuple[str, str], list[tuple[Lot, Verdict]]] = {}
    for _, lot, v, _ in rows:
        place = pickup_place(lot)
        if trips.get(place or ""):
            groups.setdefault((place, pickup_day(lot)), []).append((lot, v))
    out = {}
    for (place, _), members in groups.items():
        minutes = trips[place].minutes
        need = lots_needed(minutes, rules)
        if need is None and transport:  # too far to drive, but a transporter can bring it
            continue
        worth = sum(1 for lot, v in members if worth_collecting((None, lot, v, None), favorite_keys))
        if need is None or worth < need:
            for lot, _ in members:
                if lot.key not in favorite_keys:
                    out[lot.key] = (minutes, worth, need)
    return out


def pickup_place(lot: Lot) -> str | None:
    """Where you collect the lot: the pickup address, or at least the town."""
    if lot.delivery:
        return None
    return lot.pickup or lot.location


def dashboard_data(rows: list[Row], report: dict, config: dict, settings: Settings, site_fees: dict[str, Fees],
                   new_keys: set[str], seen: dict, now: datetime, items: list[WatchItem] | None = None,
                   trips: dict | None = None, driving: dict | None = None, favorites: dict | None = None,
                   max_units: int = 0) -> dict:
    items = items or []
    trips = trips or {}
    lots = []
    for item, lot, v, est in rows:
        fees = site_fees.get(lot.site, Fees())
        plan = plan_for(item, lot)
        units = units_of(lot, max_units)
        place = pickup_place(lot)
        trip = trips.get(place) if place else None
        lots.append({
            "key": lot.key, "item": item.name, "title": lot.title, "url": lot.url,
            "site": lot.site, "siteName": SITE_NAMES.get(lot.site, lot.site), "auction": lot.auction_title,
            "image": lot.image, "location": lot.location,
            "pickup": lot.pickup, "pickupWhen": lot.pickup_when, "delivery": lot.delivery,
            "trip": trip.as_dict() if trip else None, "transport": lot.transport,
            "closes": lot.closes_at.isoformat() if lot.closes_at else None, "closesDay": lot.closes_day,
            "bidFromEmail": lot.bid_from_email,
            "bid": lot.current_bid if lot.current_bid is not None else v.bid, "bids": lot.bids,
            "nextBid": lot.next_bid, "stepEstimated": lot.step_estimated,
            "premium": lot_rates(fees, lot)[0], "vat": lot_rates(fees, lot)[1],
            "fixed": round(fees.fixed + lot.extra_fee, 2),
            "units": units, "count": quantity(lot.title),
            "market": market_value(item, est, units), "marketSource": ("manual" if item.market_price is not None else
                                                                       "marktplaats" if est else None),
            "mp": ({"median": est.median, "low": est.low, "high": est.high, "count": est.count,
                    "query": est.query, "url": est.url, "prices": est.prices, "outliers": est.outliers,
                    "listings": est.listings, "asOf": est.as_of, "kind": est.kind, "model": est.model}
                   if est else None),
            "mpPlan": ({"kind": plan.kind, "model": plan.model, "brand": plan.brand, "note": plan.note,
                        "search": search_url(plan.searches[0])} if plan else None),
            "mpSearch": search_url(plan.searches[0] if plan else lot.title),
            "itemMaxPrice": item.max_price, "itemMinMargin": item.min_margin,
            "firstSeen": (seen.get(lot.key) or {}).get("first"), "isNew": lot.key in new_keys,
            "maxBid": v.max_bid, "isDeal": v.is_deal,
        })
    sites = [{"id": s, "name": SITE_NAMES.get(s, s), "ok": r.get("ok", False), "lots": r.get("lots", 0),
              "error": r.get("error", "")} for s, r in report.items()]
    site_cfg = config.get("sites") or {}
    notes = {"onlineveilingmeester": "Domeinen lots 10%; margin-scheme lots 21% (Domeinen 12.1%) incl. VAT",
             "troostwijk": "an estimate: Troostwijk sets it per auction, check the lot page",
             "vlavem": "VAT only on the 17% for used goods; counted on the bid too, to be safe",
             "inventarisveilingen": "no premium; lots without bids show no price (starting price on the lot page)"}
    fees = [{"id": s, "name": SITE_NAMES.get(s, s), "premium": f.premium, "vat": f.vat, "note": notes.get(s, "")}
            for s, f in site_fees.items() if site_cfg.get(s, {}).get("enabled", True) or s in report]
    troostwijk = []
    if site_cfg.get("troostwijk", {}).get("enabled", True) is False:
        for item in items:
            troostwijk.append({"item": item.name, "links": [
                {"label": k, "url": "https://www.troostwijkauctions.com/nl/search?" +
                 urlencode({"searchTerm": k, "countries": "nl"})} for k in item.keywords[:8]]})
    return {
        "generated": now.isoformat(),
        "settings": {"min_margin": settings.min_margin,
                     "resale_factor": settings.resale_factor, "selling_costs": settings.selling_costs},
        "kinds": auction_kinds(config),
        "sites": sites, "fees": sorted(fees, key=lambda f: f["premium"]), "lots": lots,
        "troostwijk": troostwijk,
        "driving": driving or {"home": False},
        "favorites": favorites or {"issue": None, "items": []},
        "repo": os.environ.get("GITHUB_REPOSITORY", ""),
    }


def write_report(path: Path, rows: list[Row], report: dict, now: datetime, url: str | None,
                 kinds: str = "bankruptcy, closure, estate or Domeinen") -> str:
    local = now.astimezone(AMS).strftime("%A %d %B %Y, %H:%M")
    lines = ["# Latest scan\n", f"_{local} (Amsterdam time)_\n"]
    if url:
        lines.append(f"**Dashboard:** {url}\n")
    lines += ["| Site | Status |", "|---|---|"]
    for site, r in report.items():
        unit = "price lookups" if site == "marktplaats" else "lots"
        status = f"✅ {r['lots']} {unit}" if r.get("ok") else f"⚠️ {r.get('error', '')[:120]}"
        lines.append(f"| {SITE_NAMES.get(site, site)} | {status} |")
    lines += ["", f"## Matching lots ({len(rows)})\n"]
    if rows:
        lines += ["| | Item | Lot | Next bid | Market | Max bid | Closes |", "|---|---|---|---|---|---|---|"]
        for item, lot, v, est in rows:
            title = lot.title.replace("|", "/")[:70]
            closes = lot.closes_at.astimezone(AMS).strftime("%a %d %b %H:%M") if lot.closes_at else (closing_day(lot) or "?")
            market = fmt_eur(est.median) if est else (fmt_eur(item.market_price) if item.market_price else "–")
            lines.append(f"| {'✅' if v.is_deal else ''} | {item.name} | [{title}]({lot.url}) ({SITE_NAMES.get(lot.site)}) | "
                         f"{fmt_eur(v.bid)} | {market} | {fmt_eur(v.max_bid) if v.max_bid is not None else '–'} | {closes} |")
    else:
        lines.append(f"Nothing on your watchlist is in a running {kinds} auction right now.")
    text = "\n".join(lines) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


# ---------------------------------------------------------------- run

def run_scan(root: Path, now: datetime, dry_run: bool = False, only: list[str] | None = None,
             http_cls=Http, telegram_cls=Telegram) -> int:
    config = load_yaml(root / "config.yml")
    watchlist = load_yaml(root / "watchlist.yml")
    state_path = root / "data" / "state.json"
    state = load_json(state_path)
    delay = float((config.get("http") or {}).get("delay_seconds", 1.0))
    url = dashboard_url(config)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    tg = telegram_cls(http_cls(delay=0.5), token, chat_id if token else None, dry_run=dry_run)

    drv_cfg = config.get("driving") or {}
    items = [i for i in (WatchItem.from_dict(d) for d in watchlist.get("items") or []) if i.keywords]
    settings = Settings.from_dict(watchlist.get("settings"), include_trip=bool(drv_cfg.get("include_in_max_bid", True)))
    max_units = int((config.get("marktplaats") or {}).get("max_items_per_lot", 0) or 0)

    # 1. scrape (skipped when the watchlist is empty)
    lots, report, site_requests = ([], {}, 0)
    if items:
        lots, report, site_requests = scan_sites(config, items, state, lambda: http_cls(delay=delay), now, only)

    # 1b. Troostwijk lots from Troostwijk's own alert emails (the bot never visits Troostwijk itself)
    mail_cfg = config.get("troostwijk_alerts") or {}
    mailbox = Mailbox.from_env()
    mail_report = None
    if items and mailbox and mail_cfg.get("enabled", True) is not False and (not only or "troostwijk" in only):
        h = state.setdefault("health", {}).setdefault("troostwijk", {})
        try:
            mail_lots, mail_report = collect_alert_lots(
                mailbox, http_cls(delay=1.0), state, now, int(mail_cfg.get("keep_days", 14)),
                is_bankruptcy=auction_filter(config),
                only_bankruptcy=mail_cfg.get("only_bankruptcy", True) is not False)
            lots.extend(mail_lots)
            report["troostwijk"] = {"ok": True, "lots": len(mail_lots), "via": "email"}
            h.update(ok=True, lots=len(mail_lots), fails=0, error="", at=now.isoformat(), last_ok=now.isoformat())
        except Exception as e:  # wrong app password, mailbox unreachable, ...
            log.error("troostwijk alerts mailbox failed: %s", type(e).__name__)
            error = f"alerts mailbox: {type(e).__name__}: {str(e)[:150]}"
            report["troostwijk"] = {"ok": False, "error": error}
            h.update(ok=False, error=error, fails=h.get("fails", 0) + 1, at=now.isoformat())
    alerts_cfg = config.get("digest") or config.get("alerts") or {}
    horizon = now + timedelta(days=float(alerts_cfg.get("ignore_closing_after_days", 30)))
    lots = [lot for lot in lots if lot.closes_at is None or now < lot.closes_at <= horizon]
    matched = match_lots(items, lots)
    log.info("%d lots scanned, %d match the watchlist", len(lots), len(matched))

    # favorites (starred on the dashboard, kept in a GitHub issue): never hidden by the filters below
    store = FavoritesStore(http_cls(delay=0.2))
    favs = store.load() if items else []
    fav_keys = {f["key"] for f in favs}

    # 1c. the description on the lot page: the type number when the title has none ("2 x Dell 24 inch monitor"),
    # a defect ("Scherm beschadigd"), storage or memory ("64 GB"). Lots without a type number in the title first,
    # then the ones closing soonest.
    det_cfg = config.get("descriptions") or {}
    if det_cfg.get("enabled", True) is not False and (not only or set(only) - {"troostwijk"}):
        def identified(lot: Lot) -> bool:
            return not (model_code(lot.title) is None and mac_plan(lot.title) is None and phone_plan(lot.title) is None)

        order = sorted(matched, key=lambda m: (identified(m[1]), m[1].closes_at or horizon))
        read = fill_descriptions(
            order, lambda: http_cls(delay=float((config.get("http") or {}).get("delay_seconds", 1.5))),
            state.setdefault("descriptions", {}), now, needs=lambda lot: True,
            limit=int(det_cfg.get("max_pages_per_run", 80)))
        log.info("read %d lot descriptions", read)
    # 1d. lots with a defect ("Scherm beschadigd", "werkt niet", "voor onderdelen"): leave them out entirely
    defect_lots = 0
    if defect_filter(config):
        kept = [(item, lot) for item, lot in matched
                if lot.key in fav_keys or not defect(lot.title, lot.description, lot.condition)]
        defect_lots = len(matched) - len(kept)
        matched = kept
        log.info("%d lots with a defect left out", defect_lots)
    # 1e. Apple products, laptops and phones from before 2020 resell poorly: leave them out entirely
    min_year = age_filter(config)
    old_lots = 0
    if min_year:
        kept = [(item, lot) for item, lot in matched if lot.key in fav_keys or not too_old(item, lot, min_year)]
        old_lots = len(matched) - len(kept)
        matched = kept
        log.info("%d Apple/laptop/phone lots from before %d left out", old_lots, min_year)
    # 1f. lots you follow on Troostwijk (the heart): priced for the Telegram digest only, so the public
    # dashboard and repository never show what you follow. Like favorites, the filters above don't hide them.
    followed = list((mail_report or {}).get("followed") or []) if dry_run or (token and chat_id) else []
    on_list = {lot.key for _, lot in matched}
    to_price: list[tuple[WatchItem, Lot]] = []
    unpriced: list[tuple[WatchItem, Lot]] = []  # an accessory your watchlist excludes ("iPhone 13 hoesje")
    for lot in followed:
        if lot.key in on_list:
            continue  # already on the dashboard: that row is reused
        listed = next((i for i, _ in match_lots(items, [lot])), None)
        item = listed or followed_item(lot.title)
        if not item:
            continue
        text = normalize(lot.title)
        excluded = not listed and any(any(phrase_in(k, text) for k in i.keywords) for i in items)
        (unpriced if excluded else to_price).append((item, lot))
    if not only or set(only) - {"troostwijk"}:
        read = fill_start_prices(matched, lambda: http_cls(delay=float((config.get("http") or {}).get("delay_seconds", 1.5))),
                                 state.setdefault("start_prices", {}), now)
        log.info("read %d starting prices", read)
    # the bid you'd have to place now: the starting bid, or the current bid + one step
    fill_next_bids(matched, parse_steps((config.get("bidding") or {}).get("steps")))

    # 2. driving costs to the pickup addresses (needs the HOME_ADDRESS secret)
    geo_http = http_cls(delay=1.1)
    trips: dict = {}
    home = os.environ.get("HOME_ADDRESS", "").strip()
    home2 = os.environ.get("HOME_ADDRESS_2", "").strip()  # optional second starting point (e.g. in Belgium)
    driving = {"home": bool(home or home2), "enabled": drv_cfg.get("enabled", True) is not False,
               "include": settings.include_trip, "kmpl": float(drv_cfg.get("km_per_liter", 16)),
               "roundTrip": drv_cfg.get("round_trip", True) is not False,
               "extraPerKm": float(drv_cfg.get("extra_cost_per_km", 0) or 0),
               "tripRules": trip_rules(drv_cfg), "transport": transport_settings(drv_cfg)}
    places = {p: lot.pickup_latlon for _, lot in matched if (p := pickup_place(lot))}
    # towns only lots you follow are in: looked up on a copy of the saved map lookups, so they stay private
    heart_places = {p: lot.pickup_latlon for _, lot in to_price if (p := pickup_place(lot)) and p not in places}
    heart_trips: dict = {}
    private_requests = 0  # requests for the lots you follow, left out of the public request count
    if driving["enabled"] and (home or home2) and (places or heart_places):
        before = geo_http.request_count
        # only for lots you follow: the saved fuel price isn't refreshed, so state.json doesn't change for them
        price, source = fuel_price(geo_http, state if places else {"fuel": dict(state.get("fuel") or {})}, now,
                                   drv_cfg.get("fuel_price", "auto"), float(drv_cfg.get("fuel_price_fallback", 2.108)))
        if not places:
            private_requests += geo_http.request_count - before
        costs = DrivingCosts(km_per_liter=driving["kmpl"], fuel_price=price, round_trip=driving["roundTrip"],
                             extra_per_km=driving["extraPerKm"])
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        geo_cache = state.setdefault("geo", {}) if places else dict(state.get("geo") or {})
        planner = TripPlanner(geo_http, geo_cache, now, costs, home,
                              user_agent=f"auction-deals-bot (github.com/{repo})" if repo else "auction-deals-bot",
                              second_address=home2)
        if places:
            trips = planner.plan(places)
            planner.prune()
            driving.update(fuel=price, fuelSource=source, error=planner.error)
        if heart_places:
            before = geo_http.request_count
            with quiet_logs(*PRIVATE_LOGS):
                heart_trips = TripPlanner(geo_http, dict(planner.cache), now, costs, home,
                                          user_agent=planner.user_agent, second_address=home2).plan(heart_places)
            private_requests += geo_http.request_count - before
        for _, lot in matched + to_price:
            trip = trips.get(pickup_place(lot) or "") or heart_trips.get(pickup_place(lot) or "")
            lot.trip_cost = round(trip.cost, 2) if trip else None
            if trip and driving["transport"] and lots_needed(trip.minutes, driving["tripRules"]) is None:
                lot.transport = True  # too far to drive: count a transporter instead of fuel
                lot.trip_cost = transport_cost(driving["transport"], 1)

    # 3. price + evaluate
    mp_cfg = config.get("marktplaats") or {}
    http = http_cls(delay=float(mp_cfg.get("delay_seconds", 4)), jitter=float(mp_cfg.get("extra_random_delay", 3)))
    cache_days = float(mp_cfg["cache_days"]) if "cache_days" in mp_cfg else float(mp_cfg.get("cache_hours", 72)) / 24
    finder = PriceFinder(http, state.setdefault("price_cache", {}), now, cache_days=cache_days,
                         stale_days=float(mp_cfg.get("fallback_days", 14)),
                         min_listings=int(mp_cfg.get("min_listings", 4)),
                         min_listings_exact=int(mp_cfg.get("min_listings_exact", 2)),
                         max_lookups=int(mp_cfg.get("max_lookups_per_run", 40)),
                         enabled=mp_cfg.get("enabled", True))
    site_fees = {s: Fees.from_dict(c) for s, c in (config.get("sites") or {}).items()}
    rows: list[Row] = []
    for item, lot in sorted(matched, key=lambda m: closes_by(m[1], horizon)):
        trip = trips.get(pickup_place(lot) or "")
        beyond = bool(trip and lots_needed(trip.minutes, driving["tripRules"]) is None and lot.key not in fav_keys
                      and not driving["transport"])
        est = finder.for_lot(item, lot, lookup=not beyond)  # too far to drive: no Marktplaats search spent on it
        verdict = evaluate(item, lot, site_fees.get(lot.site, Fees()), settings, est, units_of(lot, max_units))
        rows.append((item, lot, verdict, est))
    finder.prune()

    # 3b. lots at one pickup share the trip
    if trips and settings.include_trip:
        def group_cost(place: str, n: int) -> float:
            if driving["transport"] and lots_needed(trips[place].minutes, driving["tripRules"]) is None:
                return transport_cost(driving["transport"], n)
            return trips[place].cost

        rows = share_trips(rows, trips, fav_keys, lambda item, lot, est: evaluate(
            item, lot, site_fees.get(lot.site, Fees()), settings, est, units_of(lot, max_units)), group_cost)

    # 3c. the lots you follow. One already on the dashboard keeps its row, with the reminder's exact closing time
    # (on a copy); the others are priced on a copy of the saved prices, so no search for them ends up in the
    # public repository
    by_key = {lot.key: lot for lot in followed}
    hearts: list[Row] = [
        (i, dataclasses.replace(l, closes_at=f.closes_at or l.closes_at,
                                closes_day=None if f.closes_at else l.closes_day), v, e)
        for i, l, v, e in rows if (f := by_key.get(l.key))]
    for item, lot in unpriced:
        hearts.append((item, lot, evaluate(item, lot, site_fees.get(lot.site, Fees()), settings, None), None))
    if to_price:
        before = http.request_count
        private = PriceFinder(http, dict(state.get("price_cache") or {}), now, cache_days=cache_days,
                              stale_days=finder.stale_days, min_listings=finder.min_listings,
                              min_listings_exact=finder.min_listings_exact,
                              max_lookups=int(mail_cfg.get("followed_lookups", 10)), enabled=finder.enabled)
        private.blocked = finder.blocked
        with quiet_logs(*PRIVATE_LOGS):
            for item, lot in to_price:
                est = private.for_lot(item, lot)
                verdict = evaluate(item, lot, site_fees.get(lot.site, Fees()), settings, est, units_of(lot, max_units))
                hearts.append((item, lot, verdict, est))
        private_requests += http.request_count - before
    hearts.sort(key=lambda r: closes_by(r[1], horizon))
    notes = []
    if defect_lots:
        notes.append(f"<i>🔧 {defect_lots} lot{'s' if defect_lots != 1 else ''} with a defect left out "
                     "(damaged, broken, for parts or locked).</i>")
    if old_lots:
        notes.append(f"<i>🗓 {old_lots} Apple, laptop or phone lot{'s' if old_lots != 1 else ''} from before {min_year} "
                     "left out (they resell poorly).</i>")
    if mail_report and mail_report.get("unreadable_emails"):  # each email is reported once
        bad = mail_report["unreadable_emails"]
        listed = "\n".join(f"• {esc(e['subject'] or '(no subject)')} ({e['date'].astimezone(AMS):%a %d %b})"
                           for e in bad[:5])
        notes.append(f"⚠️ I couldn't find any lots in {'this Troostwijk email' if len(bad) == 1 else f'these {len(bad)} Troostwijk emails'}:\n"
                     f"{listed}\n<i>An email without lots (a confirmation, a newsletter) can be ignored. If it does "
                     "show lots, send it to me as a file (Gmail on a computer: ⋮ → Download message).</i>")
    if mail_report and mail_report.get("saved_searches"):  # each email is passed on once
        terms: dict[str, dict] = {}
        for mail in mail_report["saved_searches"]:
            for found in mail["searches"]:
                terms.setdefault(found["term"].lower(), found)
        links = " · ".join(f'<a href="{attr(t["url"])}">{esc(t["term"])}</a>' for t in terms.values())
        notes.append(f"🔎 Troostwijk has new lots for your saved searches: {links}\n"
                     "<i>Their weekly email only links to Troostwijk's search page, and I don't visit Troostwijk, "
                     "so these lots aren't priced on the dashboard. Tap a word to look yourself.</i>")
    if items:
        mh = state.setdefault("health", {}).setdefault("marktplaats", {})
        failed = finder.blocked or (finder.errors and finder.errors >= finder.lookups)
        if failed:
            why = "blocked the price check" if finder.blocked else "could not be reached"
            error = f"Marktplaats {why}; showing saved prices where available"
            report["marktplaats"] = {"ok": False, "error": error, "lots": finder.lookups}
            mh.update(ok=False, error=error, fails=mh.get("fails", 0) + 1, at=now.isoformat())
            notes.append(f"⚠️ Marktplaats {why} today, so market values are from earlier scans "
                         f"(up to {int(finder.stale_days)} days old) or missing.")
        else:
            report["marktplaats"] = {"ok": True, "lots": finder.lookups}
            mh.update(ok=True, lots=finder.lookups, fails=0, error="", at=now.isoformat())

    # 4. which lots are new since the last scan
    seen = state.setdefault("seen", {})
    new_keys = {lot.key for _, lot, _, _ in rows if lot.key not in seen}
    for _, lot, _, _ in rows:
        rec = seen.setdefault(lot.key, {"first": now.isoformat()})
        rec["closes"] = lot.closes_at.isoformat() if lot.closes_at else None
    for key in list(seen):
        closes = seen[key].get("closes")
        if closes and datetime.fromisoformat(closes) < now - timedelta(days=3):
            del seen[key]

    # 6. dashboard + report
    data = dashboard_data(rows, report, config, settings, site_fees, new_keys, seen, now, items,
                          trips=trips, driving=driving, favorites={"issue": store.issue, "items": favs},
                          max_units=max_units)
    dashboard.write(root / "site", data)
    save_json(root / "data" / "lots.json", data)
    text = write_report(root / "data" / "latest.md", rows, report, now, url, auction_kinds(config))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)

    # 7. telegram digest
    if items and alerts_cfg.get("enabled", True):
        try:
            far = too_far(rows, trips, fav_keys, driving["tripRules"], transport=bool(driving["transport"]))
            tg.send(digest_message(rows, new_keys, settings, now, url, int(alerts_cfg.get("per_section", 8)), notes,
                                   favs=favs, trips=trips, kinds=auction_kinds(config), far=far,
                                   far_rules=driving["tripRules"], hearts=hearts, heart_trips=heart_trips))
        except Exception as e:
            log.warning("could not send the digest: %s", e)
    elif favs:
        fav_msg = favorites_message(favs, rows, now)
        if fav_msg:
            try:
                tg.send(fav_msg)
            except Exception as e:
                log.warning("could not send the favorites message: %s", e)
    for msg in health_messages(state, int(alerts_cfg.get("warn_after_failed_scans", 2))):
        try:
            tg.send(msg)
        except Exception as e:
            log.warning("could not send a warning: %s", e)

    deals = sum(1 for r in rows if r[2].is_deal)
    requests = site_requests + http.request_count + geo_http.request_count - private_requests
    state["last_run"] = {"at": now.isoformat(), "lots": len(lots), "matches": len(rows), "deals": deals,
                         "new": len(new_keys), "requests": requests,
                         "too_old": old_lots, "defects": defect_lots}
    if not dry_run:  # a dry run must not change what counts as "new"
        save_json(state_path, state)
    log.info("done: %d lots, %d matches, %d with room to bid, %d new, %d requests",
             len(lots), len(rows), deals, len(new_keys), requests)
    return 0
