from datetime import datetime, timezone

from scanner import advice

SETTINGS = {"resale_factor": 0.75, "min_margin": 0.25, "min_profit": 25, "min_lot_profit": 40}


def lot(**kw):
    base = {"key": "s:1", "item": "Power tools", "premium": 0.17, "vat": 0.21, "fixed": 0, "units": 1,
            "nextBid": 50.0, "closes": "2026-11-14T18:00:00+00:00",
            "mp": {"median": 200.0, "prices": [100.0, 200.0, 210.0, 400.0], "asOf": "x",
                   "listings": [{"price": 100.0, "title": "accu"}, {"price": 200.0, "title": "a"},
                                {"price": 210.0, "title": "b"}]}}
    base.update(kw)
    return base


def test_price_uses_kept_listings_and_unseen_dearer_ones():
    price, used, seen = advice.price_from(lot(), {"keep": [1, 2]})
    assert (price, used, seen) == (210.0, 3, 4)
    price, used, _ = advice.price_from(lot(), {"keep": [1, 2], "dropUnseen": True})
    assert (price, used) == (205.0, 2)
    assert advice.price_from(lot(), {"keep": [1], "dropUnseen": True})[0] is None  # one listing is too few


def test_max_bid_matches_the_bots_rule_and_trip_is_worked_back():
    no_trip = lot()
    mx = advice.max_bid(no_trip, SETTINGS, 200.0)
    # resale 150: caps 150/1.25 = 120 and 150 - 40 = 110 -> 110 / 1.21 / 1.17 = 77.7
    assert mx == 77
    with_trip = lot(maxBid=60.0, market=200.0)  # the bot counted a drive: about €24
    assert advice.trip_share(with_trip, SETTINGS) > 20
    assert advice.max_bid(with_trip, SETTINGS, 200.0) in (59, 60, 61)


def test_labels():
    assert advice.label("kopen", True, 100) == "kopen"
    assert advice.label("kopen", False, 100) == "te duur"
    assert advice.label("kopen", True, None) == "twijfel"
    assert advice.label("niet", True, 100) == "niet"


def test_apply_writes_advice_and_copies_same_as(tmp_path):
    import json
    root, priv = tmp_path / "pub", tmp_path / "priv"
    (root / "data").mkdir(parents=True)
    a, b = lot(), lot(key="s:2", nextBid=200.0)
    (root / "data" / "lots.json").write_text(json.dumps({"settings": SETTINGS, "lots": [a, b]}))
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    res = advice.apply(root, priv, {"s:1": {"keep": [1, 2], "verdict": "kopen", "reason": "r"},
                                    "s:2": {"sameAs": "s:1"}}, now)
    assert res["lots"]["s:1"]["label"] == "kopen" and res["lots"]["s:2"]["label"] == "te duur"
    assert (priv / "data" / "claude-advice.json").exists()


def test_nothing_kept_means_no_price():
    assert advice.price_from(lot(), {"keep": []})[0] is None
