"""The daily scan: scrape the auction sites, price the matches, build the dashboard, send a digest."""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from . import dashboard
from .evaluate import Fees, Settings, Verdict, evaluate, market_value
from .http import Http
from .marktplaats import PriceEstimate
from .matching import bankruptcy_matcher, match_lots, search_terms
from .models import Lot, WatchItem
from .pricing import PriceFinder, candidate_queries
from .sites import SITE_NAMES, SITES
from .sites.base import SiteContext
from .storage import dashboard_url, load_json, load_yaml, save_json
from .telegram import Telegram, attr, esc
from .util import AMS, fmt_eur

log = logging.getLogger("scanner")

Row = tuple[WatchItem, Lot, Verdict, "PriceEstimate | None"]


# ---------------------------------------------------------------- scraping

def scan_sites(config: dict, items: list[WatchItem], state: dict, http_factory, now: datetime,
               only: list[str] | None = None) -> tuple[list[Lot], dict, int]:
    """Scrape all enabled sites in parallel (one HTTP client per site). Returns (lots, report, requests)."""
    is_bankruptcy = bankruptcy_matcher(config.get("bankruptcy_keywords") or ["faillissement", "failliet", "curator"])
    terms = search_terms(items)
    health = state.setdefault("health", {})
    site_cache = state.setdefault("site_cache", {})
    jobs = {}
    for site, fetch in SITES.items():
        settings = (config.get("sites") or {}).get(site) or {}
        if settings.get("enabled", True) is False or (only and site not in only):
            continue
        ctx = SiteContext(http=http_factory(), now=now, is_bankruptcy=is_bankruptcy, search_terms=terms,
                          settings=settings, cache=site_cache.setdefault(site, {}))
        jobs[site] = (fetch, ctx)

    lots: list[Lot] = []
    report = {}
    with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
        futures = {site: pool.submit(fetch, ctx) for site, (fetch, ctx) in jobs.items()}
    for site, fut in futures.items():
        h = health.setdefault(site, {})
        err = fut.exception()
        if err is not None:
            log.error("%s failed: %s: %s", site, type(err).__name__, err)
            h.update(ok=False, error=f"{type(err).__name__}: {err}"[:300], fails=h.get("fails", 0) + 1,
                     at=now.isoformat())
            report[site] = {"ok": False, "error": h["error"]}
            continue
        found = fut.result()
        h.update(ok=True, lots=len(found), fails=0, error="", at=now.isoformat(), last_ok=now.isoformat())
        report[site] = {"ok": True, "lots": len(found)}
        log.info("%s: %d lots", site, len(found))
        lots.extend(found)
    requests = sum(getattr(ctx.http, "request_count", 0) for _, ctx in jobs.values())
    return lots, report, requests


# ---------------------------------------------------------------- telegram

def health_messages(state: dict, warn_after: int = 2) -> list[str]:
    out = []
    for site, h in (state.get("health") or {}).items():
        name = SITE_NAMES.get(site, site)
        if not h.get("ok") and h.get("fails", 0) >= warn_after and not h.get("warned"):
            h["warned"] = True
            out.append(f"⚠️ <b>{name}</b> has failed {h['fails']} scans in a row, so lots there are missing.\n"
                       f"<i>{esc(h.get('error', '')[:200])}</i>\n"
                       "The site may have changed or be blocking automated requests.")
        elif h.get("ok") and h.get("warned"):
            h["warned"] = False
            out.append(f"✅ <b>{name}</b> works again.")
    return out


def _line(item: WatchItem, lot: Lot, v: Verdict) -> str:
    local = lot.closes_at.astimezone(AMS) if lot.closes_at else None
    when = local.strftime("%a %H:%M") if local else "?"
    return (f'• <a href="{attr(lot.url)}">{esc(lot.title[:70])}</a>\n'
            f"   bid {fmt_eur(v.bid)} → max <b>{fmt_eur(v.max_bid)}</b> · {SITE_NAMES.get(lot.site, lot.site)} · {when}")


def digest_message(rows: list[Row], new_keys: set[str], settings: Settings, now: datetime, url: str | None,
                   per_section: int = 8, notes: list[str] | None = None) -> str:
    deals = [r for r in rows if r[2].is_deal]
    day = now.astimezone(AMS).strftime("%a %d %b")
    head = [f"☀️ <b>Auction scan</b> · {day}",
            f"{len(rows)} matching lots · <b>{len(deals)} with room to bid</b> · "
            f"{sum(1 for r in rows if r[1].key in new_keys)} new",
            f"<i>Max bids for a {settings.target_return:.0%} return and at least {fmt_eur(settings.min_profit)} profit</i>"]
    parts = ["\n".join(head)] + list(notes or [])
    soon = [r for r in deals if r[1].closes_at and r[1].closes_at - now <= timedelta(hours=24)]
    fresh = [r for r in deals if r[1].key in new_keys and r not in soon]
    if soon:
        parts.append("⏰ <b>Closing within 24 hours</b>\n" + "\n".join(_line(i, l, v) for i, l, v, _ in soon[:per_section]))
    if fresh:
        fresh.sort(key=lambda r: -(r[2].max_bid or 0) + (r[2].bid or 0))
        parts.append("🆕 <b>New with room to bid</b>\n" + "\n".join(_line(i, l, v) for i, l, v, _ in fresh[:per_section]))
    if not rows:
        parts.append("Nothing on your watchlist is in a running bankruptcy auction today.")
    elif not soon and not fresh:
        parts.append("Nothing new or closing soon with room to bid.")
    if url:
        parts.append(f'📊 <a href="{attr(url)}">Open the dashboard</a>')
    return "\n\n".join(parts)


# ---------------------------------------------------------------- dashboard data

def dashboard_data(rows: list[Row], report: dict, config: dict, settings: Settings, site_fees: dict[str, Fees],
                   new_keys: set[str], seen: dict, now: datetime) -> dict:
    lots = []
    for item, lot, v, est in rows:
        fees = site_fees.get(lot.site, Fees())
        queries = candidate_queries(item, lot)
        market = market_value(item, est)
        lots.append({
            "key": lot.key, "item": item.name, "title": lot.title, "url": lot.url,
            "site": lot.site, "siteName": SITE_NAMES.get(lot.site, lot.site), "auction": lot.auction_title,
            "image": lot.image, "location": lot.location,
            "closes": lot.closes_at.isoformat() if lot.closes_at else None,
            "bid": v.bid, "bids": lot.bids,
            "premium": lot.premium if lot.premium is not None else fees.premium, "vat": fees.vat,
            "fixed": round(fees.fixed + lot.extra_fee, 2),
            "market": market, "marketSource": ("manual" if item.market_price is not None else
                                               "marktplaats" if est else None),
            "mp": ({"median": est.median, "low": est.low, "high": est.high, "count": est.count,
                    "query": est.query, "url": est.url, "prices": est.prices, "outliers": est.outliers,
                    "listings": est.listings, "asOf": est.as_of} if est else None),
            "mpSearch": PriceEstimate(0, 0, 0, 0, queries[-1] if queries else item.name).url,
            "itemMaxPrice": item.max_price, "itemMinProfit": item.min_profit,
            "firstSeen": (seen.get(lot.key) or {}).get("first"), "isNew": lot.key in new_keys,
            "maxBid": v.max_bid, "isDeal": v.is_deal,
        })
    sites = [{"id": s, "name": SITE_NAMES.get(s, s), "ok": r.get("ok", False), "lots": r.get("lots", 0),
              "error": r.get("error", "")} for s, r in report.items()]
    fees = [{"id": s, "name": SITE_NAMES.get(s, s), "premium": f.premium, "vat": f.vat}
            for s, f in site_fees.items() if (config.get("sites") or {}).get(s, {}).get("enabled", True)]
    return {
        "generated": now.isoformat(),
        "settings": {"target_return": settings.target_return, "min_profit": settings.min_profit,
                     "resale_factor": settings.resale_factor, "selling_costs": settings.selling_costs},
        "sites": sites, "fees": sorted(fees, key=lambda f: f["premium"]), "lots": lots,
    }


def write_report(path: Path, rows: list[Row], report: dict, now: datetime, url: str | None) -> str:
    local = now.astimezone(AMS).strftime("%A %d %B %Y, %H:%M")
    lines = ["# Latest scan\n", f"_{local} (Amsterdam time)_\n"]
    if url:
        lines.append(f"**Dashboard:** {url}\n")
    lines += ["| Site | Status |", "|---|---|"]
    for site, r in report.items():
        unit = "price lookups" if site == "marktplaats" else "lots"
        status = f"✅ {r['lots']} {unit}" if r.get("ok") else f"⚠️ {r.get('error', '')[:120]}"
        lines.append(f"| {SITE_NAMES.get(site, site)} | {status} |")
    lines += ["", f"## Matching lots ({len(rows)})\n"]
    if rows:
        lines += ["| | Item | Lot | Bid | Market | Max bid | Closes |", "|---|---|---|---|---|---|---|"]
        for item, lot, v, est in rows:
            title = lot.title.replace("|", "/")[:70]
            closes = lot.closes_at.astimezone(AMS).strftime("%a %d %b %H:%M") if lot.closes_at else "?"
            market = fmt_eur(est.median) if est else (fmt_eur(item.market_price) if item.market_price else "–")
            lines.append(f"| {'✅' if v.is_deal else ''} | {item.name} | [{title}]({lot.url}) ({SITE_NAMES.get(lot.site)}) | "
                         f"{fmt_eur(v.bid)} | {market} | {fmt_eur(v.max_bid) if v.max_bid is not None else '–'} | {closes} |")
    else:
        lines.append("Nothing on your watchlist is in a running bankruptcy auction right now.")
    text = "\n".join(lines) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


# ---------------------------------------------------------------- run

def run_scan(root: Path, now: datetime, dry_run: bool = False, only: list[str] | None = None,
             http_cls=Http, telegram_cls=Telegram) -> int:
    config = load_yaml(root / "config.yml")
    watchlist = load_yaml(root / "watchlist.yml")
    state_path = root / "data" / "state.json"
    state = load_json(state_path)
    delay = float((config.get("http") or {}).get("delay_seconds", 1.0))
    url = dashboard_url(config)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    tg = telegram_cls(http_cls(delay=0.5), token, chat_id, dry_run=dry_run or not (token and chat_id))

    items = [i for i in (WatchItem.from_dict(d) for d in watchlist.get("items") or []) if i.keywords]
    settings = Settings.from_dict(watchlist.get("settings"))

    # 1. scrape (skipped when the watchlist is empty)
    lots, report, site_requests = ([], {}, 0)
    if items:
        lots, report, site_requests = scan_sites(config, items, state, lambda: http_cls(delay=delay), now, only)
    alerts_cfg = config.get("digest") or config.get("alerts") or {}
    horizon = now + timedelta(days=float(alerts_cfg.get("ignore_closing_after_days", 30)))
    lots = [lot for lot in lots if lot.closes_at is None or now < lot.closes_at <= horizon]
    matched = match_lots(items, lots)
    log.info("%d lots scanned, %d match the watchlist", len(lots), len(matched))

    # 2. price + evaluate
    mp_cfg = config.get("marktplaats") or {}
    http = http_cls(delay=float(mp_cfg.get("delay_seconds", 4)), jitter=float(mp_cfg.get("extra_random_delay", 3)))
    cache_days = float(mp_cfg["cache_days"]) if "cache_days" in mp_cfg else float(mp_cfg.get("cache_hours", 72)) / 24
    finder = PriceFinder(http, state.setdefault("price_cache", {}), now, cache_days=cache_days,
                         stale_days=float(mp_cfg.get("fallback_days", 14)),
                         min_listings=int(mp_cfg.get("min_listings", 4)),
                         max_lookups=int(mp_cfg.get("max_lookups_per_run", 40)),
                         enabled=mp_cfg.get("enabled", True))
    site_fees = {s: Fees.from_dict(c) for s, c in (config.get("sites") or {}).items()}
    rows: list[Row] = []
    for item, lot in sorted(matched, key=lambda m: m[1].closes_at or horizon):
        est = finder.for_lot(item, lot)
        rows.append((item, lot, evaluate(item, lot, site_fees.get(lot.site, Fees()), settings, est), est))
    finder.prune()
    notes = []
    if items:
        mh = state.setdefault("health", {}).setdefault("marktplaats", {})
        failed = finder.blocked or (finder.errors and finder.errors >= finder.lookups)
        if failed:
            why = "blocked the price check" if finder.blocked else "could not be reached"
            error = f"Marktplaats {why}; showing saved prices where available"
            report["marktplaats"] = {"ok": False, "error": error, "lots": finder.lookups}
            mh.update(ok=False, error=error, fails=mh.get("fails", 0) + 1, at=now.isoformat())
            notes.append(f"⚠️ Marktplaats {why} today, so market values are from earlier scans "
                         f"(up to {int(finder.stale_days)} days old) or missing.")
        else:
            report["marktplaats"] = {"ok": True, "lots": finder.lookups}
            mh.update(ok=True, lots=finder.lookups, fails=0, error="", at=now.isoformat())

    # 3. which lots are new since the last scan
    seen = state.setdefault("seen", {})
    new_keys = {lot.key for _, lot, _, _ in rows if lot.key not in seen}
    for _, lot, _, _ in rows:
        rec = seen.setdefault(lot.key, {"first": now.isoformat()})
        rec["closes"] = lot.closes_at.isoformat() if lot.closes_at else None
    for key in list(seen):
        closes = seen[key].get("closes")
        if closes and datetime.fromisoformat(closes) < now - timedelta(days=3):
            del seen[key]

    # 4. dashboard + report
    data = dashboard_data(rows, report, config, settings, site_fees, new_keys, seen, now)
    dashboard.write(root / "site", data)
    save_json(root / "data" / "lots.json", data)
    text = write_report(root / "data" / "latest.md", rows, report, now, url)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)

    # 5. telegram digest
    if items and alerts_cfg.get("enabled", True):
        try:
            tg.send(digest_message(rows, new_keys, settings, now, url, int(alerts_cfg.get("per_section", 8)), notes))
        except Exception as e:
            log.warning("could not send the digest: %s", e)
    for msg in health_messages(state, int(alerts_cfg.get("warn_after_failed_scans", 2))):
        try:
            tg.send(msg)
        except Exception as e:
            log.warning("could not send a warning: %s", e)

    deals = sum(1 for r in rows if r[2].is_deal)
    state["last_run"] = {"at": now.isoformat(), "lots": len(lots), "matches": len(rows), "deals": deals,
                         "new": len(new_keys), "requests": site_requests + http.request_count}
    if not dry_run:  # a dry run must not change what counts as "new"
        save_json(state_path, state)
    log.info("done: %d lots, %d matches, %d with room to bid, %d new, %d requests",
             len(lots), len(rows), deals, len(new_keys), site_requests + http.request_count)
    return 0
