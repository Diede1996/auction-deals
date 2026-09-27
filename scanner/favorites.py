"""Favorite lots, starred on the dashboard.

The dashboard is a static page, so it keeps favorites in one GitHub issue of this repository: the page
edits that issue with a token you create once (it can only touch issues of this repository), and the
bot reads it to remind you before a favorite closes. The issue body holds the list as JSON:

    <!-- auction-deals:favorites -->
    ...a readable list...
    ```json
    [{"key": "hnvi:196601", "title": "...", "url": "https://www.hnvi.nl/...", "closes": "2026-10-05T19:30:00+02:00", ...}]
    ```
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

from .util import from_iso

log = logging.getLogger(__name__)

MARKER = "<!-- auction-deals:favorites -->"
API = "https://api.github.com"
ALLOWED_HOSTS = {"www.hnvi.nl", "hnvi.nl", "www.proveiling.nl", "proveiling.nl", "www.plaatsjebod.nl",
                 "plaatsjebod.nl", "onlineveilingmeester.nl", "www.onlineveilingmeester.nl",
                 "www.troostwijkauctions.com"}
_KEY_RE = re.compile(r"^[a-z]{2,30}:[A-Za-z0-9_.-]{1,80}$")
_JSON_RE = re.compile(r"```json\s*(\[.*?\])\s*```", re.S)


def parse_body(body: str) -> list[dict]:
    """The favorites in an issue body, keeping only well-formed entries that link to an auction site."""
    m = _JSON_RE.search(body or "")
    if not m:
        return []
    try:
        raw = json.loads(m.group(1))
    except ValueError:
        return []
    out, seen = [], set()
    for f in raw if isinstance(raw, list) else []:
        if not isinstance(f, dict):
            continue
        key, url = str(f.get("key") or ""), str(f.get("url") or "")
        parsed = urlparse(url)
        if not _KEY_RE.match(key) or key in seen or parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
            continue
        seen.add(key)
        clean = {"key": key, "url": url, "title": str(f.get("title") or key)[:200]}
        for k in ("site", "siteName", "auction", "item", "closes", "added", "pickup"):
            if isinstance(f.get(k), str):
                clean[k] = f[k][:200]
        for k in ("bid", "maxBid"):
            if isinstance(f.get(k), (int, float)):
                clean[k] = f[k]
        out.append(clean)
    return out


class FavoritesStore:
    """Reads the favorites issue with the workflow's own GITHUB_TOKEN (read access to issues)."""

    def __init__(self, http, repo: str | None = None, token: str | None = None):
        self.http = http
        self.repo = repo if repo is not None else os.environ.get("GITHUB_REPOSITORY", "")
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN", "")
        self.issue: int | None = None
        self.error: str | None = None

    @property
    def owner(self) -> str:
        return self.repo.split("/", 1)[0].lower() if "/" in self.repo else ""

    def load(self) -> list[dict]:
        if "/" not in self.repo:
            return []
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            issues = self.http.json(f"{API}/repos/{self.repo}/issues?state=open&per_page=100&creator={self.owner}",
                                    headers=headers)
        except Exception as e:
            self.error = str(e)
            log.warning("could not read the favorites issue: %s", e)
            return []
        mine = [i for i in issues if isinstance(i, dict) and MARKER in (i.get("body") or "")
                and str((i.get("user") or {}).get("login", "")).lower() == self.owner]
        if not mine:
            return []
        issue = min(mine, key=lambda i: i.get("number", 0))
        self.issue = issue.get("number")
        return parse_body(issue.get("body") or "")


def closing(fav: dict, lots_by_key: dict[str, dict]) -> datetime | None:
    """Latest known closing time: from today's scan if the lot is in it, else the time saved with the star."""
    lot = lots_by_key.get(fav["key"])
    return from_iso((lot or {}).get("closes") or fav.get("closes"))


def closing_between(favs: list[dict], lots_by_key: dict[str, dict], start: datetime, end: datetime) -> list[tuple[dict, datetime]]:
    out = []
    for f in favs:
        c = closing(f, lots_by_key)
        if c and start < c <= end:
            out.append((f, c))
    return sorted(out, key=lambda x: x[1])


def due_reminders(favs: list[dict], lots_by_key: dict[str, dict], now: datetime, minutes: int,
                  reminded: dict) -> list[tuple[dict, datetime]]:
    """Favorites closing within `minutes` that haven't had their reminder yet."""
    due = []
    for f, c in closing_between(favs, lots_by_key, now, now + timedelta(minutes=minutes)):
        if reminded.get(f["key"]) != c.isoformat():
            due.append((f, c))
    return due
