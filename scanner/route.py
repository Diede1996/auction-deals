"""Marktplaats bargains on the way: near home, near work, or near an auction pickup (Diede, 12 Nov 2026).

A Marktplaats bargain means a drive for one item, so it must make more profit (marktplaats_deals.min_profit).
Close to the pickup address of an auction lot you're collecting anyway, the usual lot minimum is enough: these
are the pickup addresses of your favorites that closed in the last few days or close in the coming week, and
within ~20 minutes' drive of home or of work (secret WORK_ADDRESS; never written anywhere readable or logged).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from .geo import FALLBACK_KMH, ROAD_FACTOR, geocode, haversine_km, town_label
from .storage import load_json

log = logging.getLogger(__name__)
MAX_LOOKUPS = 15  # new addresses looked up per run (PDOK, free); the rest wait for the next run


def _cache(memory: dict) -> dict:
    """Looked-up points, kept in the bot's encrypted memory; anything that isn't a (lat, lon) pair is dropped."""
    cache = memory.get("route_points")
    if not isinstance(cache, dict):
        cache = memory["route_points"] = {}
    for k, v in list(cache.items()):
        if v is not None and not (isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v)):
            del cache[k]
    return cache


def pickup_spots(root: Path, now: datetime, cfg: dict, memory: dict, http_cls) -> list[dict]:
    """[{address, closes, point}] of favorites with a pickup address in the window."""
    favs = (load_json(root / "data" / "lots.json").get("favorites") or {}).get("items") or []
    before = now - timedelta(days=float(cfg.get("route_days_after_close", 4)))
    after = now + timedelta(days=float(cfg.get("route_days_before_close", 7)))
    spots, addresses = [], {}
    for f in favs:
        addr, closes = (f.get("pickup") or "").strip(), f.get("closes")
        if not addr or not closes:
            continue
        try:
            t = datetime.fromisoformat(closes)
        except ValueError:
            continue
        if before <= t <= after and addr not in addresses:
            addresses[addr] = closes
    cache = _cache(memory)
    lookups = 0
    http = None
    for addr, closes in addresses.items():
        if addr not in cache and lookups < MAX_LOOKUPS:
            http = http or http_cls(delay=1.0)
            p = geocode(http, addr)
            cache[addr] = list(p) if p else None
            lookups += 1
        if cache.get(addr):
            spots.append({"address": addr, "closes": closes, "point": tuple(cache[addr])})
    log.info("route: %d auction pickup addresses in the window", len(spots))
    work = os.environ.get("WORK_ADDRESS", "").strip()
    if work:
        point = memory.get("work_point") if memory.get("work_for") == work else None
        if point is None:
            p = geocode(http or http_cls(delay=1.0), work)
            memory["work_point"], memory["work_for"] = (list(p) if p else None), work
            point = memory["work_point"]
        if point:
            spots.append({"label": "work", "point": tuple(point),
                          "km": float(cfg.get("route_minutes", 20)) / 60 * FALLBACK_KMH / ROAD_FACTOR})
    return spots


def near_home(lot, minutes: float) -> bool:
    """Within this many minutes' drive of home (Marktplaats gives the straight line from your postcode)."""
    km = getattr(lot, "distance_km", None)
    return km is not None and km * ROAD_FACTOR / FALLBACK_KMH * 60 <= minutes


def near(lot, spots: list[dict], memory: dict, http_cls, km: float) -> str | None:
    """'Veghel (closes 14 Nov)' when the seller's town is within km of a pickup spot, else None."""
    if not spots or not lot.location:
        return None
    cache = _cache(memory)
    key = f"{lot.location}, Nederland"
    if key not in cache:
        p = geocode(http_cls(delay=1.0), key)
        cache[key] = list(p) if p else None
    if not cache.get(key):
        return None
    here = tuple(cache[key])
    work = [s for s in spots if s.get("label") == "work"]
    if work and haversine_km(here, work[0]["point"]) <= work[0]["km"]:
        return "your work"
    spots = [s for s in spots if s.get("address")]
    if not spots:
        return None
    best = min(spots, key=lambda s: haversine_km(here, s["point"]))
    if haversine_km(here, best["point"]) > km:
        return None
    day = datetime.fromisoformat(best["closes"]).strftime("%-d %b")
    return f"{town_label(best['address'])} (auction closes {day})"
