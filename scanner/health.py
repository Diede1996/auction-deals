"""Daily check that the bot still works (Diede, 12 Nov 2026: "check every so often whether everything still works").

Looks at what the bot produced, not at its code: did the scheduled workflows run and succeed, is the scan's data
fresh, did every auction site give lots, are the private files (Troostwijk, Marktplaats bargains, Claude advice)
being updated, and does the dashboard page show lots in a real browser (scripts/dashboard_check.js). Sends one
Telegram message only when something is wrong; silence means all is well. Costs no Claude credit.

    python -m scanner.health [--browser result.json] [--dry-run]
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from . import private_repo
from .storage import load_json, load_yaml
from .telegram import Telegram, esc
from .util import AMS

log = logging.getLogger(__name__)
API = "https://api.github.com"

# workflow file -> (name, hours without a successful run before it counts as stuck)
WORKFLOWS = {
    "scan.yml": ("Daily scan", 36),
    "deals.yml": ("Marktplaats bargains", 6),
    "commands.yml": ("Telegram commands and closing reminders", 12),
}
# private file -> (what it is, hours before it's old)
PRIVATE_FILES = {
    "data/troostwijk-lots.json": ("Troostwijk lots", 36),
    "data/claude-advice.json": ("Claude advice (Wednesday and Sunday)", 24 * 5),
}


def _age_h(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (now - t).total_seconds() / 3600


def _ago(hours: float) -> str:
    return f"{hours:.0f} h" if hours < 48 else f"{hours / 24:.0f} days"


def check_lots(lots: dict, now: datetime) -> list[str]:
    """The public scan result data/lots.json: fresh, and every site read."""
    out = []
    age = _age_h(lots.get("generated"), now)
    if age is None or age > WORKFLOWS["scan.yml"][1]:
        out.append(f"The daily scan hasn't saved new lots for {_ago(age) if age is not None else 'a long time'}.")
    for s in lots.get("sites") or []:
        name = s.get("name") or s.get("id")
        if not s.get("ok"):
            out.append(f"{name}: couldn't be read in the last scan ({(s.get('error') or 'no reason given')[:120]}).")
        elif not s.get("lots"):
            out.append(f"{name}: read, but gave 0 lots (the site may have changed).")
        elif s.get("private") and s.get("privateSaved") is False:
            out.append(f"{name}: lots couldn't be saved to the private repository.")
    return out


def check_runs(runs_by_file: dict[str, list], now: datetime) -> list[str]:
    """runs_by_file: the newest scheduled/dispatched runs per workflow file (GitHub API 'workflow_runs')."""
    out = []
    hour_nl = now.astimezone(AMS).hour
    for f, (name, max_h) in WORKFLOWS.items():
        runs = [r for r in runs_by_file.get(f) or [] if r.get("status") == "completed"]
        if f == "deals.yml" and not 9 <= hour_nl <= 23:
            max_h = 14  # it doesn't run at night
        ok = [r for r in runs if r.get("conclusion") == "success"]
        age = _age_h(ok[0].get("updated_at") if ok else None, now)
        if age is None or age > max_h:
            out.append(f"{name}: no successful run for {_ago(age) if age is not None else 'a long time'}.")
        failed = 0
        for r in runs:  # failures in a row, newest first
            if r.get("conclusion") != "failure":
                break
            failed += 1
        if failed:
            out.append(f"{name}: the last {failed} run{'s' if failed > 1 else ''} failed.")
    return out


def check_private(files: dict[str, dict | None], now: datetime) -> list[str]:
    out = []
    for path, (name, max_h) in PRIVATE_FILES.items():
        if path not in files:
            continue  # couldn't look (no token)
        age = _age_h((files[path] or {}).get("generated"), now)
        if age is None or age > max_h:
            out.append(f"{name}: not updated for {_ago(age) if age is not None else 'a long time'}.")
    return out


def check_browser(result: dict | None) -> list[str]:
    if result is None:
        return ["Dashboard: the browser check didn't run."]
    return [f"Dashboard: {p}" for p in result.get("problems") or []]


# ---------------------------------------------------------------- fetching

def _gh(session, url: str, token: str | None):
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = session.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_runs(session, repo: str) -> dict[str, list]:
    out = {}
    for f in WORKFLOWS:
        try:
            d = _gh(session, f"{API}/repos/{repo}/actions/workflows/{f}/runs?per_page=15&exclude_pull_requests=true",
                    os.environ.get("GITHUB_TOKEN"))
            out[f] = d.get("workflow_runs") or []
        except requests.RequestException as e:
            log.warning("health: runs of %s: %s", f, e)
    return out


def fetch_private(session, config: dict) -> dict[str, dict | None]:
    tok, repo = private_repo.token(), private_repo.repo_name(config)
    if not tok or not repo:
        return {}
    out = {}
    for path in PRIVATE_FILES:
        try:
            f = _gh(session, f"{API}/repos/{repo}/contents/{path}", tok)
            if not f.get("content"):  # over 1 MB: read the blob
                f = _gh(session, f"{API}/repos/{repo}/git/blobs/{f['sha']}", tok)
            out[path] = json.loads(base64.b64decode(f["content"]))
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                out[path] = None  # missing counts as not updated
            else:
                log.warning("health: %s: %s", path, e)
        except (requests.RequestException, ValueError, KeyError) as e:
            log.warning("health: %s: %s", path, e)
    return out


def message(problems: list[str]) -> str:
    lines = [f"🩺 <b>Bot check: {len(problems)} thing{'s' if len(problems) != 1 else ''} not working</b>"]
    lines += [f"• {esc(p)}" for p in problems]
    lines.append("Ask Claude in the project to look at it.")
    return "\n".join(lines)


def run(root: Path, now: datetime, browser: dict | None, dry_run: bool = False, session=None) -> list[str]:
    session = session or requests.Session()
    config = load_yaml(root / "config.yml") or {}
    repo = os.environ.get("GITHUB_REPOSITORY") or "Diede1996/auction-deals"
    problems = check_lots(load_json(root / "data" / "lots.json") or {}, now)
    problems += check_runs(fetch_runs(session, repo), now)
    problems += check_private(fetch_private(session, config), now)
    problems += check_browser(browser)
    for p in problems:
        log.info("health: %s", p)
    if not problems:
        log.info("health: everything works")
        return problems
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if token or dry_run:
        from .http import Http
        Telegram(Http(delay=0.5), token, chat_id if token else None, dry_run=dry_run).send(message(problems))
    return problems


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", help="result file of scripts/dashboard_check.js")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    browser = None
    if a.browser:
        try:
            browser = json.loads(Path(a.browser).read_text() or "null")
        except (OSError, ValueError):
            browser = None
    run(Path("."), datetime.now(timezone.utc), browser, a.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
