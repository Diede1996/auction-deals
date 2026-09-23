"""Command line entry point.

python -m scanner scan       the daily scan: auction sites -> Marktplaats prices -> dashboard + Telegram digest
python -m scanner commands   read Telegram commands and reply (never contacts the auction sites)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .bot import run_commands
from .scan import run_scan
from .util import utc_now


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Dutch bankruptcy auction deal finder")
    ap.add_argument("mode", nargs="?", default="scan", choices=["scan", "commands"])
    ap.add_argument("--root", default=".", help="folder with config.yml and watchlist.yml")
    ap.add_argument("--dry-run", action="store_true", help="print Telegram messages instead of sending them")
    ap.add_argument("--sites", help="comma-separated subset of sites to scan")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    root = Path(args.root)
    now = utc_now()
    if args.mode == "commands":
        return run_commands(root, now, dry_run=args.dry_run)
    only = [s.strip() for s in args.sites.split(",")] if args.sites else None
    return run_scan(root, now, dry_run=args.dry_run, only=only)


if __name__ == "__main__":
    sys.exit(run())
