"""Messages by email instead of Telegram (Diede, 2026-11-20: Telegram isn't pleasant to use).

The bot already reads a Gmail mailbox for Troostwijk's alert emails (secrets ALERTS_EMAIL and
ALERTS_APP_PASSWORD). The same app password lets it send mail through Gmail's SMTP server, so no
new account is needed. Mail goes to the address in the secret MAIL_TO (kept out of this public repository).

Mailer has the same send()/send_photo() as Telegram, and the messages are written in Telegram's small
HTML dialect (<b>, <i>, <a>, <code>), which email shows as it is; only line breaks need <br>.
"""
from __future__ import annotations

import html
import logging
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

log = logging.getLogger(__name__)

TAG = re.compile(r"<[^>]+>")


def subject_of(text: str, fallback: str = "Veilingbot") -> str:
    """The first non-empty line of a message, without tags, as the mail subject."""
    for line in text.splitlines():
        plain = html.unescape(TAG.sub("", line)).strip()
        if plain:
            return plain[:150]
    return fallback


def to_html(text: str) -> str:
    body = text.replace("\n", "<br>\n")
    return ('<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:15px;'
            f'line-height:1.45;color:#1b1b1b;max-width:680px">{body}</div>')


def to_plain(text: str) -> str:
    # keep link targets in the plain-text version: "name (https://...)"
    text = re.sub(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', lambda m: f"{m.group(2)} ({html.unescape(m.group(1))})",
                  text, flags=re.S)
    return html.unescape(TAG.sub("", text))


class Mailer:
    def __init__(self, user: str, password: str, to: str, host: str = "smtp.gmail.com", port: int = 465,
                 dry_run: bool = False, smtp_cls=smtplib.SMTP_SSL):
        self.user, self.password, self.to = user, password, to
        self.host, self.port = host, port
        self.dry_run = dry_run
        self.smtp_cls = smtp_cls
        self.sent = 0
        self.chat_id = None  # Telegram's attribute; a mailer reads no chat

    @classmethod
    def from_env(cls, dry_run: bool = False, smtp_cls=smtplib.SMTP_SSL) -> "Mailer | None":
        to = os.environ.get("MAIL_TO", "").strip()
        user = os.environ.get("MAIL_FROM", "").strip() or os.environ.get("ALERTS_EMAIL", "").strip()
        password = (os.environ.get("MAIL_APP_PASSWORD", "") or os.environ.get("ALERTS_APP_PASSWORD", "")).replace(" ", "").strip()
        if not to or not user or not password:
            return None
        host = os.environ.get("MAIL_SMTP_HOST", "").strip() or "smtp.gmail.com"
        return cls(user, password, to, host=host, dry_run=dry_run, smtp_cls=smtp_cls)

    def build(self, text: str, subject: str | None = None, images: dict[str, bytes] | None = None) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = subject or subject_of(text)
        msg["From"] = f"Veilingbot <{self.user}>"
        msg["To"] = self.to
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain="veilingbot.local")
        msg.set_content(to_plain(text))
        msg.add_alternative(to_html(text), subtype="html")
        html_part = msg.get_payload()[-1]
        for cid, png in (images or {}).items():  # shown inside the mail where the text says <img src="cid:...">
            html_part.add_related(png, maintype="image", subtype="png", cid=f"<{cid}>", filename=f"{cid}.png")
        return msg

    def send(self, text: str, chat_id: str | None = None, preview: bool = False, subject: str | None = None,
             images: dict[str, bytes] | None = None) -> None:
        if self.dry_run:
            print(f"\n[mail] {subject or subject_of(text)}\n" + text)
            return
        msg = self.build(text, subject, images)
        with self.smtp_cls(self.host, self.port, context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(self.user, self.password)
            smtp.send_message(msg)
        self.sent += 1

    def send_photo(self, photo_url: str | None, caption: str) -> None:
        self.send(caption)


def notifier(telegram, dry_run: bool = False):
    """Where the bot's messages go: email when MAIL_TO is set up, else Telegram (the old way)."""
    return Mailer.from_env(dry_run=dry_run) or telegram
