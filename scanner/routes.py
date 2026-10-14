"""One drive past several pickup addresses close to each other on the same pickup day.

Lots at the same address already share a trip (scan.share_trips). This looks one step further: Veghel and
Uden are 15 km apart, so two lots in Veghel and one in Uden on Monday are one drive of three lots, not two
drives that each fall short of your trip rules. The route is estimated: home -> the nearest address -> the
next nearest one ... -> the furthest one -> home, with the legs between addresses as straight lines x 1.3
at 75 km/h (no extra route planner requests).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .geo import FALLBACK_KMH, ROAD_FACTOR, haversine_km


@dataclass
class Combo:
    day: str  # pickup day, "Mon 19 Oct"
    places: list[tuple[str, int, float]]  # (address, lots worth collecting there, minutes one way), nearest first
    minutes: float  # the whole drive: there, past every address, and back
    separate: float  # the same lots as separate drives, there and back each
    keys: set[str] = field(default_factory=set)  # lots collected on this drive

    @property
    def lots(self) -> int:
        return sum(n for _, n, _ in self.places)

    @property
    def one_way(self) -> float:
        """Half the drive: what the trip rules compare with ("up to 90 minutes one way")."""
        return self.minutes / 2


def combined_pickups(groups: dict[tuple[str, str], list[tuple[str, bool]]], trips: dict, coords: dict,
                     within_km: float = 25) -> list[Combo]:
    """groups: (address, pickup day) -> [(lot key, worth collecting)]. trips: address -> Trip from home.
    coords: address -> (lat, lon). Addresses with a lot worth collecting that lie within within_km of each
    other (directly or via another such address) on the same day form one Combo."""
    if within_km <= 0:
        return []
    by_day: dict[str, dict[str, list[str]]] = {}
    for (place, day), lots in groups.items():
        worth = [key for key, ok in lots if ok]
        if day and worth and trips.get(place) and coords.get(place):
            by_day.setdefault(day, {})[place] = worth
    out = []
    for day, places in by_day.items():
        names = sorted(places, key=lambda p: trips[p].minutes)
        cluster_of = {p: i for i, p in enumerate(names)}
        for i, a in enumerate(names):  # single linkage: join clusters of addresses close to each other
            for b in names[i + 1:]:
                if cluster_of[a] != cluster_of[b] and haversine_km(coords[a], coords[b]) <= within_km:
                    old, new = cluster_of[b], cluster_of[a]
                    for p in names:
                        if cluster_of[p] == old:
                            cluster_of[p] = new
        clusters: dict[int, list[str]] = {}
        for p in names:
            clusters.setdefault(cluster_of[p], []).append(p)
        for members in clusters.values():
            if len(members) < 2:
                continue
            route = [members[0]]  # nearest to home first, then each time the closest address not visited yet
            rest = members[1:]
            legs = 0.0
            while rest:
                nxt = min(rest, key=lambda p: haversine_km(coords[route[-1]], coords[p]))
                legs += haversine_km(coords[route[-1]], coords[nxt]) * ROAD_FACTOR / FALLBACK_KMH * 60
                route.append(nxt)
                rest.remove(nxt)
            minutes = trips[route[0]].minutes + legs + trips[route[-1]].minutes
            separate = sum(2 * trips[p].minutes for p in members)
            out.append(Combo(day=day, places=[(p, len(places[p]), trips[p].minutes) for p in route],
                             minutes=minutes, separate=separate,
                             keys={k for p in members for k in places[p]}))
    return sorted(out, key=lambda c: (-c.lots, c.minutes))
