"""Look up a lot's resale value on Marktplaats, politely.

identify.plan_for() decides what to search for: the exact model when the lot title has a type number
("samsung s27c366eau"), otherwise a rough comparison with similar items ("monitor lenovo").
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timedelta

from . import marktplaats
from .http import BlockedError
from .identify import SearchPlan, is_brand, plan_for
from .marktplaats import PriceEstimate
from .models import Lot, WatchItem

log = logging.getLogger(__name__)

# Bump when what counts as a comparable listing changes: saved prices from older rules are checked again
# (and still shown as a fallback until they are).
RULES_VERSION = 2


def result_kind(plan: SearchPlan, rule_groups: list) -> str:
    """"exact" when the listings compared name the same model; "general" for a rough comparison.
    A general search that still pins a model ("jura e8", "ipad air 5", "playstation 5 slim") counts as exact:
    a word with a number in it that isn't a brand ("dsquared2") or a category."""
    if plan.kind != "general" or plan.rough:
        return plan.kind
    words = [group[0] for group in rule_groups if len(group) == 1 and not is_brand(group[0]) and group[0] != "5g"]
    return "exact" if any(ch.isdigit() for w in words for ch in w) else "general"


class PriceFinder:
    """- At most two Marktplaats searches per lot: the most specific one and, if that finds too few
      comparable listings, a broader or differently written one (three when the first one names the lot's
      storage or memory: "galaxy a12 64gb"). Rules are tried on the combined results.
    - A lot's price is reused for `cache_days`; identical searches in one run are made only once.
    - When Marktplaats blocks us, no further requests are made this run and lots get their last
      known price (up to `stale_days` old) instead.
    """

    def __init__(self, http, cache: dict, now, cache_days: float = 3, stale_days: float = 14,
                 min_listings: int = 4, max_lookups: int = 40, enabled: bool = True, min_listings_exact: int = 2):
        self.http = http
        self.cache = cache
        self.now = now
        self.cache_days = cache_days
        self.stale_days = stale_days
        self.min_listings = min_listings
        self.min_listings_exact = min_listings_exact
        self.lookups_left = max_lookups
        self.enabled = enabled
        self.blocked = False
        self.errors = 0
        self.last_error: str | None = None
        self.lookups = 0  # searches actually sent to Marktplaats
        self.stale_used = 0  # lots shown with an older saved price
        self._results: dict[str, list] = {}

    def _age(self, entry: dict) -> timedelta:
        return self.now - datetime.fromisoformat(entry["at"])

    def _saved(self, entry: dict | None) -> PriceEstimate | None:
        if entry and entry.get("est") and self._age(entry) <= timedelta(days=self.stale_days):
            self.stale_used += 1
            return PriceEstimate(**entry["est"])
        return None

    def _search(self, query: str) -> list[dict]:
        if query not in self._results:
            self.lookups_left -= 1
            self.lookups += 1
            self._results[query] = marktplaats.search(self.http, query)
        return self._results[query]

    def for_lot(self, item: WatchItem, lot: Lot, lookup: bool = True) -> PriceEstimate | None:
        """lookup=False: only a saved price, no new Marktplaats search (lots too far away to collect)."""
        if not self.enabled or item.market_price is not None:
            return None
        plan = plan_for(item, lot)
        if not plan:  # title has nothing beyond a brand or category: no reliable comparison possible
            return None
        key = marktplaats.cache_key(f"{plan.kind}:{plan.searches[0]}", item.exclude)
        entry = self.cache.get(key)
        if entry and self._age(entry) <= timedelta(days=self.cache_days) and entry.get("v") == RULES_VERSION:
            return PriceEstimate(**entry["est"]) if entry.get("est") else None
        if self.blocked or self.lookups_left <= 0 or not lookup:
            return self._saved(entry)

        min_count = self.min_listings_exact if plan.exact else self.min_listings
        results: list[dict] = []
        found = None
        # two searches; three when the first names the lot's options ("galaxy a12 64gb", then the plan's own)
        for i, query in enumerate(plan.searches[:3 if plan.options else 2]):
            if i and self.lookups_left <= 0 and query not in self._results:
                break
            try:
                results = results + self._search(query)
            except BlockedError as e:
                self.blocked = True
                self.last_error = str(e)
                return self._saved(entry)
            except Exception as e:
                self.errors += 1
                self.last_error = str(e)
                log.warning("marktplaats lookup failed for %r: %s", query, e)
                return self._saved(entry)
            found = marktplaats.best_estimate(results, plan.rules, item.exclude, min_count)
            if found:
                break
        est = None
        if found:
            est, rule = found
            est.as_of = self.now.isoformat()
            est.kind = result_kind(plan, rule.groups)
            est.model = plan.model
            est.search = plan.searches[0] if plan.kind != "general" else rule.label
        self.cache[key] = {"at": self.now.isoformat(), "est": asdict(est) if est else None, "v": RULES_VERSION}
        return est

    def prune(self) -> None:
        """Forget saved prices that are too old to fall back on."""
        for key in [k for k, v in self.cache.items() if self._age(v) > timedelta(days=self.stale_days)]:
            del self.cache[key]
