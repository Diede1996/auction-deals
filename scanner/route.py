"""Marktplaats bargains on the way to an auction pickup (Diede, 12 Nov 2026).

A Marktplaats bargain means a drive for one item, so it must make more profit (marktplaats_deals.min_profit).
Close to the pickup address of an auction lot you're collecting anyway, the usual lot minimum is enough: these
are the pickup addresses of your favorites that closed in the last few days or close in the coming week.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from .geo import geocode, haversine_km, town_label
from .storage import load_json

log = logging.getLogger(__name__)
MAX_LOOKUPS = 15  # new addresses looked up per run (PDOK, free); the rest wait for the next run


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
    cache = memory.setdefault("geo", {})
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
    return spots


def near(lot, spots: list[dict], memory: dict, http_cls, km: float) -> str | None:
    """'Veghel (closes 14 Nov)' when the seller's town is within km of a pickup spot, else None."""
    if not spots or not lot.location:
        return None
    cache = memory.setdefault("geo", {})
    key = f"{lot.location}, Nederland"
    if key not in cache:
        p = geocode(http_cls(delay=1.0), key)
        cache[key] = list(p) if p else None
    if not cache.get(key):
        return None
    here = tuple(cache[key])
    best = min(spots, key=lambda s: haversine_km(here, s["point"]))
    if haversine_km(here, best["point"]) > km:
        return None
    day = datetime.fromisoformat(best["closes"]).strftime("%-d %b")
    return f"{town_label(best['address'])} (auction closes {day})"
