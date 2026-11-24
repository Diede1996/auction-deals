"""Brand survey (workflow "Brand survey"): which clothing brands sell well second-hand?

For each search it pages through Marktplaats (private sellers only) and reports how many listings there are,
the median asking price and how long they have been online (Diede, 24 Nov 2026: "many listings from September
means it doesn't sell quickly"). It also tries Vinted's catalogue, where the upload time of the first photo
gives the age of a listing. Asking prices, not sale prices.
"""
from __future__ import annotations

import argparse
import html
import re
import statistics
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

from .http import Http
from .marktplaats import API, PRICED_TYPES, is_business

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"])}
WANTED = re.compile(r"\b(gezocht|gevraagd|zoek|ruil)\b", re.I)


def listing_day(raw: str, today: date) -> date | None:
    """Marktplaats' "date": "Vandaag", "Gisteren", "Eergisteren", "12 nov 25", "12 nov '25" or an ISO time."""
    s = (raw or "").strip().lower().replace("'", "").replace(".", "")
    if not s:
        return None
    if s.startswith("vandaag"):
        return today
    if s.startswith("eergisteren"):
        return today - timedelta(days=2)
    if s.startswith("gisteren"):
        return today - timedelta(days=1)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.match(r"(\d{1,2}) ([a-z]{3})[a-z]* ?(\d{2,4})?", s)
    if m and m[2] in MONTHS:
        year = int(m[3]) if m[3] else today.year
        year = year + 2000 if year < 100 else year
        d = date(year, MONTHS[m[2]], int(m[1]))
        return d if d <= today else d.replace(year=d.year - 1)
    return None


def summary(label: str, rows: list[tuple[float, int | None]]) -> str:
    """rows: (price, age in days or None)."""
    prices = [p for p, _ in rows]
    ages = sorted(a for _, a in rows if a is not None)
    if not rows:
        return f"{label}: 0 listings"
    def share(lo, hi=10**6):
        return round(100 * sum(lo <= a < hi for a in ages) / len(ages)) if ages else 0
    q = statistics.quantiles(prices, n=4) if len(prices) >= 4 else [min(prices), statistics.median(prices), max(prices)]
    age_txt = (f"age median {statistics.median(ages):.0f}d | <=7d {share(0, 8)}% | 8-30d {share(8, 31)}% | "
               f"31-90d {share(31, 91)}% | >90d {share(91)}% | oldest {ages[-1]}d") if ages else "age ?"
    return (f"{label}: n={len(rows)} | price median EUR {statistics.median(prices):.0f} "
            f"(P25 {q[0]:.0f}, P75 {q[-1]:.0f}) | {age_txt}")


def marktplaats(http: Http, query: str, today: date, pages: int, min_price: float) -> tuple[str, list]:
    """Newest first. Time online (Little's law): stock of private listings / new private listings per day."""
    rows, total, seen, n_all, n_private, last_page_full = [], None, set(), 0, 0, False
    for page in range(pages):
        params = {"query": query, "limit": 100, "offset": page * 100, "searchInTitleAndDescription": "false",
                  "viewOptions": "list-view", "sortBy": "SORT_INDEX", "sortOrder": "DECREASING"}
        data = http.json(f"{API}?{urlencode(params)}")
        total = data.get("totalResultCount", total)
        found = data.get("listings") or []
        for li in found:
            key = li.get("itemId")
            info = li.get("priceInfo") or {}
            title = html.unescape(li.get("title") or "")
            if key in seen or WANTED.search(title):
                continue
            seen.add(key)
            n_all += 1
            if is_business(li):
                continue
            n_private += 1
            if info.get("priceType") not in PRICED_TYPES or (info.get("priceCents") or 0) / 100 < min_price:
                continue
            day = listing_day(str(li.get("date") or ""), today)
            rows.append((info["priceCents"] / 100, (today - day).days if day else None))
        last_page_full = len(found) >= 100
        if not last_page_full:
            break
    ages = [a for _, a in rows if a is not None]
    complete = not last_page_full
    head = summary(f"MP {query}", rows) + f" | total on MP (incl. shops) {total} | {'ALL' if complete else 'newest'} {n_all} read"
    if ages and n_all:
        window = 30 if complete else max(1, max(ages))  # days the newest listings cover
        new_per_day = sum(a <= window for a in ages) / window
        stock = len(rows) if complete else (total or n_all) * len(rows) / n_all
        if new_per_day:
            head += f" | new/day {new_per_day:.1f} | stock ~{stock:.0f} | est. days online {stock / new_per_day:.0f}"
    return head, rows


def vinted(http: Http, query: str, now: datetime, min_price: float, pages: int, debug: bool) -> str:
    """Vinted catalogue (shows only unsold items). Age = upload time of the first photo."""
    items = []
    try:
        if not http.session.cookies.get("access_token_web"):
            r = http.get("https://www.vinted.nl/")
            if debug:
                print("VINTED home", r.status_code, sorted(http.session.cookies.keys()), flush=True)
                probe_vinted(http, query)
        for page in range(1, pages + 1):
            data = http.json("https://www.vinted.nl/api/v2/catalog/items?" + urlencode(
                {"search_text": query, "per_page": 96, "page": page, "order": "relevance"}),
                headers={"Referer": "https://www.vinted.nl/catalog"})
            items += data.get("items") or []
            if len(data.get("items") or []) < 96:
                break
    except Exception as e:  # blocked or changed: say so, don't stop the Marktplaats part
        if not items:
            return f"VINTED {query}: failed ({type(e).__name__}: {str(e)[:120]})"
    rows, favs = [], []
    for it in items:
        price = it.get("price")
        amount = float(price.get("amount") if isinstance(price, dict) else price or 0)
        if amount < min_price:
            continue
        ts = ((it.get("photo") or {}).get("high_resolution") or {}).get("timestamp")
        age = (now - datetime.fromtimestamp(ts, timezone.utc)).days if ts else None
        rows.append((amount, age))
        favs.append(it.get("favourite_count") or 0)
    fav = f" | favourites median {statistics.median(favs):.0f}" if favs else ""
    return summary(f"VINTED {query}", rows) + f" | {len(items)} read{fav}"


def probe_vinted(http: Http, query: str) -> None:
    """Vinted moved its search API: try known paths and list the API paths the search page mentions."""
    q = urlencode({"search_text": query, "per_page": 20, "page": 1})
    for path, hdr in (("/api/v2/svc-catalogue/items", {"X-Next-App": "marketplace-web", "X-Platform": "web"}),
                      ("/web/api/core/svc-catalogue/items", {"X-Next-App": "marketplace-web", "X-Platform": "web"}),
                      ("/api/v2/svc-catalogue/items", {"x-next-app": "marketplace-web", "platform": "web"})):
        try:
            r = http.session.get("https://www.vinted.nl" + path + "?" + q, headers={"Accept": "application/json", **hdr},
                                 timeout=30)
            print("VINTED probe", path, r.status_code, r.text[:150].replace("\n", " "), flush=True)
        except Exception as e:
            print("VINTED probe", path, type(e).__name__, flush=True)
    try:
        page = http.session.get("https://www.vinted.nl/catalog?" + urlencode({"search_text": query}), timeout=30).text
        paths = sorted(set(re.findall(r"(?:web\\?/api\\?/core|api\\?/v2)[A-Za-z0-9_\\/{}.-]+", page)))
        print("VINTED page", len(page), "chars | api paths", paths[:60], flush=True)
        hits = [page[max(0, m.start() - 100):m.end() + 200].replace("\n", " ")
                for m in re.finditer(r"(?i)gateway|apiBase|api_base|baseUrl", page)]
        print("VINTED page config", len(hits), hits[:8], flush=True)
    except Exception as e:
        print("VINTED page", type(e).__name__, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", required=True, help="searches separated by ;")
    ap.add_argument("--pages", type=int, default=4, help="Marktplaats pages of 100 per search")
    ap.add_argument("--min-price", type=float, default=15)
    ap.add_argument("--vinted-pages", type=int, default=3, help="Vinted pages of 96; 0 = skip Vinted")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    mp, vt = Http(delay=2.0, jitter=1.0), Http(delay=3.0, jitter=1.5)
    queries = [x.strip() for x in args.queries.split(";") if x.strip()]
    for q in queries:
        try:
            print(marktplaats(mp, q, now.date(), args.pages, args.min_price)[0], flush=True)
        except Exception as e:
            print(f"MP {q}: failed ({type(e).__name__}: {str(e)[:120]})", flush=True)
        if args.vinted_pages:
            print(vinted(vt, q, now, args.min_price, args.vinted_pages, debug=(q == queries[0])), flush=True)


if __name__ == "__main__":
    main()
