"""Minimal Telegram Bot API client (send messages/photos, read incoming commands)."""
from __future__ import annotations

import html
import logging

log = logging.getLogger(__name__)


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=False)


def attr(text) -> str:
    """Escape a value used inside an HTML attribute, e.g. a link."""
    return html.escape(str(text if text is not None else ""), quote=True)


class Telegram:
    def __init__(self, http, token: str, chat_id: str | None, dry_run: bool = False):
        self.http = http
        self.token = token
        self.chat_id = str(chat_id) if chat_id else None
        self.dry_run = dry_run
        self.sent = 0

    @property
    def api(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    def _call(self, method: str, payload: dict) -> dict:
        resp = self.http.post(f"{self.api}/{method}", json=payload, timeout=30)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
        return data.get("result")

    def send(self, text: str, chat_id: str | None = None, preview: bool = False) -> None:
        chat = chat_id or self.chat_id
        if self.dry_run or not chat:
            print("\n[telegram]\n" + text)
            return
        for chunk in _chunks(text, 4000):
            self._call("sendMessage", {"chat_id": chat, "text": chunk, "parse_mode": "HTML",
                                       "disable_web_page_preview": not preview})
        self.sent += 1

    def send_photo(self, photo_url: str | None, caption: str) -> None:
        if self.dry_run or not self.chat_id or not photo_url or len(caption) > 1024:
            self.send(caption, preview=bool(photo_url))
            return
        try:
            self._call("sendPhoto", {"chat_id": self.chat_id, "photo": photo_url, "caption": caption,
                                     "parse_mode": "HTML"})
            self.sent += 1
        except Exception as e:  # image refused by Telegram -> send text instead
            log.warning("sendPhoto failed (%s), sending text", e)
            self.send(caption, preview=True)

    def updates(self, offset: int | None) -> list[dict]:
        payload = {"timeout": 0, "allowed_updates": ["message"]}
        if offset:
            payload["offset"] = offset
        return self._call("getUpdates", payload) or []


def _chunks(text: str, size: int):
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        cut = cut if cut > 0 else size
        yield text[:cut]
        text = text[cut:].lstrip("\n")
    yield text
