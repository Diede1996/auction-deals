"""Reading and writing the repository files (config, watchlist, state)."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

WATCHLIST_HEADER = """\
# Your watchlist. Edit it here on GitHub or send commands to your Telegram bot (/help).
#
# settings:
#   target_return  the return you want on what you pay: 0.30 = 30% profit on bid + premium + VAT
#   min_profit     and at least this many euros profit per lot
#   resale_factor  you'll likely sell below the average asking price; 0.85 = 85% of the Marktplaats median
#
# items: one entry per thing you want to find
#   name               label used in messages and on the dashboard
#   keywords           search phrases; a lot matches when its title contains all words of one phrase
#   exclude            lots whose title contains any of these words are skipped
#   max_price          (optional) never suggest paying more than this in total (bid + premium + VAT)
#   market_price       (optional) your own resale value; skips the Marktplaats lookup
#   marktplaats_query  (optional) exact Marktplaats search to use for the resale value
#   min_profit         (optional) overrides the default above for this item
"""


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def save_watchlist(path: Path, data: dict) -> None:
    opts = {"sort_keys": False, "allow_unicode": True, "width": 100}
    body = (yaml.safe_dump({"settings": data.get("settings") or {}}, default_flow_style=False, **opts)
            + yaml.safe_dump({"items": data.get("items") or []}, default_flow_style=None, **opts))
    path.write_text(WATCHLIST_HEADER + "\n" + body, encoding="utf-8")


def load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("%s unreadable, starting fresh", path)
    return {}


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def dashboard_url(config: dict) -> str | None:
    """Configured URL, or the GitHub Pages address of the repository this runs in."""
    url = ((config.get("dashboard") or {}).get("url") or os.environ.get("DASHBOARD_URL") or "").strip()
    if url:
        return url
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        if name.lower() == f"{owner.lower()}.github.io":
            return f"https://{owner.lower()}.github.io/"
        return f"https://{owner.lower()}.github.io/{name}/"
    return None


def github_output(key: str, value: str) -> None:
    """Pass a value to later workflow steps (no-op outside GitHub Actions)."""
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")
