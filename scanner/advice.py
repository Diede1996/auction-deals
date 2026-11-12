"""Claude's buy advice per lot (Diede, 12 Nov 2026).

The bot's own rules decide which Marktplaats listings count as "the same thing" as a lot, and they get it wrong too
often (only a battery, a broken one, another version). Once a day a scheduled Claude session reads the listings of
the promising lots, says which ones really compare, and writes a short advice. This module does the bookkeeping
around that, so the session only has to judge:

  python -m scanner.advice prepare --private ../auction-private --out advice-input.json
      picks the lots worth a look (favorites, lots you follow, lots with room to bid or close to it, closing within
      a week; no wine, spirits or mixed lots), numbers their Marktplaats listings and leaves out lots judged
      before whose listings haven't changed.
  (Claude reads advice-input.json and writes advice-judgments.json, see JUDGMENT below)
  python -m scanner.advice apply --private ../auction-private --judgments advice-judgments.json
      works out the price from the listings Claude kept (the median, like the bot does: Claude never makes up a
      price) and a max bid with the same margin rules as the bot, and saves data/claude-advice.json in the private
      repository. The dashboard shows it on each lot.

JUDGMENT (one entry per lot key):
  {"<key>": {"keep": [0, 2, 5],            # numbers of the listings that really are the same thing
             "model": "Makita DHR243",       # what the lot is, as precisely as the lot says
             "confidence": "hoog",           # hoog / middel / laag
             "verdict": "kopen",             # kopen / twijfel / niet
             "reason": "...",                # one or two sentences, Dutch
             "risks": "...",                 # optional, Dutch
             "dropUnseen": true,             # optional: the listings beyond the 30 shown don't count either
                                             # (when the search mixes in other models or years)
             "listings": [{"price": 120, "title": "...", "url": "..."}]}}   # only for a lot without listings,
                                                                             # from a Price lookup run
For a lot with "sameListingsAs" (an identical lot), {"sameAs": "<that key>"} copies that lot's judgment; add
fields to change some ("reason", "keep"). A lot judged "niet" or without at least 2 kept listings gets no max bid.
The same steps work with an API key later: only who writes advice-judgments.json changes.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ADVICE_PATH = "data/claude-advice.json"
SKIP_ITEMS = {"Wine", "Spirits", "Mixed lot"}
WINDOW_HOURS = 24 * 7  # lots closing within a week (judged once, then reused while the listings stay the same)
MAX_JUDGE = 40  # lots to judge per run (Diede's Pro limit)
MAX_UNPRICED = 5  # favorites without Marktplaats listings: Claude runs a Price lookup for these
NEAR_MAX = 1.3  # "close to room to bid": the next bid is at most 30% above the max bid
KEEP_DAYS = 3  # advice of closed lots stays this long
MAX_LOOKUPS = 10  # Marktplaats searches per "Advice lookup" run
MIN_LISTINGS = 2  # fewer comparable listings than this: no price (as the bot does for an exact model)


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _closes(lot: dict) -> datetime | None:
    if lot.get("closes"):
        try:
            return datetime.fromisoformat(lot["closes"])
        except ValueError:
            return None
    if lot.get("closesDay"):
        try:
            return datetime.fromisoformat(lot["closesDay"] + "T22:59:00+00:00")
        except ValueError:
            return None
    return None


def all_lots(root: Path, private: Path | None) -> tuple[dict, list[dict]]:
    data = _load(root / "data" / "lots.json") or {}
    lots = list(data.get("lots") or [])
    if private:
        have = {lot.get("key") for lot in lots}
        extra = _load(private / "data" / "troostwijk-lots.json") or {}
        lots += [lot for lot in extra.get("lots") or [] if lot.get("key") not in have]
    return data, lots


def favorite_keys(data: dict) -> set[str]:
    return {f.get("key") for f in (data.get("favorites") or {}).get("items") or [] if f.get("key")}


def next_bid(lot: dict) -> float | None:
    for k in ("nextBid", "bid"):
        if lot.get(k) is not None:
            return float(lot[k])
    return None


def candidates(data: dict, lots: list[dict], advice: dict, now: datetime) -> tuple[list[dict], list[dict]]:
    """(lots to judge, favorites without listings), most important first."""
    favs = favorite_keys(data)
    old = (advice or {}).get("lots") or {}
    priced, unpriced = [], []
    for lot in lots:
        if lot.get("item") in SKIP_ITEMS or lot.get("repairOf") in SKIP_ITEMS:
            continue
        closes = _closes(lot)
        if not closes or closes < now or closes > now + timedelta(hours=WINDOW_HOURS):
            continue
        fav = lot.get("key") in favs or bool(lot.get("followed"))
        mp = lot.get("mp") or {}
        nb, mx = next_bid(lot), lot.get("maxBid")
        near = bool(lot.get("isDeal")) or (mx and nb is not None and nb <= mx * NEAR_MAX)
        if not (fav or near):
            continue
        prev = old.get(lot["key"])
        if mp.get("listings"):
            if prev and prev.get("asOf") == mp.get("asOf"):
                continue  # judged before on the same listings: apply() reuses it with today's bid
            priced.append((not fav, closes, lot))
        elif fav and not prev:
            unpriced.append((closes, lot))
    priced.sort(key=lambda t: (t[0], t[1]))
    unpriced.sort(key=lambda t: t[0])
    return [t[2] for t in priced[:MAX_JUDGE]], [t[1] for t in unpriced[:MAX_UNPRICED]]


def _brief(lot: dict) -> dict:
    return {k: lot.get(k) for k in ("key", "title", "item", "siteName", "auction", "closes", "bid", "nextBid",
                                     "units", "count", "maxBid", "url") if lot.get(k) is not None}


def prepare(root: Path, private: Path | None, now: datetime) -> dict:
    data, lots = all_lots(root, private)
    advice = _load(private / ADVICE_PATH) if private else None
    judge, unpriced = candidates(data, lots, advice or {}, now)
    out = {"generated": now.isoformat(), "instructions": __doc__.split("JUDGMENT")[1].strip(), "lots": [],
           "unpriced": [_brief(lot) for lot in unpriced]}
    first_with: dict[str, str] = {}  # identical lots (ten of the same laptop) share their listings
    for lot in judge:
        mp = lot["mp"]
        entry = _brief(lot)
        same = json.dumps([(li.get("price"), li.get("title")) for li in mp.get("listings") or []])
        if same in first_with:
            entry["marktplaats"] = {"sameListingsAs": first_with[same], "botMedian": mp.get("median")}
            out["lots"].append(entry)
            continue
        first_with[same] = lot["key"]
        entry["marktplaats"] = {
            "search": mp.get("query"), "model": mp.get("model"), "botMedian": mp.get("median"),
            "botCount": mp.get("count"),
            "listings": [dict({"n": i, "price": li.get("price"), "title": li.get("title")},
                              **({"of": li["of"]} if li.get("of") else {}),
                              **({"bid": True} if li.get("kind") == "bid" else {}))
                         for i, li in enumerate(mp.get("listings") or [])],
            "notShown": max(0, len(mp.get("prices") or []) - len(mp.get("listings") or [])),
        }
        out["lots"].append(entry)
    return out


# ---------- prices and max bids, the same rules as evaluate.py ----------

def _margin(lot: dict, settings: dict, sale_each: float | None, sale_total: float) -> float:
    if lot.get("itemMinMargin") is not None:
        return float(lot["itemMinMargin"])
    if lot.get("repair"):
        return 1.0
    if lot.get("transport") and settings.get("transport_margin") is not None \
            and sale_total >= float(settings.get("transport_min_value") or 0):
        return float(settings["transport_margin"])
    for tier in settings.get("margin_tiers") or []:
        if sale_each is not None and sale_each <= float(tier[0] if isinstance(tier, (list, tuple)) else tier["up_to"]):
            return float(tier[1] if isinstance(tier, (list, tuple)) else tier["min_margin"])
    return float(settings.get("min_margin", 0.25))


def _caps(lot: dict, settings: dict, market: float) -> float | None:
    units = max(1, int(lot.get("units") or 1))
    factor = float(settings.get("resale_factor", 0.75))
    resale = market * factor - float(settings.get("selling_costs") or 0)
    margin = _margin(lot, settings, market / units * factor, market * factor)
    caps = [resale / (1 + max(0.0, margin))]
    min_profit = lot.get("itemMinProfit")
    min_profit = float(min_profit if min_profit is not None else settings.get("min_profit") or 0)
    floor = max(min_profit * units, float(settings.get("min_lot_profit") or 0))
    if floor > 0:
        caps.append(resale - floor)
    if lot.get("itemMaxPrice") is not None:
        caps.append(float(lot["itemMaxPrice"]))
    return min(caps) if caps else None


def _rates(lot: dict) -> tuple[float, float, float]:
    return float(lot.get("premium") or 0), float(lot.get("vat") or 0), float(lot.get("fixed") or 0)


def trip_share(lot: dict, settings: dict) -> float:
    """The drive (or transport) this lot carries. The bot doesn't save it per lot, so it's worked back from the
    bot's own max bid; without one, the whole trip."""
    premium, vat, fixed = _rates(lot)
    mx, market = lot.get("maxBid"), lot.get("market")
    if mx and mx > 0 and market:
        cap = _caps(lot, settings, float(market))
        if cap is not None:
            return max(0.0, cap - (float(mx) * (1 + premium) + fixed) * (1 + vat))
    return float((lot.get("trip") or {}).get("cost") or 0)


def max_bid(lot: dict, settings: dict, market: float) -> int:
    premium, vat, fixed = _rates(lot)
    cap = _caps(lot, settings, market)
    if cap is None:
        return 0
    return max(0, math.floor(((cap - trip_share(lot, settings)) / (1 + vat) - fixed) / (1 + premium)))


def price_from(lot: dict, judgment: dict) -> tuple[float | None, int, int]:
    """(median, listings used, listings looked at) from the listings Claude kept. Prices the dashboard doesn't
    list (the dearer ones beyond the 30 cheapest) are counted as the bot counted them."""
    if judgment.get("listings"):  # a lot without Marktplaats listings: Claude's own Price lookup
        prices = [float(x["price"]) for x in judgment["listings"] if x.get("price")]
        return (round(statistics.median(prices), 2) if len(prices) >= MIN_LISTINGS else None), len(prices), len(prices)
    mp = lot.get("mp") or {}
    shown = mp.get("listings") or []
    keep = {int(i) for i in judgment.get("keep") or [] if str(i).lstrip("-").isdigit()}
    prices = [float(li["price"]) for i, li in enumerate(shown) if i in keep and li.get("price")]
    rest = [] if judgment.get("dropUnseen") else sorted(mp.get("prices") or [])[len(shown):]
    prices += [float(p) for p in rest]
    if len(prices) < MIN_LISTINGS:
        return None, len(prices), len(shown) + len(rest)
    return (round(statistics.median(prices), 2) if prices else None), len(prices), len(shown) + len(rest)


def label(verdict: str | None, room: bool | None, price: float | None) -> str:
    v = (verdict or "").lower()
    if v.startswith("niet"):
        return "niet"
    if price is None:
        return "twijfel"
    if room is False:
        return "te duur"
    return "kopen" if v.startswith("kopen") else "twijfel"


def apply(root: Path, private: Path, judgments: dict, now: datetime) -> dict:
    data, lots = all_lots(root, private)
    settings = data.get("settings") or {}
    by_key = {lot.get("key"): lot for lot in lots}
    advice = _load(private / ADVICE_PATH) or {}
    old = advice.get("lots") or {}
    out = {}
    # earlier advice: today's bid and max bid; drop lots that closed a while ago
    for key, a in old.items():
        lot = by_key.get(key)
        closes = datetime.fromisoformat(a["closes"]) if a.get("closes") else None
        if closes and closes < now - timedelta(days=KEEP_DAYS):
            continue
        if lot and a.get("price") is not None:
            units = max(1, int(lot.get("units") or 1))
            a["maxBid"] = max_bid(lot, settings, a["price"] * units)
            nb = next_bid(lot)
            a["nextBid"] = nb
            a["label"] = label(a.get("verdict"), None if nb is None else nb <= a["maxBid"], a["price"])
        out[key] = a
    for key, j in (judgments or {}).items():
        lot = by_key.get(key)
        if not lot or not isinstance(j, dict):
            continue
        if j.get("sameAs") and isinstance(judgments.get(j["sameAs"]), dict):  # the same thing as another lot
            j = dict(judgments[j["sameAs"]], **{k: v for k, v in j.items() if k != "sameAs"})
        price, used, seen = price_from(lot, j)
        units = max(1, int(lot.get("units") or 1))
        mx = max_bid(lot, settings, price * units) if price is not None else None
        nb = next_bid(lot)
        room = None if mx is None or nb is None else nb <= mx
        out[key] = {
            "label": label(j.get("verdict"), room, price), "verdict": j.get("verdict"),
            "confidence": j.get("confidence"), "model": j.get("model"),
            "reason": str(j.get("reason") or "")[:400], "risks": str(j.get("risks") or "")[:300],
            "price": price, "used": used, "seen": seen, "botPrice": (lot.get("mp") or {}).get("median"),
            "botMaxBid": lot.get("maxBid"), "maxBid": mx, "nextBid": nb,
            "asOf": (lot.get("mp") or {}).get("asOf"), "closes": lot.get("closes") or (
                lot["closesDay"] + "T22:59:00+00:00" if lot.get("closesDay") else None),
            "judgedAt": now.isoformat(), "title": lot.get("title"),
        }
    result = {"generated": now.isoformat(), "lots": out}
    (private / "data").mkdir(parents=True, exist_ok=True)
    (private / ADVICE_PATH).write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    return result


def lookup(queries: list[str], now: datetime) -> dict:
    """Marktplaats searches for lots the bot couldn't price (workflow "Advice lookup"), kept short so the Claude
    session can read them: working, priced listings by private sellers, cheapest first."""
    import html as _html
    import time

    from .http import BlockedError, Http
    from .marktplaats import PRICED_TYPES, condition_of, is_business, search

    http, out = Http(), {}
    for q in queries[:MAX_LOOKUPS]:
        try:
            found = search(http, q)
        except BlockedError as e:
            out[q] = {"error": f"blocked: {e}"}
            break
        except Exception as e:  # one failed search shouldn't lose the others
            out[q] = {"error": str(e)[:200]}
            continue
        rows = []
        for li in found:
            info = li.get("priceInfo") or {}
            if info.get("priceType") not in PRICED_TYPES or not info.get("priceCents") or is_business(li):
                continue
            cond = condition_of(li)
            if cond in ("niet werkend", "defect"):
                continue
            vip = li.get("vipUrl") or ""
            rows.append({"price": round(info["priceCents"] / 100, 2),
                         "title": _html.unescape(li.get("title") or "").strip()[:90],
                         "desc": _html.unescape(li.get("description") or "").replace("\n", " ").strip()[:120],
                         **({"cond": cond} if cond else {}),
                         **({"bid": True} if info.get("priceType") == "MIN_BID" else {}),
                         "url": ("https://www.marktplaats.nl" + vip) if vip.startswith("/") else vip})
        rows.sort(key=lambda r: r["price"])
        out[q] = {"listings": rows[:40]}
        time.sleep(4)
    return {"generated": now.isoformat(), "queries": out}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=["prepare", "apply", "lookup"])
    ap.add_argument("--root", default=".", help="this repository")
    ap.add_argument("--private", help="a checkout of the private repository (auction-private)")
    ap.add_argument("--queries", default="", help="lookup: Marktplaats searches separated by ;")
    ap.add_argument("--out", default="advice-input.json")
    ap.add_argument("--judgments", default="advice-judgments.json")
    args = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    if args.mode == "lookup":  # in GitHub Actions: Marktplaats -> data/advice-lookups.json in the private repository
        from . import private_repo
        res = lookup([q.strip() for q in args.queries.split(";") if q.strip()], now)
        for q, r in res["queries"].items():
            print(q, "->", r.get("error") or f"{len(r['listings'])} listings")
        if not private_repo.put_json("data/advice-lookups.json", res, "Advice lookups"):
            print(json.dumps(res, ensure_ascii=False))
        return 0
    if not args.private:
        ap.error("--private is needed")
    root, private = Path(args.root), Path(args.private)
    if args.mode == "prepare":
        out = prepare(root, private, now)
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
        print(f"{len(out['lots'])} lots to judge, {len(out['unpriced'])} favorites without listings -> {args.out}")
        return 0
    judgments = _load(Path(args.judgments))
    if judgments is None:
        print(f"no judgments in {args.judgments}", file=sys.stderr)
        return 1
    res = apply(root, private, judgments, now)
    counts: dict[str, int] = {}
    for a in res["lots"].values():
        counts[a["label"]] = counts.get(a["label"], 0) + 1
    print(f"{len(res['lots'])} lots with advice: {counts} -> {private / ADVICE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
