"""Cordless tools "zonder accu" are compared with bare machines only (Diede, 25 Nov 2026: a MAKITA DDF484 zonder accu
lot was priced on listings with a 3Ah battery)."""
import pytest

from fixtures import mp_listing
from scanner import advice_ai, marktplaats
from scanner.identify import battery_kit, plan_for
from scanner.models import Lot, WatchItem


@pytest.mark.parametrize("text,item,kit", [
    ("Accu schroefboormachine MAKITA DDF484 zonder accu", "Power tools", "bare"),
    ("Accu schroefboormachine MAKITA DDF484", "Power tools", None),
    ("Makita DDF484 met 3Ah accu, nette staat", None, "battery"),
    ("Makita DDF484 18V body", None, "bare"),
    ("Makita DDF484 solo 18V", None, "bare"),
    ("Makita DDF484Z", None, "bare"),
    ("Makita DDF484RTJ", None, "battery"),
    ("Makita DDF484 + 2x 5.0Ah accu's en lader", None, "battery"),
    ("Makita DDF484 zonder accu en lader", None, "bare"),
    ("Makita DDF484 boormachine, accu en lader niet inbegrepen", None, "bare"),
    ("Makita DHR243 boorhamer excl. accu", None, "bare"),
    ("Makita DDF484 accu schroefboormachine", None, None),
    ("Nikon D7000 body", "Camera", None),
])
def test_battery_kit(text, item, kit):
    assert battery_kit(text, item) == kit


LISTINGS = [mp_listing("Makita DDF484 zonder accu", 4000), mp_listing("Makita DDF484 body 18V", 4500),
            mp_listing("Makita DDF484Z boormachine", 5000), mp_listing("Makita DDF484 solo", 5500),
            mp_listing("Makita DDF484 met 3Ah accu", 9000), mp_listing("Makita DDF484 + 2 accu's en lader", 12000),
            mp_listing("Makita DDF484RTJ", 15000)]
TOOLS = WatchItem(name="Power tools", keywords=["makita"])


def prices(title):
    plan = plan_for(TOOLS, Lot("hnvi", "1", title, "u", 1, None))
    est, _ = marktplaats.best_estimate([dict(li, itemId=f"m{i}") for i, li in enumerate(LISTINGS)],
                                       plan.rules, TOOLS.exclude, 2)
    return est.prices


def test_bare_lot_only_compares_with_bare_listings():
    assert prices("Accu schroefboormachine MAKITA DDF484 zonder accu") == [40, 45, 50, 55]


def test_lot_with_battery_leaves_bare_listings_out():
    assert prices("MAKITA DDF484 accuboormachine met 2 accu's") == [90, 120, 150]


def test_lot_that_does_not_say_compares_with_all():
    assert len(prices("Accu schroefboormachine MAKITA DDF484")) == 7


def test_claude_keeping_listings_with_battery_for_a_bare_lot_is_overruled():
    lot = {"title": "Accu schroefboormachine MAKITA DDF484 zonder accu", "item": "Power tools"}
    assert "WITHOUT battery" in advice_ai.prompt(lot, [])
    listings = [{"price": 40, "title": "Makita DDF484 zonder accu"}, {"price": 88, "title": "Makita DDF484 met 3Ah accu"},
                {"price": 90, "title": "DDF484 met accu"}]
    j = advice_ai.drop_wrong_kit({"keep": [1, 2], "verdict": "kopen", "reason": "x"}, lot, listings)
    assert j["keep"] == [] and j["verdict"] == "twijfel" and "zonder accu" in j["reason"]
