"""Match auction lots against the watchlist."""
from __future__ import annotations

import re
from datetime import date

from .models import Lot, WatchItem
from .util import AMS, normalize, phrase_in, token_in


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


def bankruptcy_matcher(keywords: list[str], extra_words: list[str] | None = None):
    """Which auctions to read: is_bankruptcy(name, description="").
    - `keywords` count anywhere in the name or description, also inside a word ("boedel" finds "inboedel").
    - `extra_words` (IT auctions) only count in the name, as a word or the start of one: "it" finds
      "IT en multimedia" but not "uit", and "bieden via uw computer" in a description doesn't count."""
    pattern = re.compile("|".join(re.escape(normalize(k)) for k in keywords if normalize(k)) or r"(?!)")
    extra = [normalize(w) for w in extra_words or [] if normalize(w)]

    def is_bankruptcy(name: str, description: str = "") -> bool:
        norm = normalize(name)
        if pattern.search(norm) or pattern.search(normalize(description)):
            return True
        return any(token_in(w, norm) for w in extra)

    return is_bankruptcy


def auction_filter(config: dict):
    """The auction filter from config.yml: auction_keywords, plus extra_auctions.words when it's on."""
    keywords = config.get("auction_keywords") or config.get("bankruptcy_keywords") or ["faillissement", "curator"]
    extra = config.get("extra_auctions") or {}
    words = [str(w) for w in extra.get("words") or []] if extra.get("enabled", True) is not False else []
    return bankruptcy_matcher(keywords, words)


def all_auctions_trial(config: dict, now) -> dict | None:
    """The trial with every auction (config.yml all_auctions): {"until": date, "max_per_site": n} while it's on and
    hasn't ended, else None. Lots of auctions that aren't a bankruptcy, closure, estate, Domeinen or IT auction are
    read too and labelled "regular auction"."""
    trial = config.get("all_auctions") or {}
    if trial.get("enabled", False) is not True:
        return None
    until = trial.get("until")
    if until is not None:
        try:
            until = until if isinstance(until, date) else date.fromisoformat(str(until))
        except ValueError:
            until = None
        if until and now.astimezone(AMS).date() > until:
            return None
    return {"until": until, "max_per_site": int(trial.get("max_auctions_per_site", 0) or 0)}
