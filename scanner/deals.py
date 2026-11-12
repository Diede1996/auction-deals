"""Marktplaats bargain finder (Diede, 6 Nov 2026): new listings by private sellers that sell below what the same
thing usually goes for on Marktplaats, priced with the same rules as the auction lots.

Each run (the "Marktplaats deals" workflow, several times a day):
1. Takes the watchlist keywords that were searched longest ago (`searches_per_run` of them) and asks Marktplaats
   for the newest listings of each (the same search the price comparison already uses, newest first).
2. Keeps listings it hasn't seen before, with a price, from private sellers (no paid "Dagtopper" placements,
   no shops), that match a watchlist item and aren't damaged or too old.
3. Prices them like an auction lot without premium or VAT: the Marktplaats median of comparable listings,
   your margin and minimum profit, and the drive to the seller's town and back.
4. Sends the ones whose asking price is at or below that maximum to Telegram, and saves them to your PRIVATE
   repository (data/marktplaats-deals.json) for the dashboard. Nothing about them goes into this public repository:
   the bot's memory of what it saw is kept encrypted in data/deals.enc.

When Marktplaats refuses the bot, it stops for that run and doesn't try to get around it.
"""
from __future__ import annotations

import html
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from . import claude_check, private_repo
from .age import age_filter, age_skip, too_old
from .condition import defect, defect_filter
from .evaluate import Fees, Settings, Verdict, evaluate
from .geo import FALLBACK_KMH, ROAD_FACTOR, DrivingCosts, Trip, fuel_price
from .http import BlockedError, Http
from .identify import quantity
from .marktplaats import API, PriceEstimate, is_business
from .matching import match_lots
from .models import Lot, WatchItem
from .pricing import PriceFinder
from .storage import load_json, load_yaml
from .telegram import Telegram, attr, esc
from .util import AMS, fmt_eur, normalize

log = logging.getLogger(__name__)

SITE = "mpdeal"
BASE = "https://www.marktplaats.nl"
NO_FEES = Fees(premium=0.0, vat=0.0, fixed=0.0)
PRICED = {"FIXED", "MIN_BID"}
# shops and paid placements: "Dagtopper" and "Topadvertentie" ads are almost always shops
RECENT = {"vandaag", "gisteren"}  # Marktplaats' own "date" for listings from today and yesterday


def settings_of(config: dict) -> dict:
    return config.get("marktplaats_deals") or {}


# ---------------------------------------------------------------- the bot's memory (encrypted)

def load_memory(path: Path) -> dict:
    secret = private_repo._secret()
    if not secret or not path.exists():
        return {}
    from cryptography.fernet import InvalidToken
    from .inventory import _fernet
    try:
        return json.loads(_fernet("deals|" + secret).decrypt(path.read_bytes()))
    except (InvalidToken, ValueError):
        log.warning("%s can't be opened with this key: starting afresh", path.name)
        return {}


def save_memory(memory: dict, path: Path) -> bool:
    secret = private_repo._secret()
    if not secret:
        return False  # a local run without a key: nothing is written to the public repository
    from .inventory import _fernet
    path.write_bytes(_fernet("deals|" + secret).encrypt(json.dumps(memory, ensure_ascii=False, sort_keys=True).encode()))
    return True


# ---------------------------------------------------------------- searching

def eligible_items(items: list[WatchItem], cfg: dict) -> list[WatchItem]:
    skip = {normalize(s) for s in cfg.get("skip_items") or ["Wine", "Mixed lot"]}
    return [i for i in items if i.price_check and normalize(i.name) not in skip]


def searches(items: list[WatchItem], cfg: dict) -> list[str]:
    """Every keyword of the items the finder looks for, once (or config's own list)."""
    if cfg.get("searches"):
        return [str(s) for s in cfg["searches"]]
    seen, out = set(), []
    for item in items:
        for k in item.keywords:
            n = normalize(k)
            if n and n not in seen:
                seen.add(n)
                out.append(k.strip())
    return out


def next_searches(all_searches: list[str], last: dict, n: int) -> list[str]:
    """The n searches done longest ago (never done first, in watchlist order)."""
    order = sorted(range(len(all_searches)), key=lambda i: (last.get(normalize(all_searches[i]), ""), i))
    return [all_searches[i] for i in order[:n]]


def home_postcode(http, address: str) -> str | None:
    """The Dutch postcode of your HOME_ADDRESS (PDOK, the same lookup as the driving costs), so Marktplaats can
    search near you. Kept only in the encrypted memory."""
    from .geo import PDOK
    if not address:
        return None
    try:
        data = http.json(f"{PDOK}?{urlencode({'q': address, 'rows': 1, 'fl': 'postcode'})}")
        docs = (data.get("response") or {}).get("docs") or []
        code = (docs[0].get("postcode") if docs else "") or ""
        return code.replace(" ", "").upper() or None
    except Exception as e:
        log.warning("postcode lookup failed: %s", type(e).__name__)
        return None


def newest(http, query: str, limit: int = 50, postcode: str | None = None, radius_km: float = 0) -> list[dict]:
    params = {"query": query, "limit": limit, "offset": 0, "searchInTitleAndDescription": "false",
              "viewOptions": "list-view", "sortBy": "SORT_INDEX", "sortOrder": "DECREASING"}
    if postcode and radius_km:  # only listings near you
        params.update(postcode=postcode, distanceMeters=int(radius_km * 1000))
    return http.json(f"{API}?{urlencode(params)}").get("listings") or []


def price_of(li: dict) -> float | None:
    info = li.get("priceInfo") or {}
    cents = info.get("priceCents")
    if info.get("priceType") not in PRICED or not cents:
        return None
    return cents / 100


def to_lot(li: dict, query: str) -> Lot | None:
    price = price_of(li)
    item_id = li.get("itemId")
    if price is None or not item_id:
        return None
    images = li.get("imageUrls") or []
    image = images[0] if images else None
    if image and image.startswith("//"):
        image = "https:" + image
    loc = li.get("location") or {}
    city = loc.get("cityName") or None
    bid_from = (li.get("priceInfo") or {}).get("priceType") == "MIN_BID"
    return Lot(site=SITE, lot_id=str(item_id), title=html.unescape(li.get("title") or "").strip(),
               url=BASE + (li.get("vipUrl") or f"/{item_id}"), current_bid=price, next_bid=price,
               closes_at=None, auction_title=("Bieden vanaf" if bid_from else "Vraagprijs") + f" · {query}",
               description=html.unescape(li.get("description") or ""), image=image, location=city,
               pickup=f"{city}, Nederland" if city and not loc.get("abroad") else None)


# ---------------------------------------------------------------- the run

def message(found: list[tuple[WatchItem, Lot, Verdict, PriceEstimate | None]], trips: dict,
            notes: dict | None = None) -> str:
    lines = [f"🛒 <b>Marktplaats: {len(found)} possible bargain{'s' if len(found) != 1 else ''}</b>"]
    for item, lot, v, est in found:
        trip = trips.get(lot.pickup or "")
        drive = f" · 🚗 ≈{trip.km:.0f} km" if trip else ""
        rough = "≈" if est is not None and est.kind == "general" else ""
        profit = f" · profit ≈ {fmt_eur(v.profit)}" if v.profit is not None else ""
        median = f" (median {fmt_eur(est.median)}, {est.count} listings)" if est else ""
        lines.append(f'• <a href="{attr(lot.url)}">{esc(lot.title[:70])}</a>\n'
                     f"   asks {fmt_eur(v.bid)} · worth paying up to <b>{rough}{fmt_eur(v.max_bid)}</b>{median}"
                     f"{profit} · {esc(lot.location or '?')}{drive}")
        if (notes or {}).get(lot.key):
            lines[-1] += f"\n   🤖 Claude: {esc(notes[lot.key])}"
    lines.append("Check photos and description first: the price is a comparison of asking prices, not a guarantee.")
    return "\n".join(lines)


def run_deals(root: Path, now: datetime, dry_run: bool = False, http_cls=Http, telegram_cls=Telegram) -> int:
    from .scan import dashboard_data  # late: scan imports a lot

    config = load_yaml(root / "config.yml")
    cfg = settings_of(config)
    if cfg.get("enabled", True) is False:
        log.info("marktplaats deals: off (config.yml marktplaats_deals.enabled)")
        return 0
    local = now.astimezone(AMS)
    hours = cfg.get("hours") or [7, 23]
    if not dry_run and not (int(hours[0]) <= local.hour < int(hours[1])):
        log.info("marktplaats deals: outside %s-%s h, nothing to do", hours[0], hours[1])
        return 0
    watchlist = load_yaml(root / "watchlist.yml")
    drv_cfg = config.get("driving") or {}
    items = eligible_items([i for i in (WatchItem.from_dict(d) for d in watchlist.get("items") or []) if i.keywords],
                           cfg)
    settings = Settings.from_dict(watchlist.get("settings"), include_trip=bool(drv_cfg.get("include_in_max_bid", True)))
    mem_path = root / "data" / "deals.enc"
    memory = load_memory(mem_path)
    seen: dict = memory.setdefault("seen", {})  # itemId -> first seen (ISO)
    last: dict = memory.setdefault("searched", {})  # normalized search -> last searched (ISO)
    kept: list = memory.setdefault("deals", [])  # dashboard rows of earlier bargains

    mp_cfg = config.get("marktplaats") or {}
    http = http_cls(delay=float(cfg.get("delay_seconds", mp_cfg.get("delay_seconds", 4))),
                    jitter=float(mp_cfg.get("extra_random_delay", 3)))
    home = os.environ.get("HOME_ADDRESS", "").strip()
    if home and not memory.get("postcode"):
        memory["postcode"] = home_postcode(http_cls(delay=1.0), home)
    radius = float(cfg.get("radius_km", 50))
    todo = next_searches(searches(items, cfg), last, int(cfg.get("searches_per_run", 12)))
    candidates: list[Lot] = []
    blocked = False
    for query in todo:
        first_time = normalize(query) not in last
        try:
            listings = newest(http, query, int(cfg.get("listings_per_search", 50)), memory.get("postcode"), radius)
        except BlockedError as e:
            log.warning("marktplaats refused the bot (%s): stopping for this run", e)
            blocked = True
            break
        except Exception as e:  # one failed search shouldn't stop the others
            log.warning("marktplaats search %r failed: %s", query, e)
            continue
        last[normalize(query)] = now.isoformat()
        for li in listings:
            item_id = str(li.get("itemId") or "")
            if not item_id or item_id in seen:
                continue
            seen[item_id] = now.isoformat()
            if is_business(li) or (li.get("location") or {}).get("abroad"):
                continue
            if first_time and normalize(str(li.get("date") or "")) not in RECENT:
                continue  # the first time a search runs, only today's and yesterday's listings count as new
            lot = to_lot(li, query)
            meters = (li.get("location") or {}).get("distanceMeters")
            if lot and isinstance(meters, (int, float)) and meters >= 0:
                lot.distance_km = meters / 1000  # straight line from your postcode, says Marktplaats
            if lot:
                candidates.append(lot)
    matched = match_lots(items, candidates)
    if defect_filter(config):
        matched = [(i, lot) for i, lot in matched if not defect(lot.title, lot.description)]
    min_year = age_filter(config)
    if min_year:
        matched = [(i, lot) for i, lot in matched if not too_old(i, lot, min_year, age_skip(config))]
    log.info("marktplaats deals: %d searches (%s), %d new private listings, %d match the watchlist",
             len(todo), f"within {radius:g} km" if memory.get("postcode") else "all of NL: no postcode",
             len(candidates), len(matched))

    # the drive to each seller: Marktplaats gives the straight-line distance from your postcode; roads are ~1.3x
    # longer (looking the towns up on a map put several in the wrong place, so this is more reliable)
    trips: dict = {}
    if drv_cfg.get("enabled", True) is not False and any(getattr(l, "distance_km", None) is not None for _, l in matched):
        shared = load_json(root / "data" / "state.json")  # read only: the daily scan owns state.json
        price, _ = fuel_price(http_cls(delay=1.0), {"fuel": dict(shared.get("fuel") or {})}, now,
                              drv_cfg.get("fuel_price", "auto"), float(drv_cfg.get("fuel_price_fallback", 2.108)))
        costs = DrivingCosts(km_per_liter=float(drv_cfg.get("km_per_liter", 16)), fuel_price=price,
                             round_trip=drv_cfg.get("round_trip", True) is not False,
                             extra_per_km=float(drv_cfg.get("extra_cost_per_km", 0) or 0))
        for _, lot in matched:
            km = getattr(lot, "distance_km", None)
            if km is None:
                continue
            road = km * ROAD_FACTOR
            lot.pickup = lot.pickup or lot.location or lot.lot_id
            trips.setdefault(lot.pickup, Trip(road, road / FALLBACK_KMH * 60, costs.cost(road), approx=True))  # same town: same drive
    max_minutes = float(cfg.get("max_minutes", 45))
    for _, lot in matched:
        trip = trips.get(lot.pickup or "")
        lot.trip_cost = round(trip.cost, 2) if trip else None
    matched = [(i, lot) for i, lot in matched
               if not (trips.get(lot.pickup or "") and trips[lot.pickup].minutes > max_minutes)]

    # price them like an auction lot without premium or VAT
    shared_prices = dict(load_json(root / "data" / "state.json").get("price_cache") or {})
    price_cache = {**shared_prices, **memory.get("prices", {})}
    finder = PriceFinder(http, price_cache, now, cache_days=float(mp_cfg.get("cache_days", 3)),
                         max_lookups=0 if blocked else int(cfg.get("max_price_lookups", 15)),
                         min_listings=int(mp_cfg.get("min_listings", 4)))
    log.info("marktplaats deals: %d within %g min drive", len(matched), max_minutes)
    found = []
    unpriced = 0
    for item, lot in matched:
        est = finder.for_lot(item, lot)
        units = quantity(lot.title) if quantity(lot.title) >= 2 else 1
        v = evaluate(item, lot, NO_FEES, settings, est, units)
        unpriced += est is None
        if est is None and (finder.lookups_left <= 0 or finder.blocked):
            seen.pop(lot.lot_id, None)  # no price searches left this run: look at it again next time
        if v.is_deal and (est is not None or item.market_price is not None):
            found.append((item, lot, v, est))
    memory["prices"] = {k: v for k, v in price_cache.items() if k not in shared_prices or shared_prices[k] != v}
    # Claude reads each bargain and the listings it was compared with (only with ANTHROPIC_API_KEY)
    found, notes = claude_check.check(found, lambda item, lot, est: evaluate(
        item, lot, NO_FEES, settings, est, quantity(lot.title) if quantity(lot.title) >= 2 else 1))
    log.info("marktplaats deals: %d price searches (%d left, blocked %s), %d without a price, %d bargains",
             finder.lookups, finder.lookups_left, finder.blocked, unpriced, len(found))

    # remember, tell and save
    keep_days = float(cfg.get("keep_days", 7))
    cutoff = (now - timedelta(days=keep_days)).isoformat()
    memory["seen"] = {k: v for k, v in seen.items() if v >= (now - timedelta(days=30)).isoformat()}
    if found:
        data = dashboard_data(found, {}, config, settings, {SITE: NO_FEES}, {lot.key for _, lot, _, _ in found},
                              {lot.key: {"first": now.isoformat()} for _, lot, _, _ in found}, now, items, trips)
        for d in data["lots"]:
            if notes.get(d.get("key")):
                d["claude"] = notes[d["key"]]
        have = {d.get("key") for d in data["lots"]}
        kept[:] = data["lots"] + [d for d in kept if d.get("key") not in have]
    kept[:] = [d for d in kept if (d.get("firstSeen") or "") >= cutoff]
    memory["blocked"] = now.isoformat() if blocked else None

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if found and cfg.get("telegram", True) is not False:
        tg = telegram_cls(http_cls(delay=0.5), token, chat_id if token else None, dry_run=dry_run)
        tg.send(message(found, trips, notes))
    if not dry_run and (found or not memory.get("saved_once")):
        if private_repo.put_json("data/marktplaats-deals.json", {"generated": now.isoformat(), "lots": kept},
                                 "Marktplaats bargains", config):
            memory["saved_once"] = True
    if not dry_run:
        save_memory(memory, mem_path)
    return 0
