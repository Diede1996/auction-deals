"""The daily scan: scrape the auction sites, price the matches, build the dashboard, send a digest."""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from . import dashboard
from .evaluate import Fees, Settings, Verdict, evaluate, lot_rates, market_value
from .favorites import FavoritesStore, closing_between
from .geo import DrivingCosts, TripPlanner, fuel_price
from .http import Http
from .identify import plan_for, quantity
from .mail_alerts import Mailbox, collect as collect_alert_lots
from .marktplaats import PriceEstimate, search_url
from .matching import bankruptcy_matcher, match_lots, search_terms
from .models import Lot, WatchItem
from .pricing import PriceFinder
from .sites import SITE_NAMES, SITES
from .sites.base import SiteContext
from .storage import dashboard_url, load_json, load_yaml, save_json
from .telegram import Telegram, attr, esc
from .util import AMS, fmt_eur

log = logging.getLogger("scanner")

Row = tuple[WatchItem, Lot, Verdict, "PriceEstimate | None"]


def units_of(lot: Lot, max_units: int = 0) -> int:
    """How many items the resale value counts: "40x Colbert" -> 40, so price, margin and max bid are for
    the whole lot. `max_units` (0 = no limit) makes bigger bulk lots count as one item instead."""
    n = quantity(lot.title)
    return n if n >= 2 and (max_units <= 0 or n <= max_units) else 1


# ---------------------------------------------------------------- scraping

def scan_sites(config: dict, items: list[WatchItem], state: dict, http_factory, now: datetime,
               only: list[str] | None = None) -> tuple[list[Lot], dict, int]:
    """Scrape all enabled sites in parallel (one HTTP client per site). Returns (lots, report, requests)."""
    keywords = config.get("auction_keywords") or config.get("bankruptcy_keywords") or ["faillissement", "curator"]
    is_bankruptcy = bankruptcy_matcher(keywords)
    terms = search_terms(items)
    health = state.setdefault("health", {})
    site_cache = state.setdefault("site_cache", {})
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
    drive = f" · 🚗 {trip.km:.0f} km" if trip else ""
    if lot.bid_from_email:  # the current bid isn't known, only the max bid
        return (f'• <a href="{attr(lot.url)}">{esc(lot.title[:70])}</a>\n'
                f"   bid up to <b>{rough}{fmt_eur(v.max_bid)}</b>{margin} · Troostwijk · closes {when}{drive}")
    return (f'• <a href="{attr(lot.url)}">{esc(lot.title[:70])}</a>\n'
            f"   bid {fmt_eur(v.bid)} → max <b>{rough}{fmt_eur(v.max_bid)}</b>{margin} · "
            f"{SITE_NAMES.get(lot.site, lot.site)} · {when}{drive}")


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
        bid = f"bid {fmt_eur(v.bid)} · " if v else ""
        maxbid = f"your max <b>{fmt_eur(v.max_bid)}</b> · " if v and v.max_bid is not None else (
            f"your max <b>{fmt_eur(f['maxBid'])}</b> · " if f.get("maxBid") is not None else "")
        lines.append(f'• <a href="{attr(f["url"])}">{esc(f["title"][:70])}</a>\n'
                     f"   {bid}{maxbid}closes {c.astimezone(AMS):%H:%M}")
    lines.append("<i>I'll remind you again about an hour before each one closes.</i>")
    return "\n".join(lines)


def digest_message(rows: list[Row], new_keys: set[str], settings: Settings, now: datetime, url: str | None,
                   per_section: int = 8, notes: list[str] | None = None, favs: list[dict] | None = None,
                   trips: dict | None = None) -> str:
    deals = [r for r in rows if r[2].is_deal]
    day = now.astimezone(AMS).strftime("%a %d %b")
    head = [f"☀️ <b>Auction scan</b> · {day}",
            f"{len(rows)} matching lots · <b>{len(deals)} with room to bid</b> · "
            f"{sum(1 for r in rows if r[1].key in new_keys)} new",
            f"<i>Max bids for selling at {settings.resale_factor:.0%} of the Marktplaats median "
            f"with at least {settings.min_margin:.0%} margin</i>"]
    parts = ["\n".join(head)] + list(notes or [])
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
    check = [r for r in rows if r[1].bid_from_email and r[1].key in new_keys and (r[2].max_bid or 0) > 0]
    if check:
        check.sort(key=lambda r: -(r[2].max_bid or 0))
        parts.append("🔎 <b>New from Troostwijk emails</b> · <i>check the current bid on the lot page</i>\n" +
                     "\n".join(_line(i, l, v, e, trips) for i, l, v, e in check[:per_section]))
    if not rows:
        parts.append("Nothing on your watchlist is in a running bankruptcy, closure or Domeinen auction today.")
    elif not soon and not fresh and not check:
        parts.append("Nothing new or closing soon with room to bid.")
    if any(e is not None and e.kind == "general" for _, _, v, e in soon + fresh + check[:per_section]):
        parts.append("<i>≈ rough price: no type number in the lot title, compared with similar items.</i>")
    if url:
        parts.append(f'📊 <a href="{attr(url)}">Open the dashboard</a>')
    return "\n\n".join(parts)


# ---------------------------------------------------------------- dashboard data

def closing_day(lot: Lot) -> str | None:
    """"Wed 7 Oct" for lots whose closing time isn't known, only the day."""
    if not lot.closes_day:
        return None
    day = datetime.fromisoformat(lot.closes_day)
    return f"{day:%a} {day.day} {day:%b}"


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
            "trip": trip.as_dict() if trip else None,
            "closes": lot.closes_at.isoformat() if lot.closes_at else None, "closesDay": lot.closes_day,
            "bidFromEmail": lot.bid_from_email,
            "bid": v.bid, "bids": lot.bids,
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
             "troostwijk": "an estimate: Troostwijk sets it per auction, check the lot page"}
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
        "sites": sites, "fees": sorted(fees, key=lambda f: f["premium"]), "lots": lots,
        "troostwijk": troostwijk,
        "driving": driving or {"home": False},
        "favorites": favorites or {"issue": None, "items": []},
        "repo": os.environ.get("GITHUB_REPOSITORY", ""),
    }


def write_report(path: Path, rows: list[Row], report: dict, now: datetime, url: str | None) -> str:
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
        lines += ["| | Item | Lot | Bid | Market | Max bid | Closes |", "|---|---|---|---|---|---|---|"]
        for item, lot, v, est in rows:
            title = lot.title.replace("|", "/")[:70]
            closes = lot.closes_at.astimezone(AMS).strftime("%a %d %b %H:%M") if lot.closes_at else (closing_day(lot) or "?")
            market = fmt_eur(est.median) if est else (fmt_eur(item.market_price) if item.market_price else "–")
            lines.append(f"| {'✅' if v.is_deal else ''} | {item.name} | [{title}]({lot.url}) ({SITE_NAMES.get(lot.site)}) | "
                         f"{fmt_eur(v.bid)} | {market} | {fmt_eur(v.max_bid) if v.max_bid is not None else '–'} | {closes} |")
    else:
        lines.append("Nothing on your watchlist is in a running bankruptcy, closure or Domeinen auction right now.")
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
    tg = telegram_cls(http_cls(delay=0.5), token, chat_id, dry_run=dry_run or not (token and chat_id))

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
            keywords = config.get("auction_keywords") or config.get("bankruptcy_keywords") or ["faillissement", "curator"]
            mail_lots, mail_report = collect_alert_lots(
                mailbox, http_cls(delay=1.0), state, now, int(mail_cfg.get("keep_days", 14)),
                is_bankruptcy=bankruptcy_matcher(keywords),
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

    # 2. driving costs to the pickup addresses (needs the HOME_ADDRESS secret)
    geo_http = http_cls(delay=1.1)
    trips: dict = {}
    home = os.environ.get("HOME_ADDRESS", "").strip()
    driving = {"home": bool(home), "enabled": drv_cfg.get("enabled", True) is not False,
               "include": settings.include_trip, "kmpl": float(drv_cfg.get("km_per_liter", 16)),
               "roundTrip": drv_cfg.get("round_trip", True) is not False,
               "extraPerKm": float(drv_cfg.get("extra_cost_per_km", 0) or 0)}
    places = {p: lot.pickup_latlon for _, lot in matched if (p := pickup_place(lot))}
    if driving["enabled"] and home and places:
        price, source = fuel_price(geo_http, state, now, drv_cfg.get("fuel_price", "auto"),
                                   float(drv_cfg.get("fuel_price_fallback", 2.108)))
        costs = DrivingCosts(km_per_liter=driving["kmpl"], fuel_price=price, round_trip=driving["roundTrip"],
                             extra_per_km=driving["extraPerKm"])
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        planner = TripPlanner(geo_http, state.setdefault("geo", {}), now, costs, home,
                              user_agent=f"auction-deals-bot (github.com/{repo})" if repo else "auction-deals-bot")
        trips = planner.plan(places)
        planner.prune()
        driving.update(fuel=price, fuelSource=source, error=planner.error)
        for _, lot in matched:
            trip = trips.get(pickup_place(lot) or "")
            lot.trip_cost = round(trip.cost, 2) if trip else None

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
    for item, lot in sorted(matched, key=lambda m: m[1].closes_at or horizon):
        est = finder.for_lot(item, lot)
        verdict = evaluate(item, lot, site_fees.get(lot.site, Fees()), settings, est, units_of(lot, max_units))
        rows.append((item, lot, verdict, est))
    finder.prune()
    notes = []
    if mail_report and mail_report.get("unreadable"):
        notes.append(f"⚠️ I couldn't find any lots in {mail_report['unreadable']} Troostwijk alert email(s). "
                     "Their email layout may be new to me: save one as a file (Gmail: ⋮ → Download message) "
                     "and share it so the bot can learn it.")
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

    # 5. favorites (starred on the dashboard, kept in a GitHub issue)
    store = FavoritesStore(http_cls(delay=0.2))
    favs = store.load() if items else []

    # 6. dashboard + report
    data = dashboard_data(rows, report, config, settings, site_fees, new_keys, seen, now, items,
                          trips=trips, driving=driving, favorites={"issue": store.issue, "items": favs},
                          max_units=max_units)
    dashboard.write(root / "site", data)
    save_json(root / "data" / "lots.json", data)
    text = write_report(root / "data" / "latest.md", rows, report, now, url)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)

    # 7. telegram digest
    if items and alerts_cfg.get("enabled", True):
        try:
            tg.send(digest_message(rows, new_keys, settings, now, url, int(alerts_cfg.get("per_section", 8)), notes,
                                   favs=favs, trips=trips))
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
    requests = site_requests + http.request_count + geo_http.request_count
    state["last_run"] = {"at": now.isoformat(), "lots": len(lots), "matches": len(rows), "deals": deals,
                         "new": len(new_keys), "requests": requests}
    if not dry_run:  # a dry run must not change what counts as "new"
        save_json(state_path, state)
    log.info("done: %d lots, %d matches, %d with room to bid, %d new, %d requests",
             len(lots), len(rows), deals, len(new_keys), requests)
    return 0
