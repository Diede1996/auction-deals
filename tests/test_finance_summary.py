"""Totals for the wealth overview: same sums as the dashboard's Finance tab."""
from datetime import datetime, timezone

from scanner.finance_summary import summarize

NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)


def test_totals():
    data = {"items": [
        {"status": "sold", "paid": 50.4, "qty": 4, "sales": [{"price": 90, "qty": 4}]},
        {"status": "kept", "paid": 36, "qty": 2, "newPrice": 60, "soldCost": 18, "sales": [{"price": 50, "qty": 1}]},
        {"status": "kept", "paid": 100, "newPrice": 400},
        {"status": "stock", "paid": 200, "sales": [{"price": 300, "unpaid": True}]},
        {"status": "stock", "paid": 80},
    ]}
    s = summarize(data, NOW)
    assert s["earned"] == round(90 - 50.4 + 50 - 18, 2)
    assert s["saved"] == round((60 * 0.5 - 18) + 300, 2)
    assert s["savedItems"] == 2
    assert s["keptNewValue"] == 430
    assert s["savedPct"] == round(312 / 430 * 100, 2)
    assert s["marginPctOfPrice"] == round(s["earned"] / 140 * 100, 2)
    assert s["earnedWhenPaid"] == 100
    assert s["moneyInStock"] == 80  # the unpaid sale already counts as sold
    assert s["updatedAt"] == "2026-11-03T00:00:00Z"


def test_personal_item_sold_later_is_not_profit():
    data = {"items": [{"status": "sold", "paid": 57.6, "personal": True, "sales": [{"price": 50}]}]}
    s = summarize(data, NOW)
    assert s["earned"] == 0 and s["sales"] == 0 and s["saved"] == 0
