import pytest
from scanner import marktplaats
from scanner.commands import handle
from scanner.evaluate import Fees, Settings, evaluate
from scanner.models import Lot, WatchItem

TIERS = {"min_margin": 0.25, "resale_factor": 0.75, "margin_tiers": [
    {"up_to": 250, "min_margin": 0.4}, {"up_to": 100, "min_margin": 0.5}, {"up_to": 500, "min_margin": 0.3}]}


def test_margin_for_picks_the_first_tier_that_fits():
    s = Settings.from_dict(TIERS)
    assert s.margin_tiers == ((100, 0.5), (250, 0.4), (500, 0.3))  # sorted, whatever the order in the file
    assert [s.margin_for(x) for x in (40, 100, 101, 250, 400, 500, 501, None)] == \
        [0.5, 0.5, 0.4, 0.4, 0.3, 0.3, 0.25, 0.25]
    assert s.describe_margin() == "50% margin up to €100, 40% margin up to €250, 30% margin up to €500 per item, 25% above"
    assert Settings.from_dict({"min_margin": 0.5}).describe_margin() == "50% margin"


def test_max_bid_uses_the_tier_of_the_sale_price_per_item():
    s, fees = Settings.from_dict(TIERS), Fees(premium=0.0, vat=0.0)
    item = WatchItem(name="X", keywords=["x"])
    est = lambda median: marktplaats.PriceEstimate(median=median, low=median, high=median, count=5, query="x")
    lot = Lot("s", "1", "x", "u", 1.0, None)
    # sells for 75% of 100 = 75 -> 50%: pay at most 50
    assert evaluate(item, lot, fees, s, est(100)).max_bid == 50
    # 75% of 1000 = 750 -> 25%: pay at most 600
    assert evaluate(item, lot, fees, s, est(1000)).max_bid == 600
    # 10 items of 100 each: the tier follows the price per item (75 -> 50%), not the lot's 750
    assert evaluate(item, lot, fees, s, est(100), units=10).max_bid == 500
    # an item's own margin still wins
    own = WatchItem(name="X", keywords=["x"], min_margin=0.1)
    assert evaluate(own, lot, fees, s, est(100)).max_bid == pytest.approx(68, abs=1)


def test_list_and_minmargin_mention_the_tiers():
    wl = {"settings": dict(TIERS), "items": [{"name": "Monitor", "keywords": ["monitor"]}]}
    text, _ = handle("/list", wl, "")
    assert "50% margin up to €100" in text and "25% above" in text
    text, changed = handle("/minmargin 20", wl, "")
    assert changed and wl["settings"]["min_margin"] == 0.2 and "50% up to €100" in text
    assert len(wl["settings"]["margin_tiers"]) == 3
