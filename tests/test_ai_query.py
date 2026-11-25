import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from scanner import ai_query, marktplaats
from scanner.models import Lot, WatchItem
from scanner.pricing import PriceFinder

NOW = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)


class Client:
    def __init__(self, answers):
        self.answers, self.calls = answers, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return NS(stop_reason="end_turn", content=[NS(type="text", text=json.dumps({"lots": self.answers}))])


def lot(i, title):
    return Lot(site="hnvi", lot_id=str(i), title=title, url="https://x", current_bid=20, closes_at=None)


def listing(i, title, price, cond="Gebruikt"):
    return {"itemId": f"m{i}", "title": title, "vipUrl": f"/v/{i}",
            "priceInfo": {"priceType": "FIXED", "priceCents": price * 100},
            "attributes": [{"key": "condition", "value": cond}]}


def test_plan_from_uses_base_model_and_type():
    plan = ai_query.plan_from({"brand": "Makita", "model": "DGA504", "type": "haakse slijper", "part": False})
    assert plan.searches == ["makita dga504 haakse slijper", "makita dga504"]
    rule = plan.rules[0]
    assert rule.matches("makita dga504rtj accu haakse slijper") and not rule.matches("makita dga506 slijper")
    assert not rule.parts_ok


def test_plan_from_needs_a_type_number():
    assert ai_query.plan_from({"brand": "dyson", "model": "Airwrap", "type": "styler", "part": False}) is None
    assert ai_query.plan_from({"brand": "makita", "model": None, "type": "set", "part": False}) is None
    assert ai_query.plan_from(None) is None


def test_answers_are_saved_by_text_hash_and_reused():
    cache, l1 = {}, lot(1, "Accu haakse slijper Makita, DGA504RTJ")
    client = Client([{"i": 0, "brand": "makita", "model": "DGA504", "type": "haakse slijper", "part": False}])
    got = ai_query.answers_for([l1], cache, NOW, client, 10)
    assert got["hnvi:1"]["model"] == "DGA504" and len(client.calls) == 1
    assert "hnvi:1" not in json.dumps(cache) and "DGA504RTJ" not in json.dumps(cache)  # no lot keys or titles
    again = ai_query.answers_for([l1], cache, NOW + timedelta(days=2), client, 10)
    assert again["hnvi:1"]["model"] == "DGA504" and len(client.calls) == 1
    ai_query.prune(cache, NOW + timedelta(days=40))
    assert cache == {}


def test_reprice_prices_a_lot_the_bot_could_not(monkeypatch):
    results = [listing(1, "Makita DGA504RTJ haakse slijper", 90), listing(2, "Makita DGA504 18V slijper", 110),
               listing(3, "Makita DGA506 slijper", 300)]
    monkeypatch.setattr(marktplaats, "search", lambda http, q: results)
    finder = PriceFinder(None, {}, NOW, max_lookups=10)
    item = WatchItem.from_dict({"name": "Power tools", "keywords": ["makita"]})
    l1, l2 = lot(1, "Accu haakse slijper Makita, DGA504RTJ"), lot(2, "Makita koffer")
    rows = [(item, l1, "old", None), (item, l2, "old2", None)]
    client = Client([{"i": 0, "brand": "makita", "model": "DGA504", "type": "haakse slijper", "part": False},
                     {"i": 1, "brand": "makita", "model": None, "type": "koffer", "part": True}])
    out, plans = ai_query.reprice(rows, finder, {}, NOW, client, lambda i, l, e: ("new", e.median),
                                  lambda l: True)
    assert out[0][2] == ("new", 100.0) and out[0][3].count == 2
    assert out[1][2] == "old2" and out[1][3] is None
    assert list(plans) == ["hnvi:1"]


def test_reprice_skips_far_lots_and_lots_with_a_price():
    item = WatchItem.from_dict({"name": "Power tools", "keywords": ["makita"]})
    client = Client([])
    rows = [(item, lot(1, "a"), "v", None), (item, lot(2, "b"), "v", "has price")]
    out, plans = ai_query.reprice(rows, None, {}, NOW, client, None, lambda l: False)
    assert out == rows and plans == {} and client.calls == []
