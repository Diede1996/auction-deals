"""Writes files into your PRIVATE repository (Diede1996/auction-private), where the dashboard page reads them
with the key you gave each device.

Troostwijk's terms (Gebruikersvoorwaarden 4.3, Algemene Voorwaarden 4.6) don't allow making their site, or
parts of it, publicly accessible, "for example via deeplinks". This repository and its dashboard are public, so
Troostwijk lots go to the private repository instead: the bot needs the PRIVATE_REPO_TOKEN secret for that
(a fine-grained token with Contents read/write on auction-private only). Without it they appear only in Telegram.
"""
from __future__ import annotations

import base64
import json
import logging
import os

import requests

log = logging.getLogger(__name__)
API = "https://api.github.com"


def repo_name(config: dict | None = None) -> str | None:
    name = ((config or {}).get("private_repo") or {}).get("name")
    if name:
        return name
    owner = (os.environ.get("GITHUB_REPOSITORY") or "").split("/")[0]
    return f"{owner}/auction-private" if owner else None


def token() -> str | None:
    return os.environ.get("PRIVATE_REPO_TOKEN") or None


def put_json(path: str, data, message: str, config: dict | None = None, session=None) -> bool:
    """Create or replace one JSON file in the private repository. False (and a log line) when it can't."""
    tok, repo = token(), repo_name(config)
    if not tok or not repo:
        return False
    s = session or requests.Session()
    headers = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    url = f"{API}/repos/{repo}/contents/{path}"
    body = {"message": message,
            "content": base64.b64encode((json.dumps(data, ensure_ascii=False, indent=1) + "\n").encode()).decode()}
    for _ in range(3):  # a device may save something in between: read the new version and try again
        r = s.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            body["sha"] = r.json().get("sha")
        elif r.status_code != 404:
            log.warning("private repository: reading %s answered %s", path, r.status_code)
            return False
        r = s.put(url, headers=headers, json=body, timeout=60)
        if r.status_code in (200, 201):
            return True
        if r.status_code not in (409, 422):
            log.warning("private repository: saving %s answered %s", path, r.status_code)
            return False
    return False


# ---------------------------------------------------------------- the bot's own Troostwijk memory

SECRET_SECTIONS = ("troostwijk_alerts", "troostwijk_site")


def _secret() -> str | None:
    return (os.environ.get("INVENTORY_KEY") or os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip() or None


def load_secret_state(state: dict, path) -> None:
    """Put the encrypted Troostwijk memory (lots, links, auctions read) back into the state."""
    secret = _secret()
    if not secret or not path.exists():
        return
    from cryptography.fernet import InvalidToken
    from .inventory import _fernet
    try:
        saved = json.loads(_fernet("troostwijk|" + secret).decrypt(path.read_bytes()))
    except (InvalidToken, ValueError):
        log.warning("%s can't be opened with this key: starting the Troostwijk memory afresh", path.name)
        return
    for k in SECRET_SECTIONS:
        if k in saved:
            state[k] = saved[k]


def save_secret_state(state: dict, path) -> None:
    """Take the Troostwijk memory out of the public state.json and save it encrypted next to it."""
    secret = _secret()
    if not secret:
        return  # no key (a local run): it stays in state.json as before
    from .inventory import _fernet
    saved = {k: state.pop(k) for k in SECRET_SECTIONS if k in state}
    path.write_bytes(_fernet("troostwijk|" + secret).encrypt(json.dumps(saved, ensure_ascii=False, sort_keys=True).encode()))
