"""Match auction lots against the watchlist."""
from __future__ import annotations

import re

from .models import Lot, WatchItem
from .util import normalize, phrase_in, token_in


def matches(item: WatchItem, title: str) -> bool:
    text = normalize(title)
    if not any(phrase_in(k, text) for k in item.keywords):
        return False
    return not any(token_in(x, text) for x in item.exclude)


def match_lots(items: list[WatchItem], lots: list[Lot]) -> list[tuple[WatchItem, Lot]]:
    """Each lot is assigned to the first watchlist item it matches."""
    out = []
    for lot in lots:
        for item in items:
            if matches(item, lot.title):
                out.append((item, lot))
                break
    return out


def search_terms(items: list[WatchItem]) -> list[str]:
    """Distinct phrases to feed to sites that have a search box (Troostwijk)."""
    seen, out = set(), []
    for item in items:
        for k in item.keywords:
            key = normalize(k)
            if key and key not in seen:
                seen.add(key)
                out.append(k.strip())
    return out


def bankruptcy_matcher(keywords: list[str]):
    pattern = re.compile("|".join(re.escape(normalize(k)) for k in keywords if normalize(k)))

    def is_bankruptcy(text: str) -> bool:
        return bool(pattern.search(normalize(text)))

    return is_bankruptcy
