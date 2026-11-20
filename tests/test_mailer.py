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


def test_sunday_mail(monkeypatch):
    from datetime import datetime, timezone
    from scanner import weekly_mail
    now = datetime(2026, 11, 22, 16, 0, tzinfo=timezone.utc)  # Sunday 17:00 in Amsterdam
    assert weekly_mail.is_sunday(now)
    favs = [{"key": "hnvi:1", "title": "Festool AGC 18", "url": "https://www.hnvi.nl/k/1", "maxBid": 120},
            {"key": "hnvi:2", "title": "Makita DHR243", "url": "https://www.hnvi.nl/k/2"},
            {"key": "bva:3", "title": "Dell monitor", "url": "https://www.bva-auctions.com/k/3"},
            {"key": "hnvi:4", "title": "Next month", "url": "https://www.hnvi.nl/k/4"}]
    lots = {"hnvi:1": {"closes": "2026-11-24T19:30:00+01:00", "pickup": "Produktieweg 9, Emmeloord",
                       "pickupWhen": "do 26 nov 10:00-12:00", "trip": {"km": 84}},
            "hnvi:2": {"closes": "2026-11-24T19:32:00+01:00", "pickup": "Produktieweg 9, Emmeloord", "maxBid": 90},
            "bva:3": {"closes": "2026-11-23T20:00:00+01:00"},
            "hnvi:4": {"closes": "2026-12-20T20:00:00+01:00"}}
    text = weekly_mail.message(favs, lots, [], now, "https://dash")
    assert "3 favorieten sluiten deze week" in text and "Next month" not in text
    assert text.index("Ophaaladres nog onbekend") < text.index("Produktieweg")  # the earliest closing first
    assert text.count("Produktieweg 9, Emmeloord") == 1 and "do 26 nov 10:00-12:00" in text and "84 km" in text
    assert "di 24 nov 19:30" in text and "je max bod € 120" in text.replace("€120", "€ 120")
    assert "alles werkt" in text
    bad = weekly_mail.message([], {}, ["Daily scan: no successful run for 2 days."], now)
    assert "Er sluiten deze week geen favorieten" in bad and "1 ding werkt niet" in bad


def test_health_sends_the_sunday_mail_on_the_scheduled_run(tmp_path, monkeypatch):
    import shutil
    from datetime import datetime, timezone
    from pathlib import Path
    from scanner import health
    from scanner.favorites import FavoritesStore
    shutil.copy(Path(__file__).resolve().parents[1] / "config.yml", tmp_path / "config.yml")
    mail_env(monkeypatch)
    mailed = []
    monkeypatch.setattr(Mailer, "send", lambda self, text, **kw: mailed.append(text))
    monkeypatch.setattr(health, "fetch_runs", lambda s, r: {})
    monkeypatch.setattr(health, "fetch_private", lambda s, c: {})
    monkeypatch.setattr(FavoritesStore, "load", lambda self: [])
    sunday = datetime(2026, 11, 22, 16, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    health.run(tmp_path, sunday, {"problems": []}, session=object())
    assert len(mailed) == 1 and "Bot check" in mailed[0] and "Weekoverzicht" not in mailed[0]  # problems only
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    health.run(tmp_path, sunday, {"problems": []}, session=object())
    assert len(mailed) == 2 and "Weekoverzicht" in mailed[1] and "werkt niet" in mailed[1]  # no lots.json


def test_pickup_map_in_the_mail(monkeypatch):
    import io
    from datetime import datetime, timezone
    from PIL import Image
    from scanner import mail_map, weekly_mail

    def tile(z, x, y):
        out = io.BytesIO()
        Image.new("RGB", (256, 256), (200, 220, 200)).save(out, "PNG")
        return out.getvalue()
    now = datetime(2026, 11, 22, 16, 0, tzinfo=timezone.utc)
    favs = [{"key": "hnvi:1", "title": "Festool", "url": "https://www.hnvi.nl/k/1"},
            {"key": "hnvi:2", "title": "Makita", "url": "https://www.hnvi.nl/k/2"},
            {"key": "hnvi:3", "title": "Dell", "url": "https://www.hnvi.nl/k/3"}]
    lots = {"hnvi:1": {"closes": "2026-11-24T19:30:00+01:00", "pickup": "Handelsstraat 52, Hardenberg"},
            "hnvi:2": {"closes": "2026-11-25T19:30:00+01:00", "pickup": "Industrieweg 12A, Sint-Oedenrode"},
            "hnvi:3": {"closes": "2026-11-23T19:30:00+01:00", "pickup": "Nergens 1, Onbekend"}}
    geo = {"Handelsstraat 52, Hardenberg": {"lat": 52.56, "lon": 6.63},
           "Industrieweg 12A, Sint-Oedenrode": {"lat": 51.56, "lon": 5.48}}
    pts = weekly_mail.map_points(favs, lots, now, geo)
    assert pts == [(52.56, 6.63), (51.56, 5.48)]
    png = mail_map.render(pts, tile)
    assert Image.open(io.BytesIO(png)).size == (640, 480)
    text = weekly_mail.message(favs, lots, [], now, geo=geo, with_map=True)
    assert "cid:map" in text and "📍 1. " in text and "📍 2. " in text and "📍 <a" in text  # no pin for Onbekend
    assert "google.com/maps" in text
    msg = Mailer("bot@x", "pw", "me@x").build(text, images={"map": png})
    assert any(p.get_content_type() == "image/png" and p["Content-ID"] == "<map>" for p in msg.walk())
    assert mail_map.pickup_map(pts, get_tile=lambda z, x, y: 1 / 0) is None  # tiles down: mail without map
