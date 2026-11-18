"""Totals of your private stock (data/stock.json in the private repository) for the wealth overview.

Same sums as the Finance tab of the dashboard (scanner/dashboard_private.js):
  earned  = what paid sales brought in minus what the sold part cost you
  saved   = new price minus cost, only on what you kept for yourself (a partly sold lot: the kept share)
  inStock = paid for what's still for sale
  byMonth = profit of paid sales per calendar month (sales without a date go in undatedProfit)
  capital = working capital as on the Finance tab: stock at cost + all profit, without personal items,
            next to settings.capital_goal and settings.monthly_goal from watchlist.yml (optional third argument)

A workflow in the private repository runs this after every change of data/stock.json and saves the result
as data/summary.json; a daily scheduled task copies that small file into the wealth overview.

    python -m scanner.finance_summary data/stock.json data/summary.json [watchlist.yml]
"""
import json
import re
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


def _by_month(items):
    months, undated = {}, 0.0
    for it in items:
        if it.get("personal"):
            continue
        for s in _sales(it):
            if s.get("unpaid"):
                continue
            price = float(s.get("price") or 0)
            profit = price - _sale_cost(it, s)
            day = str(s.get("date") or "")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                undated += profit
                continue
            m = months.setdefault(day[:7], {"month": day[:7], "profit": 0.0, "soldFor": 0.0, "sales": 0})
            m["profit"] += profit
            m["soldFor"] += price
            m["sales"] += 1
    rows = [{**m, "profit": round(m["profit"], 2), "soldFor": round(m["soldFor"], 2)} for _, m in sorted(months.items())]
    return rows, round(undated, 2)


def _capital(items, settings):
    stock = sum(_in_stock(it) for it in items if not it.get("personal"))
    profit = sum(float(s.get("price") or 0) - _sale_cost(it, s)
                 for it in items if not it.get("personal") for s in _sales(it) if not s.get("unpaid"))
    out = {"inStock": round(stock, 2), "profit": round(profit, 2), "have": round(stock + profit, 2)}
    for key, name in (("capital_goal", "goal"), ("monthly_goal", "monthlyGoal")):
        if (settings or {}).get(key):
            out[name] = float(settings[key])
    return out


def read_goals(path):
    """capital_goal and monthly_goal from the settings block of watchlist.yml (no YAML library needed)"""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return {}
    out = {}
    for key in ("capital_goal", "monthly_goal"):
        m = re.search(r"^[ \t]+" + key + r":[ \t]*([0-9]+(?:\.[0-9]+)?)", text, re.M)
        if m:
            out[key] = float(m.group(1))
    return out


def summarize(data, now=None, settings=None):
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
    by_month, undated = _by_month(items)
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
        # per item, for the charts in the wealth overview (the private repository only)
        "keptItems": sorted(({"name": it.get("name") or "", "paid": r(_kept_new_value(it) - _saved(it)), "newValue": r(_kept_new_value(it))}
                             for it in items if _saved(it) is not None), key=lambda x: x["paid"] - x["newValue"]),
        "soldItems": sorted(_sold_rows(items), key=lambda x: x["cost"] - x["price"]),
        "byMonth": by_month,
        "undatedProfit": undated,
        "capital": _capital(items, settings),
    }


def _sold_rows(items):
    for it in items:
        if it.get("personal"):
            continue
        paid_sales = [s for s in _sales(it) if not s.get("unpaid")]
        if paid_sales:
            yield {"name": it.get("name") or "", "cost": round(sum(_sale_cost(it, s) for s in paid_sales), 2),
                   "price": round(sum(float(s.get("price") or 0) for s in paid_sales), 2)}


def main(argv):
    src, dst = argv[1], argv[2]
    settings = read_goals(argv[3]) if len(argv) > 3 else None
    with open(src, encoding="utf-8") as f:
        summary = summarize(json.load(f), settings=settings)
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
