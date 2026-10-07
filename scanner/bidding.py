"""The lowest bid you can place now: what you'd really pay for a lot, not the current bid.

- No bids yet: the starting bid (the price the site shows).
- Bids: the current bid plus one bid step. Onlineveilingmeester and Plaats Je Bod give the step per lot;
  for the other sites it's estimated from the usual steps on Dutch and Belgian auction sites
  (config.yml: bidding.steps).
"""
from __future__ import annotations

from .models import Lot

# (from this amount, the step): below €20 +€1, from €20 +€5, from €100 +€10, ...
DEFAULT_STEPS = [(0, 1), (20, 5), (100, 10), (500, 25), (1000, 50), (5000, 100)]


def parse_steps(raw) -> list[tuple[float, float]]:
    try:
        steps = sorted((float(a), float(b)) for a, b in raw or [])
    except (TypeError, ValueError):
        steps = []
    return steps or [(float(a), float(b)) for a, b in DEFAULT_STEPS]


def bid_step(amount: float, steps: list[tuple[float, float]] | None = None) -> float:
    step = 1.0
    for start, size in steps or parse_steps(None):
        if amount >= start:
            step = size
    return step


def fill_next_bids(lots, steps: list[tuple[float, float]] | None = None) -> None:
    """Set lot.next_bid where the site didn't: the starting bid when nobody has bid yet, else the current bid
    plus one (estimated) step. Unknown number of bids with a price (HNVI): counted as bid on, to be safe.
    Troostwijk lots from emails are left alone (their bid is old anyway)."""
    for lot in lots:
        lot = lot[1] if isinstance(lot, tuple) else lot
        if lot.next_bid is not None or lot.bid_from_email or lot.current_bid is None:
            continue
        if lot.bids == 0:
            lot.next_bid = lot.current_bid
        else:
            lot.next_bid = round(lot.current_bid + bid_step(lot.current_bid, steps), 2)
            lot.step_estimated = True


def next_bid_of(lot: Lot) -> float:
    """The bid the money is worked out for."""
    if lot.next_bid is not None:
        return lot.next_bid
    return lot.current_bid or 0.0
