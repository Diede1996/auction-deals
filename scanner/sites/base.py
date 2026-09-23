from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from ..http import Http


@dataclass
class SiteContext:
    http: Http
    now: datetime
    is_bankruptcy: Callable[[str], bool]
    search_terms: list[str] = field(default_factory=list)  # phrases from the watchlist
    settings: dict = field(default_factory=dict)  # this site's block from config.yml
    cache: dict = field(default_factory=dict)  # persisted between runs (per site)
    max_pages: int = 25


_NEXT_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', re.S)


def next_data(html: str) -> dict:
    """Extract the Next.js __NEXT_DATA__ JSON blob from a page."""
    m = _NEXT_RE.search(html)
    if not m:
        raise ValueError("no __NEXT_DATA__ found (page layout changed or request was blocked)")
    return json.loads(m.group(1))
