"""The reminders job stays awake and sends each favorite reminder on time (Diede, 24 Nov 2026)."""
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scanner import reminders

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc)  # 16:00 in Amsterdam


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += timedelta(seconds=seconds)


class Src:
    def __init__(self, favs, lots):
        self.favs, self.lots = favs, lots

    def load(self):
        return self.favs, self.lots


class Sender:
    def __init__(self, clock):
        self.clock, self.sent = clock, []

    def send(self, text, **kw):
        self.sent.append((self.clock(), text))


def setup(tmp_path):
    shutil.copy(ROOT / "config.yml", tmp_path / "config.yml")
    (tmp_path / "data").mkdir()


def test_reminder_arrives_an_hour_before_closing(tmp_path):
    setup(tmp_path)
    closes = START + timedelta(hours=3, minutes=30)
    favs = [{"key": "hnvi:1", "title": "Festool TS 55", "url": "https://www.hnvi.nl/k/1"},
            {"key": "hnvi:2", "title": "Makita DHR243", "url": "https://www.hnvi.nl/k/2"}]
    lots = {"hnvi:1": {"closes": closes.isoformat(), "maxBid": 120, "pickup": "Industrieweg 12A, Sint-Oedenrode",
                       "pickupWhen": "ma 30 nov 09:00-15:00", "trip": {"km": 17}},
            "hnvi:2": {"closes": (closes + timedelta(days=1)).isoformat()}}
    clock = Clock(START)
    sender = Sender(clock)
    again = reminders.run(tmp_path, minutes=345, sources=Src(favs, lots), sender=sender, clock=clock, sleep=clock.sleep)
    assert len(sender.sent) == 1
    at, text = sender.sent[0]
    assert closes - timedelta(minutes=61) <= at <= closes - timedelta(minutes=55)  # on time, not hours late
    assert "Favoriet sluit over" in text and "Festool TS 55" in text and "€" in text and "Sint-Oedenrode" in text
    assert again  # hnvi:2 closes tomorrow: start again
    assert "hnvi:1" in (tmp_path / "data" / "reminders.json").read_text()
    # the next run doesn't send it again
    sender2 = Sender(clock)
    reminders.run(tmp_path, minutes=30, sources=Src(favs, lots), sender=sender2, clock=clock, sleep=clock.sleep)
    assert sender2.sent == []


def test_nothing_coming_up_stops_right_away(tmp_path):
    setup(tmp_path)
    clock = Clock(START)
    favs = [{"key": "hnvi:1", "title": "x", "url": "https://www.hnvi.nl/k/1"}]
    lots = {"hnvi:1": {"closes": (START + timedelta(days=5)).isoformat()}}
    again = reminders.run(tmp_path, sources=Src(favs, lots), sender=Sender(clock), clock=clock, sleep=clock.sleep)
    assert not again and clock.t == START


def test_bundles_lots_closing_together(tmp_path):
    setup(tmp_path)
    closes = START + timedelta(minutes=40)
    favs = [{"key": f"hnvi:{i}", "title": f"Lot {i}", "url": f"https://www.hnvi.nl/k/{i}"} for i in (1, 2)]
    lots = {f["key"]: {"closes": closes.isoformat()} for f in favs}
    clock = Clock(START)
    sender = Sender(clock)
    reminders.run(tmp_path, minutes=10, sources=Src(favs, lots), sender=sender, clock=clock, sleep=clock.sleep)
    assert len(sender.sent) == 1 and "2 favorieten sluiten over 40 min" in sender.sent[0][1]
