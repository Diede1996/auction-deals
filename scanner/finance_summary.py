"""Totals of your private stock (data/stock.json in the private repository) for the wealth overview.

Same sums as the Finance tab of the dashboard (scanner/dashboard_private.js):
  earned  = what paid sales brought in minus what the sold part cost you
  saved   = new price minus cost, only on what you kept for yourself (a partly sold lot: the kept share)
  inStock = paid for what's still for sale

A workflow in the private repository runs this after every change of data/stock.json and saves the result
as data/summary.json; a daily scheduled task copies that small file into the wealth overview.

    python -m scanner.finance_summary data/stock.json data/summary.json
"""
import json
import sys
from datetime import datetime, timezone

STATUSES = ("stock", "kept", "sold")


def _status(it):
    return it.get("status") if it.get("status") in STATUSES else "stock"


def _qty(it):
    return max(1, round(it.get("qty") or 1))


def _sales(it):
    return it.get("sales") if isinstance(it.get("sales"), list) else []


def _sold_qty(it):
    return sum(s.get("qty") or 1 for s in _sales(it))


def _cost_of_sold(it):
    if not _sales(it):
        return 0.0
    if it.get("soldCost") is not None:
        return float(it["soldCost"])
    paid = float(it.get("paid") or 0)
    return paid * min(1, _sold_qty(it) / _qty(it)) if _qty(it) > 1 else paid


def _sale_cost(it, sale):
    q = _sold_qty(it)
    return _cost_of_sold(it) * (sale.get("qty") or 1) / q if q else 0.0


def _saved(it):
    if it.get("newPrice") is None or _status(it) != "kept":
        return None
    if _qty(it) > 1:
        kept_share = max(0.0, 1 - _sold_qty(it) / _qty(it))
    else:
        kept_share = 0.0 if _sales(it) else 1.0
    return float(it["newPrice"]) * kept_share - max(0.0, float(it.get("paid") or 0) - _cost_of_sold(it))


def _kept_new_value(it):
    """the new price of what you kept: the base of the savings percentage"""
    if it.get("newPrice") is None or _status(it) != "kept":
        return None
    if _qty(it) > 1:
        return float(it["newPrice"]) * max(0.0, 1 - _sold_qty(it) / _qty(it))
    return 0.0 if _sales(it) else float(it["newPrice"])


def _in_stock(it):
    return max(0.0, float(it.get("paid") or 0) - _cost_of_sold(it)) if _status(it) == "stock" else 0.0


def summarize(data, now=None):
    items = data.get("items") or []
    revenue = cost = waiting = 0.0
    sales_count = 0
    for it in items:
        if it.get("personal"):  # bought for yourself, sold later to get rid of it: not resale profit
            continue
        for s in _sales(it):
            price, c = float(s.get("price") or 0), _sale_cost(it, s)
            if s.get("unpaid"):
                waiting += price - c
            else:
                revenue += price
                cost += c
                sales_count += 1
    saved_items = [v for v in (_saved(it) for it in items) if v is not None]
    new_value = sum(v for v in (_kept_new_value(it) for it in items) if v is not None)
    r = lambda x: round(x, 2)  # noqa: E731
    return {
        "updatedAt": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "earned": r(revenue - cost),
        "soldFor": r(revenue),
        "costOfSold": r(cost),
        "earnedPctOfCost": r((revenue - cost) / cost * 100) if cost else None,
        "marginPctOfPrice": r((revenue - cost) / revenue * 100) if revenue else None,
        "sales": sales_count,
        "earnedWhenPaid": r(waiting),
        "saved": r(sum(saved_items)),
        "savedItems": len(saved_items),
        "keptNewValue": r(new_value),
        "savedPct": r(sum(saved_items) / new_value * 100) if new_value else None,
        "moneyInStock": r(sum(_in_stock(it) for it in items)),
        "paidTotal": r(sum(float(it.get("paid") or 0) for it in items)),
        "items": len(items),
        "itemsByStatus": {s: sum(1 for it in items if _status(it) == s) for s in STATUSES},
    }


def main(argv):
    src, dst = argv[1], argv[2]
    with open(src, encoding="utf-8") as f:
        summary = summarize(json.load(f))
    try:
        with open(dst, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = None
    # only the time changed: leave the file alone, so no empty commit
    if old and {k: v for k, v in old.items() if k != "updatedAt"} == {k: v for k, v in summary.items() if k != "updatedAt"}:
        return 0
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
