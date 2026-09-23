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

    @property
    def key(self) -> str:
        return f"{self.site}:{self.lot_id}"


@dataclass
class WatchItem:
    name: str
    keywords: list[str]
    exclude: list[str] = field(default_factory=list)
    max_price: float | None = None  # alert when total cost (incl. fees) is at or below this
    market_price: float | None = None  # manual resale value, skips the Marktplaats lookup
    marktplaats_query: str | None = None
    min_profit: float | None = None  # overrides the global setting

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
            min_profit=_num(d.get("min_profit")),
        )

    def to_dict(self) -> dict:
        out: dict = {"name": self.name, "keywords": list(self.keywords)}
        if self.exclude:
            out["exclude"] = list(self.exclude)
        for k in ("max_price", "market_price", "marktplaats_query", "min_profit"):
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
