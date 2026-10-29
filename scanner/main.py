"""Command line entry point.

python -m scanner scan       the daily scan: auction sites -> Marktplaats prices -> dashboard + Telegram digest
python -m scanner commands   read Telegram commands and reply (never contacts the auction sites)
python -m scanner dashboard  rebuild the dashboard page from the last scan's results (data/lots.json), so new page code
                             goes live without a scan; no auction sites, no Telegram
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import dashboard
from .bot import run_commands
from .scan import run_scan
from .storage import load_json
from .util import utc_now


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Dutch bankruptcy auction deal finder")
    ap.add_argument("mode", nargs="?", default="scan", choices=["scan", "commands", "dashboard"])
    ap.add_argument("--root", default=".", help="folder with config.yml and watchlist.yml")
    ap.add_argument("--dry-run", action="store_true", help="print Telegram messages instead of sending them")
    ap.add_argument("--sites", help="comma-separated subset of sites to scan")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    root = Path(args.root)
    now = utc_now()
    if args.mode == "commands":
        return run_commands(root, now, dry_run=args.dry_run)
    if args.mode == "dashboard":
        data = load_json(root / "data" / "lots.json")
        if not data:
            logging.error("no data/lots.json yet: run a scan first")
            return 1
        dashboard.write(root / "site", data)
        return 0
    only = [s.strip() for s in args.sites.split(",")] if args.sites else None
    return run_scan(root, now, dry_run=args.dry_run, only=only)


if __name__ == "__main__":
    sys.exit(run())
