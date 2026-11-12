import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS

from scanner import route

NOW = datetime(2026, 11, 12, 14, 0, tzinfo=timezone.utc)
POINTS = {"Het Sterrenbeeld 54, 's-Hertogenbosch": (51.70, 5.30), "Vught, Nederland": (51.65, 5.29),
          "Groningen, Nederland": (53.22, 6.57)}


def fake_geocode(http, address):
    return POINTS.get(address)


def setup(tmp_path, monkeypatch, closes):
    monkeypatch.setattr(route, "geocode", fake_geocode)
    (tmp_path / "data").mkdir()
    fav = {"pickup": "Het Sterrenbeeld 54, 's-Hertogenbosch", "closes": closes, "key": "hnvi:1"}
    (tmp_path / "data" / "lots.json").write_text(json.dumps({"favorites": {"items": [fav]}}))
    return route.pickup_spots(tmp_path, NOW, {}, {}, lambda **kw: None)


def test_near_a_pickup(tmp_path, monkeypatch):
    spots = setup(tmp_path, monkeypatch, "2026-11-14T19:30:00+01:00")
    assert len(spots) == 1
    mem = {}
    assert route.near(NS(location="Vught"), spots, mem, lambda **kw: None, 15) == "'s-Hertogenbosch (auction closes 14 Nov)"
    assert route.near(NS(location="Groningen"), spots, mem, lambda **kw: None, 15) is None
    assert route.near(NS(location=None), spots, mem, lambda **kw: None, 15) is None


def test_old_favorites_dont_count(tmp_path, monkeypatch):
    assert setup(tmp_path, monkeypatch, "2026-11-01T19:30:00+01:00") == []


def test_near_home_and_work(tmp_path, monkeypatch):
    POINTS["Kantoorweg 1, Utrecht"] = (52.09, 5.12)
    POINTS["Zeist, Nederland"] = (52.09, 5.23)
    monkeypatch.setenv("WORK_ADDRESS", "Kantoorweg 1, Utrecht")
    spots = setup(tmp_path, monkeypatch, "2026-11-01T19:30:00+01:00")  # no auction pickups in the window
    assert [s["label"] for s in spots] == ["work"]
    assert route.near(NS(location="Zeist"), spots, {}, lambda **kw: None, 15) == "your work"
    assert route.near(NS(location="Groningen"), spots, {}, lambda **kw: None, 15) is None
    assert route.near_home(NS(distance_km=15), 20) and not route.near_home(NS(distance_km=30), 20)
    assert not route.near_home(NS(distance_km=None), 20)
