"""Plain data classes shared by all modules."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Auction:
    site: str
    auction_id: str
    title: str
    url: str
    description: str = ""
    kind: str = ""  # type label reported by the site, e.g. "FAILLISEMENT" or "Executie veiling"
    closes_at: datetime | None = None
    pickup: str | None = None  # pickup address ("ophaallocatie"), e.g. "Produktieweg 9, 8304AV Emmeloord"
    pickup_when: str | None = None  # pickup day(s) as shown to you, e.g. "do 1 okt 10:00–12:00"
    pickup_latlon: tuple[float, float] | None = None  # when the site gives coordinates
    delivery: bool = False  # the auction delivers (bezorgveiling): no trip needed
    town: str = ""  # where the auction is, when the site only names the town


@dataclass
class Lot:
    site: str
    lot_id: str
    title: str
    url: str
    current_bid: float | None  # EUR, excluding premium and VAT
    closes_at: datetime | None  # timezone-aware
    auction_title: str = ""
    image: str | None = None
    location: str | None = None
    bids: int | None = None
    extra_fee: float = 0.0  # fixed per-lot costs reported by the site (excl. VAT)
    premium: float | None = None  # per-lot buyer's premium if the site reports it (0.16 = 16%)
    vat: float | None = None  # per-lot VAT on bid + premium if it differs from the site default (0 = margin scheme)
    pickup: str | None = None  # pickup address, usually the same for the whole auction
    pickup_when: str | None = None
    pickup_latlon: tuple[float, float] | None = None
    delivery: bool = False
    trip_cost: float | None = None  # fuel to drive to the pickup address and back (set by the scan)

    @property
    def key(self) -> str:
        return f"{self.site}:{self.lot_id}"

    def pickup_from(self, auction: "Auction") -> "Lot":
        """Copy the auction's pickup details onto this lot."""
        self.pickup = self.pickup or auction.pickup
        self.pickup_when = self.pickup_when or auction.pickup_when
        self.pickup_latlon = self.pickup_latlon or auction.pickup_latlon
        self.delivery = self.delivery or auction.delivery
        return self


@dataclass
class WatchItem:
    name: str
    keywords: list[str]
    exclude: list[str] = field(default_factory=list)
    max_price: float | None = None  # alert when total cost (incl. fees) is at or below this
    market_price: float | None = None  # manual resale value, skips the Marktplaats lookup
    marktplaats_query: str | None = None
    min_margin: float | None = None  # overrides the global setting (0.4 = 40%)

    @classmethod
    def from_dict(cls, d: dict) -> "WatchItem":
        def as_list(v):
            if v is None:
                return []
            if isinstance(v, str):
                return [v]
            return [str(x) for x in v]

        name = str(d.get("name") or "").strip()
        keywords = as_list(d.get("keywords")) or ([name] if name else [])
        return cls(
            name=name or keywords[0],
            keywords=keywords,
            exclude=as_list(d.get("exclude")),
            max_price=_num(d.get("max_price")),
            market_price=_num(d.get("market_price")),
            marktplaats_query=(str(d["marktplaats_query"]).strip() if d.get("marktplaats_query") else None),
            min_margin=_num(d.get("min_margin")),
        )

    def to_dict(self) -> dict:
        out: dict = {"name": self.name, "keywords": list(self.keywords)}
        if self.exclude:
            out["exclude"] = list(self.exclude)
        for k in ("max_price", "market_price", "marktplaats_query", "min_margin"):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        return out


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
