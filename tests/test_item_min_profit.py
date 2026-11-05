from scanner.evaluate import Fees, Settings, evaluate
from scanner.models import Lot, WatchItem


def test_item_min_profit_overrides_global():
    import inspect
    assert "min_profit" in inspect.signature(WatchItem).parameters
    s = Settings(min_margin=0.15, resale_factor=1.0, min_profit=25)
    assert WatchItem.from_dict({"name": "x", "min_profit": 50}).min_profit == 50
    assert WatchItem.from_dict({"name": "x", "min_profit": 50}).to_dict()["min_profit"] == 50


def test_lot_floor_setting():
    s = Settings.from_dict({"min_profit": 25, "min_lot_profit": 50})
    assert s.min_lot_profit == 50 and "€50 per lot" in s.describe_margin()


def test_monthly_goal_setting():
    assert Settings.from_dict({"monthly_goal": 1000}).monthly_goal == 1000
    assert Settings.from_dict({}).monthly_goal == 0
