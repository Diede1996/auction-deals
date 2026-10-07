"""Turn a matched lot into a buy decision: total cost, margin, suggested maximum bid.

    you pay     = (bid x (1 + premium) + fixed fees) x (1 + VAT) + driving costs to the pickup address
    sale price  = Marktplaats median x resale_factor ("I sell at X% of the median") - selling costs
    margin      = sale price - you pay, also shown as a % of what you pay
    max bid     = highest bid that still leaves at least min_margin, as a % of what you pay:
                  you pay at most sale price / (1 + min_margin)
                  (and keeps the total <= your own max_price, if you set one for the item)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .bidding import next_bid_of
from .marktplaats import PriceEstimate
from .models import Lot, WatchItem


@dataclass
class Fees:
    premium: float = 0.18  # buyer's premium (opgeld) as a fraction of the bid
    vat: float = 0.21  # VAT charged on bid + premium
    fixed: float = 0.0  # fixed costs per lot, excl. VAT

    @classmethod
    def from_dict(cls, d: dict | None) -> "Fees":
        d = d or {}
        return cls(premium=float(d.get("premium", 0.18)), vat=float(d.get("vat", 0.21)),
                   fixed=float(d.get("fixed_fee", 0.0)))


@dataclass
class Settings:
    min_margin: float = 0.30  # the max bid always leaves at least this margin: profit / what you pay (0.3 = 30%)
    resale_factor: float = 0.85  # you sell at 85% of the Marktplaats median asking price
    selling_costs: float = 0.0  # e.g. shipping or listing costs per sale
    include_trip: bool = True  # count the drive to the pickup address as a cost

    @classmethod
    def from_dict(cls, d: dict | None, include_trip: bool = True) -> "Settings":
        d = d or {}  # older watchlists may still have target_return / min_profit (in €); those are ignored
        return cls(min_margin=float(d.get("min_margin", 0.30)), resale_factor=float(d.get("resale_factor", 0.85)),
                   selling_costs=float(d.get("selling_costs", 0)), include_trip=include_trip)


@dataclass
class Verdict:
    is_deal: bool  # the current bid is still below the suggested max bid
    reason: str
    bid: float
    total_cost: float
    resale: float | None = None  # expected sale price
    profit: float | None = None  # margin in euros at the current bid
    margin: float | None = None  # margin as a share of what you pay, at the current bid
    max_bid: float | None = None  # suggested maximum (auto)bid, whole euros
    max_total: float | None = None  # what you'd pay in total at the max bid
    profit_at_max: float | None = None
    margin_at_max: float | None = None


def lot_rates(fees: Fees, lot: Lot) -> tuple[float, float]:
    """(premium, vat) for this lot: the lot's own rates if the site reported them, else the site's."""
    premium = lot.premium if lot.premium is not None else fees.premium
    vat = lot.vat if lot.vat is not None else fees.vat
    return premium, vat


def total_cost(bid: float, fees: Fees, lot: Lot, trip: float = 0.0) -> float:
    premium, vat = lot_rates(fees, lot)
    return (bid * (1 + premium) + fees.fixed + lot.extra_fee) * (1 + vat) + trip


def bid_for_total(total: float, fees: Fees, lot: Lot, trip: float = 0.0) -> float:
    premium, vat = lot_rates(fees, lot)
    return ((total - trip) / (1 + vat) - fees.fixed - lot.extra_fee) / (1 + premium)


def market_value(item: WatchItem, estimate: PriceEstimate | None, units: int = 1) -> float | None:
    """Resale value of the whole lot: your own price for the item, else the Marktplaats median, times the
    number of items when the title says there are a few ("2 x ...")."""
    unit = item.market_price if item.market_price is not None else (estimate.median if estimate else None)
    return unit * units if unit is not None else None


def evaluate(item: WatchItem, lot: Lot, fees: Fees, settings: Settings,
             estimate: PriceEstimate | None, units: int = 1) -> Verdict:
    bid = next_bid_of(lot)  # what you'd have to bid now, not the current bid
    trip = (lot.trip_cost or 0.0) if settings.include_trip else 0.0
    cost = total_cost(bid, fees, lot, trip)
    min_margin = item.min_margin if item.min_margin is not None else settings.min_margin

    market = market_value(item, estimate, units)
    resale = profit = margin = None
    caps = []
    if market:
        resale = market * settings.resale_factor - settings.selling_costs
        profit = resale - cost
        margin = profit / cost if cost > 0 else None
        caps.append(resale / (1 + max(0.0, min_margin)))
    if item.max_price is not None:
        caps.append(item.max_price)
    if not caps:
        return Verdict(False, "no resale value found", bid, cost)

    max_total = min(caps)
    max_bid = max(0, math.floor(bid_for_total(max_total, fees, lot, trip)))
    pay_at_max = total_cost(max_bid, fees, lot, trip)
    profit_at_max = resale - pay_at_max if resale is not None else None
    margin_at_max = profit_at_max / pay_at_max if profit_at_max is not None and pay_at_max > 0 else None
    ok = bid <= max_bid
    if lot.bid_from_email:  # the email's bid is old (usually the starting bid): only the lot page knows
        ok = False
        reason = f"bid up to €{max_bid}; check the current bid on the lot page" if max_bid > 0 else \
            "not profitable at any price"
    elif ok:
        reason = f"room to bid up to €{max_bid}"
    elif max_bid <= 0:
        reason = "not profitable at any price"
    else:
        reason = f"the next bid (€{bid:g}) is above the suggested max €{max_bid}"
    return Verdict(ok, reason, bid, cost, resale, profit, margin, float(max_bid), round(pay_at_max, 2),
                   profit_at_max, margin_at_max)
