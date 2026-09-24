"""Turn a matched lot into a buy decision: total cost, margin, suggested maximum bid.

    you pay     = (bid x (1 + premium) + fixed fees) x (1 + VAT)
    sale price  = Marktplaats median x resale_factor ("I sell at X% of the median") - selling costs
    margin      = sale price - you pay, also shown as a % of what you pay
    max bid     = highest bid that still leaves at least min_profit
                  (and keeps the total <= your own max_price, if you set one for the item)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

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
    min_profit: float = 25.0  # the max bid always leaves at least this many euros profit
    resale_factor: float = 0.85  # you sell at 85% of the Marktplaats median asking price
    selling_costs: float = 0.0  # e.g. shipping or listing costs per sale

    @classmethod
    def from_dict(cls, d: dict | None) -> "Settings":
        d = d or {}  # older watchlists may still have target_return / min_margin; those are ignored
        return cls(min_profit=float(d.get("min_profit", 25)), resale_factor=float(d.get("resale_factor", 0.85)),
                   selling_costs=float(d.get("selling_costs", 0)))


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


def total_cost(bid: float, fees: Fees, lot: Lot) -> float:
    premium, vat = lot_rates(fees, lot)
    return (bid * (1 + premium) + fees.fixed + lot.extra_fee) * (1 + vat)


def bid_for_total(total: float, fees: Fees, lot: Lot) -> float:
    premium, vat = lot_rates(fees, lot)
    return (total / (1 + vat) - fees.fixed - lot.extra_fee) / (1 + premium)


def market_value(item: WatchItem, estimate: PriceEstimate | None) -> float | None:
    if item.market_price is not None:
        return item.market_price
    return estimate.median if estimate else None


def evaluate(item: WatchItem, lot: Lot, fees: Fees, settings: Settings,
             estimate: PriceEstimate | None) -> Verdict:
    bid = lot.current_bid or 0.0
    cost = total_cost(bid, fees, lot)
    min_profit = item.min_profit if item.min_profit is not None else settings.min_profit

    market = market_value(item, estimate)
    resale = profit = margin = None
    caps = []
    if market:
        resale = market * settings.resale_factor - settings.selling_costs
        profit = resale - cost
        margin = profit / cost if cost > 0 else None
        caps.append(resale - min_profit)
    if item.max_price is not None:
        caps.append(item.max_price)
    if not caps:
        return Verdict(False, "no resale value found", bid, cost)

    max_total = min(caps)
    max_bid = max(0, math.floor(bid_for_total(max_total, fees, lot)))
    pay_at_max = total_cost(max_bid, fees, lot)
    profit_at_max = resale - pay_at_max if resale is not None else None
    margin_at_max = profit_at_max / pay_at_max if profit_at_max is not None and pay_at_max > 0 else None
    ok = bid < max_bid
    if ok:
        reason = f"room to bid up to €{max_bid}"
    elif max_bid <= 0:
        reason = "not profitable at any price"
    else:
        reason = f"current bid is above the suggested max €{max_bid}"
    return Verdict(ok, reason, bid, cost, resale, profit, margin, float(max_bid), round(pay_at_max, 2),
                   profit_at_max, margin_at_max)
