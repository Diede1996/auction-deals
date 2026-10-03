"""Driving costs from your home to each lot's pickup address.

- Your address comes from the HOME_ADDRESS secret, and an optional second one (e.g. in Belgium) from
  HOME_ADDRESS_2. They are looked up every run and never written to the repository (the repository and
  dashboard are public). With two addresses, each lot gets the trip from whichever is closer, labelled
  "from <town of the second address>".
- Addresses are looked up with PDOK Locatieserver (Dutch government, free) and, for addresses outside
  the Netherlands, OpenStreetMap Nominatim. Pickup addresses are remembered, so each is looked up once.
- Driving distance and time come from the OSRM route planner (OpenStreetMap data): one request for all
  pickup addresses of a scan. If it can't be reached, the straight-line distance x 1.3 is used instead.
- Fuel price: the official Belgian maximum price for Euro 95 E10, read once a day, unless you set your own.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from .util import normalize

log = logging.getLogger(__name__)

PDOK = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
OSRM = "https://router.project-osrm.org/table/v1/driving/"
FUEL_URL = "https://www.energiafed.be/nl/maximumprijzen"
ROAD_FACTOR = 1.3  # roads are longer than a straight line
FALLBACK_KMH = 75

_ABROAD = re.compile(r"\b(belgi[eë]|belgium|belgique|deutschland|duitsland|germany|luxemb\w+)\b", re.I)


@dataclass
class Trip:
    km: float  # one way
    minutes: float  # one way
    cost: float  # fuel (and optional per-km costs) for the whole trip
    approx: bool = False  # straight-line estimate, the route planner wasn't reachable
    origin: str | None = None  # where the trip starts when there are two addresses: "home" or e.g. "Gent"

    def as_dict(self) -> dict:
        out = {"km": round(self.km, 1), "min": round(self.minutes), "cost": round(self.cost, 2),
               "approx": self.approx}
        if self.origin:
            out["from"] = self.origin
        return out


@dataclass
class DrivingCosts:
    km_per_liter: float = 16.0
    fuel_price: float = 2.108  # € per liter
    round_trip: bool = True
    extra_per_km: float = 0.0  # optional wear and tear per km

    def cost(self, km_one_way: float) -> float:
        km = km_one_way * (2 if self.round_trip else 1)
        return km / self.km_per_liter * self.fuel_price + km * self.extra_per_km


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _town(address: str) -> str:
    part = [p.strip() for p in address.split(",") if p.strip() and not _ABROAD.fullmatch(p.strip())]
    last = part[-1] if part else address
    return re.sub(r"^\d{4}\s?[A-Za-z]{2}\b|^\d{4,5}\b", "", last).strip()


def _point(wkt: str) -> tuple[float, float] | None:
    m = re.match(r"POINT\(([-\d.]+) ([-\d.]+)\)", wkt or "")
    return (float(m.group(2)), float(m.group(1))) if m else None  # WKT is "lon lat"


# Belgian flat numbers: "Larochelaan 14-0202", "14/2", "14 bus 2" -> "14" (the box doesn't matter for driving)
_BOX = re.compile(r"\b(\d+[a-zA-Z]?)\s*(?:[-/]\s*\d{1,4}|\s+bus\s+\w{1,4})\b", re.I)


def town_label(address: str) -> str:
    """'Raymonde de Larochelaan 14, 9051 Gent, België' -> 'Gent'."""
    return _town(address) or "home 2"


def geocode(http, address: str, user_agent: str = "auction-deals-bot") -> tuple[float, float] | None:
    """(lat, lon) for an address, or None."""
    address = _BOX.sub(r"\1", address)
    if not _ABROAD.search(address):
        try:
            data = http.json(f"{PDOK}?{urlencode({'q': address, 'rows': 1, 'fl': 'centroide_ll,weergavenaam,type'})}")
            docs = (data.get("response") or {}).get("docs") or []
            if docs:
                found = docs[0]
                town = normalize(_town(address))
                # PDOK only knows the Netherlands: make sure it found the right town
                if not town or f" {town} " in f" {normalize(found.get('weergavenaam') or '')} ":
                    point = _point(found.get("centroide_ll") or "")
                    if point:
                        return point
        except Exception as e:
            log.warning("PDOK lookup failed for %r: %s", address, e)
    try:
        params = {"q": address, "format": "jsonv2", "limit": 1, "countrycodes": "nl,be,de,lu"}
        data = http.json(f"{NOMINATIM}?{urlencode(params)}", headers={"User-Agent": user_agent})
        if isinstance(data, list) and data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        log.warning("Nominatim lookup failed for %r: %s", address, e)
    return None


def route_table(http, home: tuple[float, float], places: list[tuple[float, float]]) -> list[tuple[float, float] | None]:
    """(km, minutes) from home to each place by car, in one request per 40 places."""
    out: list[tuple[float, float] | None] = []
    for start in range(0, len(places), 40):
        chunk = places[start:start + 40]
        coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in [home] + chunk)
        data = http.json(f"{OSRM}{coords}?sources=0&annotations=distance,duration")
        if data.get("code") != "Ok":
            raise RuntimeError(f"route planner answered {data.get('code')}")
        dist = (data.get("distances") or [[]])[0]
        dur = (data.get("durations") or [[]])[0]
        for i in range(1, len(chunk) + 1):
            d = dist[i] if i < len(dist) else None
            t = dur[i] if i < len(dur) else None
            out.append((d / 1000, t / 60) if d is not None and t is not None else None)
    return out


def parse_fuel_price(html: str) -> tuple[float, str | None] | None:
    """(€ per liter, valid-from date) for Euro 95 E10 from a page listing the Belgian maximum prices."""
    text = re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" "))
    m = re.search(r"(?:95\s*RON|Super\s*95|Euro\s*95)\s*[-–(]?\s*\(?E\s?10\)?\D{0,40}?(\d[.,]\d{2,4})", text, re.I)
    if not m:
        return None
    price = float(m.group(1).replace(",", "."))
    if not 0.8 < price < 4:
        return None
    since = re.search(r"(\d{4}-\d{2}-\d{2}|\d{2}[/-]\d{2}[/-]\d{2,4})", text[m.end():m.end() + 80])
    return price, since.group(1) if since else None


class TripPlanner:
    """Works out a Trip for every pickup address of the matched lots."""

    def __init__(self, http, cache: dict, now: datetime, costs: DrivingCosts, home_address: str | None,
                 user_agent: str = "auction-deals-bot", second_address: str | None = None):
        self.http = http
        self.cache = cache  # address -> {"lat", "lon", "at"} or {"fail": true, "at"}; public, holds no home data
        self.now = now
        self.costs = costs
        self.home_address = (home_address or "").strip()
        self.second_address = (second_address or "").strip()
        self.user_agent = user_agent
        self.home: tuple[float, float] | None = None
        self.error: str | None = None
        self.trips: dict[str, Trip] = {}

    def _place(self, address: str, latlon: tuple[float, float] | None) -> tuple[float, float] | None:
        if latlon:
            return latlon
        entry = self.cache.get(address)
        if entry and entry.get("lat") is not None:
            return entry["lat"], entry["lon"]
        if entry and entry.get("fail") and self.now - datetime.fromisoformat(entry["at"]) < timedelta(days=7):
            return None
        found = geocode(self.http, address, self.user_agent)
        self.cache[address] = ({"lat": found[0], "lon": found[1], "at": self.now.isoformat()} if found
                               else {"fail": True, "at": self.now.isoformat()})
        return found

    def _trips_from(self, start: tuple[float, float], located: dict, origin: str | None) -> dict[str, Trip]:
        names = list(located)
        try:
            routes = route_table(self.http, start, [located[a] for a in names])
        except Exception as e:
            log.warning("route planner failed, using straight-line distances: %s", e)
            self.error = "route planner unavailable, distances are estimates"
            routes = [None] * len(names)
        out = {}
        for name, route in zip(names, routes):
            if route:
                km, minutes = route
                out[name] = Trip(km, minutes, self.costs.cost(km), origin=origin)
            else:
                km = haversine_km(start, located[name]) * ROAD_FACTOR
                out[name] = Trip(km, km / FALLBACK_KMH * 60, self.costs.cost(km), approx=True, origin=origin)
        return out

    def plan(self, places: dict[str, tuple[float, float] | None]) -> dict[str, Trip]:
        """places: address -> coordinates if the site gave them. Returns address -> Trip (from the closer
        of the two addresses, when there are two)."""
        if not (self.home_address or self.second_address) or not places:
            return {}
        starts = []  # (label, coordinates)
        two = bool(self.home_address and self.second_address)
        for address, label, secret in ((self.home_address, "home", "HOME_ADDRESS"),
                                       (self.second_address, town_label(self.second_address), "HOME_ADDRESS_2")):
            if not address:
                continue
            found = geocode(self.http, address, self.user_agent)
            if found:
                starts.append((label if two else None, found))
            else:
                self.error = f"your {secret} could not be found on the map"
                log.warning(self.error)
        if not starts:
            return {}
        self.home = starts[0][1]
        located = {a: p for a, p in ((a, self._place(a, ll)) for a, ll in places.items()) if p}
        for origin, start in starts:
            for name, trip in self._trips_from(start, located, origin).items():
                if name not in self.trips or trip.km < self.trips[name].km:
                    self.trips[name] = trip
        return self.trips

    def prune(self, days: int = 60) -> None:
        for key in [k for k, v in self.cache.items()
                    if self.now - datetime.fromisoformat(v["at"]) > timedelta(days=days)]:
            del self.cache[key]


def fuel_price(http, cache: dict, now: datetime, configured, fallback: float) -> tuple[float, str]:
    """(price, where it came from). `configured` is a number, or "auto" for the Belgian maximum price."""
    if isinstance(configured, (int, float)) and configured > 0:
        return float(configured), "your setting"
    saved = cache.get("fuel") or {}
    if saved.get("at") and now - datetime.fromisoformat(saved["at"]) < timedelta(hours=20):
        return saved["price"], saved.get("source", "")
    try:
        found = parse_fuel_price(http.text(FUEL_URL))
        if found:
            price, since = found
            source = "Belgian maximum price Euro 95 E10" + (f" from {since}" if since else "")
            cache["fuel"] = {"price": price, "source": source, "at": now.isoformat()}
            return price, source
        log.warning("fuel price not found on %s", FUEL_URL)
    except Exception as e:
        log.warning("could not read the fuel price: %s", e)
    if saved.get("price") and now - datetime.fromisoformat(saved["at"]) < timedelta(days=14):
        return saved["price"], saved.get("source", "") + " (saved)"
    return fallback, "fallback in config.yml"
