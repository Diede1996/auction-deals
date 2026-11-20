"""Messages by email instead of Telegram (Diede, 2026-11-20)."""
import json
from datetime import timedelta

from scanner import inventory
from scanner.bot import run_commands
from scanner.mailer import Mailer, notifier, subject_of, to_plain

from test_inventory import NOW
from test_main import FakeTelegram, repo  # noqa: F401  (pytest fixture)


class FakeSMTP:
    sent = []

    def __init__(self, host, port, context=None, timeout=None):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, password):
        self.user = user

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def mail_env(monkeypatch):
    monkeypatch.setenv("MAIL_TO", "me@example.com")
    monkeypatch.setenv("ALERTS_EMAIL", "bot@example.com")
    monkeypatch.setenv("ALERTS_APP_PASSWORD", "abcd efgh ijkl mnop")


def test_subject_and_plain_text():
    text = '⏰ <b>Favoriet sluit over 1 uur</b>\n<a href="https://x.nl/a?b=1&amp;c=2">Dell monitor</a> &amp; meer'
    assert subject_of(text) == "⏰ Favoriet sluit over 1 uur"
    assert "Dell monitor (https://x.nl/a?b=1&c=2) & meer" in to_plain(text)


def test_no_mail_without_address(monkeypatch):
    monkeypatch.setenv("ALERTS_EMAIL", "bot@example.com")
    monkeypatch.setenv("ALERTS_APP_PASSWORD", "x")
    tg = object()
    assert Mailer.from_env() is None and notifier(tg) is tg


def test_mail_is_sent_with_the_alerts_mailbox(monkeypatch):
    mail_env(monkeypatch)
    FakeSMTP.sent = []
    m = Mailer.from_env(smtp_cls=FakeSMTP)
    assert m.password == "abcdefghijklmnop"
    m.send("🩺 <b>Bot check</b>\nalles kapot")
    msg = FakeSMTP.sent[0]
    assert msg["To"] == "me@example.com" and msg["Subject"] == "🩺 Bot check"
    assert "<br>" in msg.get_body(("html",)).get_content()


def test_reminders_go_by_mail_without_telegram(repo, monkeypatch):  # noqa: F811
    mail_env(monkeypatch)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    monkeypatch.setenv("INVENTORY_KEY", "k")
    inventory.save(repo / "data" / "inventory.enc", {"next": 2, "items": [
        {"id": 1, "name": "Dell monitor", "paid": 45, "bought": (NOW - timedelta(days=35)).isoformat(),
         "listed": (NOW - timedelta(days=35)).isoformat(), "ask": 120, "floor": 90, "category": "Monitor"}]}, "k")
    cfg = repo / "config.yml"
    cfg.write_text(cfg.read_text().replace("\n  enabled: false                # false = no reminders", "\n  enabled: true # false = no reminders"))
    (repo / "data" / "telegram.json").write_text(json.dumps({"offset": 1, "welcomed": "x"}))
    mailed = []
    monkeypatch.setattr(Mailer, "send", lambda self, text, **kw: mailed.append(text))
    tg = FakeTelegram()
    assert run_commands(repo, NOW + timedelta(hours=1), http_cls=lambda **kw: None, telegram_cls=tg) == 0
    assert tg.sent == [] and len(mailed) == 1 and "Dell monitor" in mailed[0]
