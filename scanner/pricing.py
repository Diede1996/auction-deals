"""Pick a Marktplaats search for a specific lot and look up its resale value.

The watchlist keyword alone is often too broad ("macbook" covers €150 to €3.000 models), so the
lot title is used to make the search more specific: the matched keyword plus up to three
distinctive words from the title ("thinkpad" + "lenovo t580 i5"). If that finds too few
comparable listings, words are dropped one at a time until enough listings are found.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timedelta

from . import marktplaats
from .http import BlockedError
from .marktplaats import PriceEstimate
from .models import Lot, WatchItem
from .util import normalize, phrase_in

log = logging.getLogger(__name__)

NOISE = set("""
met zonder en de het een van voor in op tot aan bij of als uit incl inclusief excl exclusief
with and the for of to from new nieuw nieuwe gebruikt used zgan zga ongebruikt
partij diverse div divers kavel lot set stuks stuk st pcs x cm mm m kg gr g l ltr liter v w watt
gb tb inch type model merk bj bwjr bouwjaar ca circa oa etc zie foto fotos afbeelding afbeeldingen
zwart wit grijs zilver blauw rood groen black white grey gray silver blue red
""".split())


def useful_tokens(title: str) -> list[tuple[int, str]]:
    """Distinctive words of a lot title with their position, e.g. model numbers ("t580", "v15", "13")."""
    raw = normalize(title).split()
    out, seen = [], set()
    for i, tok in enumerate(raw):
        if tok in NOISE or tok in seen:
            continue
        if tok.isdigit():
            prev = raw[i - 1] if i else ""
            nxt = raw[i + 1] if i + 1 < len(raw) else ""
            # keep "iphone 13" / "ts 55", drop quantities and sizes like "2 x", "60 cm", "15,3"
            if nxt in NOISE or (len(tok) < 3 and not (prev.isalpha() and prev not in NOISE)):
                continue
        elif len(tok) < 2:
            continue
        seen.add(tok)
        out.append((i, tok))
    return out


def candidate_queries(item: WatchItem, lot: Lot, extra_words: int = 3) -> list[str]:
    """Most specific search first. Words after the keyword in the title ("iphone" -> "13 pro") matter
    more than words before it (usually the brand), so those are kept longest."""
    if item.marktplaats_query:
        return [item.marktplaats_query]
    text = normalize(lot.title)
    base = next((k for k in item.keywords if phrase_in(k, text)), item.keywords[0])
    base_tokens = normalize(base).split()
    raw = text.split()
    pos = next((i for i, t in enumerate(raw) if base_tokens and t.startswith(base_tokens[0])), -1)
    tokens = [(i, t) for i, t in useful_tokens(lot.title) if t not in base_tokens]
    after = [t for i, t in tokens if i > pos]
    before = [t for i, t in tokens if i <= pos]
    extras = (after + before)[:extra_words]
    queries = []
    for n in range(len(extras), -1, -1):
        q = " ".join(base_tokens + extras[:n])
        if q not in queries:
            queries.append(q)
    return queries


class PriceFinder:
    """Looks up resale values politely.

    - At most two Marktplaats searches per lot: the most specific query, and if that finds too few
      comparable listings, the keyword plus one word. Dropping words happens on those same results.
    - A lot's price is reused for `cache_days`; identical searches in one run are made only once.
    - When Marktplaats blocks us, no further requests are made this run and lots get their last
      known price (up to `stale_days` old) instead.
    """

    def __init__(self, http, cache: dict, now, cache_days: float = 3, stale_days: float = 14,
                 min_listings: int = 4, max_lookups: int = 40, enabled: bool = True):
        self.http = http
        self.cache = cache
        self.now = now
        self.cache_days = cache_days
        self.stale_days = stale_days
        self.min_listings = min_listings
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

    def for_lot(self, item: WatchItem, lot: Lot) -> PriceEstimate | None:
        if not self.enabled or item.market_price is not None:
            return None
        queries = candidate_queries(item, lot)
        key = marktplaats.cache_key(queries[0], item.exclude)
        entry = self.cache.get(key)
        if entry and self._age(entry) <= timedelta(days=self.cache_days):
            return PriceEstimate(**entry["est"]) if entry.get("est") else None
        if self.blocked or self.lookups_left <= 0:
            return self._saved(entry)

        plan = [queries[0]]
        if len(queries) > 1:
            plan.append(queries[-2] if len(queries) > 2 else queries[-1])
        est = None
        for i, query in enumerate(plan):
            if i and self.lookups_left <= 0 and query not in self._results:
                break
            try:
                listings = self._search(query)
            except BlockedError as e:
                self.blocked = True
                self.last_error = str(e)
                return self._saved(entry)
            except Exception as e:
                self.errors += 1
                self.last_error = str(e)
                log.warning("marktplaats lookup failed for %r: %s", query, e)
                return self._saved(entry)
            est = marktplaats.best_estimate(listings, queries[queries.index(query):], item.exclude,
                                            self.min_listings)
            if est:
                break
        if est:
            est.as_of = self.now.isoformat()
        self.cache[key] = {"at": self.now.isoformat(), "est": asdict(est) if est else None}
        return est

    def prune(self) -> None:
        """Forget saved prices that are too old to fall back on."""
        for key in [k for k, v in self.cache.items() if self._age(v) > timedelta(days=self.stale_days)]:
            del self.cache[key]
