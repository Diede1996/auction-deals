"""Telegram side: read commands sent to the bot and reply. Never contacts the auction sites."""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from . import commands
from .http import Http
from .sites import SITE_NAMES
from .storage import dashboard_url, github_output, load_json, load_yaml, save_json, save_watchlist
from .telegram import Telegram, attr, esc
from .util import AMS

log = logging.getLogger("scanner.bot")


def status_text(scan_state: dict, watchlist: dict, url: str | None) -> str:
    last = scan_state.get("last_run") or {}
    lines = []
    if last:
        when = datetime.fromisoformat(last["at"]).astimezone(AMS).strftime("%a %d %b %H:%M")
        lines += [f"<b>Last scan</b>: {when}",
                  f"Watching {len(watchlist.get('items') or [])} items · {last.get('matches', 0)} matching lots · "
                  f"{last.get('deals', 0)} with room to bid"]
    else:
        lines.append("No scan has finished yet.")
    for site, h in (scan_state.get("health") or {}).items():
        name = SITE_NAMES.get(site, site)
        if h.get("ok"):
            what = "price lookups" if site == "marktplaats" else "lots checked"
            lines.append(f"✅ {name}: {h.get('lots', 0)} {what}")
        else:
            lines.append(f"⚠️ {name}: failed {h.get('fails', 1)}× – {esc(h.get('error', '')[:120])}")
    if url:
        lines.append(f'\n📊 <a href="{attr(url)}">Dashboard</a>')
    return "\n".join(lines)


def announce_chat_id(tg: Telegram) -> None:
    """Setup helper: no TELEGRAM_CHAT_ID yet -> tell whoever messaged the bot what their chat id is."""
    try:
        updates = tg.updates(None)
    except Exception as e:
        print(f"Could not reach Telegram: {e}")
        return
    chats = {}
    for u in updates:
        chat = (u.get("message") or {}).get("chat") or {}
        if chat.get("id"):
            chats[str(chat["id"])] = chat.get("first_name") or chat.get("title") or ""
    if not chats:
        print("TELEGRAM_CHAT_ID is not set. Send any message to your bot in Telegram, then run this workflow again.")
        return
    for chat_id, name in chats.items():
        print(f"Found chat {chat_id} ({name}). Add it as the TELEGRAM_CHAT_ID secret.")
        tg.send(f"👋 Hi {esc(name)}! Your chat ID is <code>{chat_id}</code>.\n\n"
                "Add it in GitHub as the secret <b>TELEGRAM_CHAT_ID</b>, then run the workflow again.",
                chat_id=chat_id)


def run_commands(root: Path, now: datetime, dry_run: bool = False, http_cls=Http, telegram_cls=Telegram) -> int:
    config = load_yaml(root / "config.yml")
    watch_path = root / "watchlist.yml"
    watchlist = load_yaml(watch_path)
    tg_path = root / "data" / "telegram.json"
    tg_state = load_json(tg_path)
    scan_state = load_json(root / "data" / "state.json")
    url = dashboard_url(config)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN is not set; nothing to do.")
        return 0
    tg = telegram_cls(http_cls(delay=0.3), token, chat_id, dry_run=dry_run)
    if not chat_id:
        announce_chat_id(tg)
        return 0

    try:
        updates = tg.updates(tg_state.get("offset"))
    except Exception as e:
        log.warning("could not read Telegram messages: %s", e)
        updates = []

    limit = int((config.get("commands") or {}).get("max_extra_scans_per_day", 3))
    today = now.astimezone(AMS).date().isoformat()
    scans = [d for d in tg_state.get("extra_scans", []) if d == today]
    changed = scan_requested = False
    for u in updates:
        tg_state["offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id", "")) != tg.chat_id:
            log.info("ignoring a message from another chat")
            continue
        text = (msg.get("text") or "").strip()
        if text.split(" ")[0].split("@")[0].lower() == "/scan":
            if scan_requested:
                reply = "A scan is already starting."
            elif len(scans) >= limit:
                reply = (f"You've used today's {limit} extra scans. The next daily scan runs tomorrow morning "
                         "(fewer scans keeps the auction sites happy).")
            else:
                scan_requested = True
                scans.append(today)
                reply = f"🔎 Scanning now. Results arrive in about 5 minutes ({limit - len(scans)} extra scans left today)."
        else:
            reply, did_change = commands.handle(text, watchlist, status_text(scan_state, watchlist, url), url)
            changed |= did_change
        try:
            tg.send(reply)
        except Exception as e:
            log.warning("could not reply on Telegram: %s", e)

    if not tg_state.get("welcomed"):
        dash = f'\n📊 <a href="{attr(url)}">Your dashboard</a>' if url else ""
        tg.send("✅ <b>Your auction deal bot is connected.</b>\n"
                f"Every morning it checks 5 auction sites (bankruptcy sales only) for the "
                f"{len(watchlist.get('items') or [])} items on your watchlist and sends you a summary.{dash}\n\n"
                "Send /list to see your watchlist, /help for all commands.")
        tg_state["welcomed"] = now.isoformat()

    tg_state["extra_scans"] = scans
    if not dry_run:
        if changed:
            save_watchlist(watch_path, watchlist)
        save_json(tg_path, tg_state)
    github_output("scan", "true" if scan_requested else "false")
    return 0
