"""Totals for the wealth overview: same sums as the dashboard's Finance tab."""
from datetime import datetime, timezone

from scanner.finance_summary import read_goals, summarize

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


def test_by_month_and_capital(tmp_path):
    data = {"items": [
        {"status": "sold", "paid": 100, "sales": [{"price": 250, "date": "2026-11-16"}]},
        {"status": "sold", "paid": 40, "sales": [{"price": 90}]},  # no date
        {"status": "sold", "paid": 10, "sales": [{"price": 30, "date": "2026-10-02"}, ]},
        {"status": "stock", "paid": 60, "sales": [{"price": 99, "date": "2026-11-17", "unpaid": True}]},
        {"status": "stock", "paid": 200},
        {"status": "stock", "paid": 500, "personal": True},
        {"status": "sold", "paid": 5, "personal": True, "sales": [{"price": 50, "date": "2026-11-03"}]},
    ]}
    w = tmp_path / "watchlist.yml"
    w.write_text("settings:\n  monthly_goal: 500  # note\n  capital_goal: 5000\nitems: []\n")
    s = summarize(data, NOW, read_goals(str(w)))
    assert s["byMonth"] == [{"month": "2026-10", "profit": 20, "soldFor": 30, "sales": 1},
                           {"month": "2026-11", "profit": 150, "soldFor": 250, "sales": 1}]
    assert s["undatedProfit"] == 50
    assert s["capital"] == {"inStock": 200, "profit": 220, "have": 420, "goal": 5000, "monthlyGoal": 500}
    assert "goal" not in summarize(data, NOW)["capital"]


def test_real_profit_only_after_break_even():
    """Diede 2026-11-22: a lot only makes profit once everything it cost has come back"""
    lot = {"id": "hugo", "name": "HUGO"}
    data = {"items": [
        {"id": "a", "status": "sold", "paid": 60, "invoice": "INV1", "lotOf": lot, "sales": [{"price": 110, "date": "2026-11-10"}]},
        {"id": "b", "status": "sold", "paid": 60, "invoice": "INV1", "lotOf": lot, "sales": [{"price": 120, "date": "2026-11-20"}]},
        {"id": "c", "status": "stock", "paid": 180, "invoice": "INV1", "lotOf": lot},
        {"id": "d", "status": "kept", "paid": 50, "invoice": "INV1", "lotOf": lot, "newPrice": 300},
    ]}
    s = summarize(data, NOW)
    assert s["earnedPerPiece"] == 110
    assert s["earned"] == 0  # 230 of 300 back (the kept piece is not counted)
    [row] = s["lots"]
    assert row["cost"] == 300 and row["back"] == 230 and not row["paidBack"] and row["keptCost"] == 50
    data["items"][2]["sales"] = [{"price": 100, "date": "2026-11-25"}]
    data["items"][2]["status"] = "sold"
    s = summarize(data, NOW)
    assert s["earned"] == 30
    assert s["byMonth"] == [{"month": "2026-11", "profit": 30, "soldFor": 330, "sales": 3}]


def test_loss_counts_once_nothing_is_left():
    data = {"items": [
        {"id": "a", "status": "sold", "paid": 100, "invoice": "X", "sales": [{"price": 30, "date": "2026-11-01"}]},
        {"id": "b", "status": "stock", "paid": 100, "invoice": "X"},
    ]}
    assert summarize(data, NOW)["earned"] == 0
    data["items"][1].update(status="sold", sales=[{"price": 50, "date": "2026-11-02"}])
    assert summarize(data, NOW)["earned"] == -120
