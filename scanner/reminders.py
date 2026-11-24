"""Closing reminders for favorites that arrive on time (Diede, 24 Nov 2026: "vandaag sloten er kavels, gemist").

GitHub starts scheduled workflows hours late (the 5-minute "Telegram commands" job ran 2-3 times a day in Nov 2026),
so a reminder an hour before closing was usually missed. This job doesn't wait for GitHub's clock: it stays awake
for up to ~5¾ hours (the limit for one GitHub job is 6), checks the favorites every few minutes, sends each reminder
when it's due, and before it stops starts itself again while favorites are still coming up (workflow reminders.yml).
The commands and scan jobs start it too, so a broken chain heals itself. Free for a public repository.

    python -m scanner reminders [--minutes 345]
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .favorites import FavoritesStore, closing_between, due_stage_reminders
from .http import Http
from .mailer import notifier
from .storage import github_output, load_json, load_yaml, save_json
from .telegram import Telegram, attr, esc
from .util import AMS, fmt_eur

log = logging.getLogger(__name__)
STATE = Path("data") / "reminders.json"
AHEAD = timedelta(hours=48)  # keep the chain going while a favorite closes within this time


def _left(closes: datetime, now: datetime) -> str:
    mins = max(1, round((closes - now).total_seconds() / 60))
    return f"{mins} min" if mins < 90 else f"{mins // 60} uur {mins % 60:02d} min"


def _line(fav: dict, lot: dict | None) -> str:
    lot = lot or {}
    parts = []
    max_bid = lot.get("maxBid", fav.get("maxBid"))
    if max_bid is not None:
        parts.append(f"je max bod <b>{fmt_eur(max_bid)}</b>")
    bid = lot.get("bid", fav.get("bid"))
    if bid is not None:
        parts.append(f"bod bij de laatste scan {fmt_eur(bid)}")
    place = lot.get("pickup") or fav.get("pickup") or lot.get("location")
    trip = lot.get("trip") or {}
    pickup = ""
    if place:
        pickup = f"\n   📍 {esc(place)}" + (f" · {trip['km']:.0f} km" if isinstance(trip.get("km"), (int, float)) else "")
        when = lot.get("pickupWhen") or fav.get("pickupWhen")
        if when:
            pickup += f"\n   🚚 Ophalen: {esc(when)}"
    return (f'⭐ <a href="{attr(fav["url"])}">{esc(fav["title"][:90])}</a>'
            + ("\n   " + " · ".join(parts) if parts else "") + pickup)


def reminder_text(items: list[tuple[dict, dict | None]], closes: datetime, now: datetime) -> str:
    what = "Favoriet sluit" if len(items) == 1 else f"{len(items)} favorieten sluiten"
    head = f"⏰ <b>{what} over {_left(closes, now)}</b> (om {closes.astimezone(AMS):%H:%M})"
    return "\n\n".join([head] + [_line(f, lot) for f, lot in items])


def next_due(favs: list[dict], lots_by_key: dict, now: datetime, stages: list[int], sent: dict) -> datetime | None:
    """When the next reminder becomes due (now if one is due already), or None."""
    stages = sorted({int(s) for s in stages if int(s) > 0})
    best = None
    for f, c in closing_between(favs, lots_by_key, now, now + timedelta(days=30)):
        prev = sent.get(f["key"]) or {}
        for s in reversed(stages):  # largest stage first
            if prev.get("closes") == c.isoformat() and int(prev.get("stage", 10**6)) <= s:
                continue
            at = max(now, c - timedelta(minutes=s))
            best = at if best is None or at < best else best
            break
    return best


def coming_up(favs: list[dict], lots_by_key: dict, now: datetime) -> bool:
    return bool(closing_between(favs, lots_by_key, now, now + AHEAD))


def _git(*args: str) -> bool:
    try:
        subprocess.run(["git", *args], check=True, capture_output=True, timeout=120)
        return True
    except Exception as e:
        log.warning("git %s: %s", " ".join(args), e)
        return False


def save_state(root: Path, state: dict, commit: bool) -> None:
    save_json(root / STATE, state)
    if not commit:
        return
    if not _git("add", str(STATE)) or subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        return
    _git("commit", "-m", "Favorite reminders [skip ci]")
    for _ in range(3):
        if _git("pull", "--rebase", "-X", "theirs") and _git("push"):
            return
        time.sleep(5)


class Sources:
    """The favorites (GitHub issue) and the newest scan (lots.json, closing times move when people bid late)."""

    def __init__(self, root: Path, pull: bool):
        self.root, self.pull = root, pull
        self.store = FavoritesStore(Http(delay=0.2))

    def load(self) -> tuple[list[dict], dict]:
        if self.pull:
            _git("pull", "--rebase", "-X", "theirs", "-q")
        lots = (load_json(self.root / "data" / "lots.json") or {}).get("lots") or []
        by_key = {lot["key"]: lot for lot in lots if isinstance(lot, dict) and lot.get("key")}
        favs = self.store.load()
        return favs, by_key


def run(root: Path, minutes: float = 345, poll_seconds: int = 300, dry_run: bool = False, sources=None,
        sender=None, clock=None, sleep=time.sleep) -> bool:
    """Stay awake `minutes`, sending reminders when due. Returns True when it should start again."""
    clock = clock or (lambda: datetime.now(timezone.utc))
    config = load_yaml(root / "config.yml") or {}
    fav_cfg = config.get("favorites") or {}
    if fav_cfg.get("reminders", True) is False:
        return False
    stages = fav_cfg.get("reminder_minutes_before") or [60]
    in_ci = bool(os.environ.get("GITHUB_ACTIONS"))
    sources = sources or Sources(root, pull=in_ci and not dry_run)
    if sender is None:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        sender = notifier(Telegram(Http(delay=0.5), token, chat if token else None, dry_run=dry_run), dry_run)
    state = load_json(root / STATE) or {}
    if not state.get("sent"):  # carry over what the commands job already sent
        state["sent"] = dict((load_json(root / "data" / "telegram.json") or {}).get("reminder_stages") or {})
    sent = state["sent"]
    start = clock()
    end = start + timedelta(minutes=minutes)
    favs, lots = [], {}
    while True:
        now = clock()
        try:
            favs, lots = sources.load()
        except Exception as e:
            log.warning("reminders: could not load favorites: %s", e)
        groups: dict[datetime, list] = {}
        for fav, closes, stage in due_stage_reminders(favs, lots, now, stages, sent):
            groups.setdefault(closes, []).append((fav, stage))
        changed = False
        for closes, group in sorted(groups.items()):
            try:
                sender.send(reminder_text([(f, lots.get(f["key"])) for f, _ in group], closes, now))
                for fav, stage in group:
                    sent[fav["key"]] = {"closes": closes.isoformat(), "stage": stage}
                changed = True
                log.info("reminder sent for %d favorite(s) closing %s", len(group), closes.isoformat())
            except Exception as e:
                log.warning("could not send a reminder: %s", e)
        for key in [k for k, v in sent.items() if datetime.fromisoformat(v["closes"]) < now - timedelta(days=2)]:
            del sent[key]
            changed = True
        if changed:
            state["at"] = now.isoformat()
            save_state(root, state, commit=in_ci and not dry_run)
        nxt = next_due(favs, lots, now, stages, sent)
        if now >= end or not coming_up(favs, lots, now):  # nothing closes soon: no need to stay awake
            break
        wait = poll_seconds if nxt is None else min(poll_seconds, max(20, (nxt - now).total_seconds()))
        wait = min(wait, max(1, (end - now).total_seconds()))
        sleep(wait)
    return coming_up(favs, lots, clock())


def main(root: Path, minutes: float, dry_run: bool) -> int:
    again = run(root, minutes=minutes, dry_run=dry_run)
    github_output("again", "true" if again else "false")
    return 0
