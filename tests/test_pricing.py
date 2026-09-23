from datetime import datetime, timedelta, timezone

import pytest
from conftest import FakeHttp, url_has
from fixtures import mp_listing
from scanner import marktplaats
from scanner.evaluate import Fees, Settings, evaluate, total_cost
from scanner.models import Lot, WatchItem
from scanner.http import BlockedError
from scanner.pricing import PriceFinder, candidate_queries

NOW = datetime(2026, 9, 22, 19, 30, tzinfo=timezone.utc)

PS5_LISTINGS = [
    mp_listing("Sony PlayStation 5 Slim 1TB - Zo goed als nieuw", 0, "FAST_BID"),
    mp_listing("Playstation 5 slim digital edition 2 controllers", 56999, "MIN_BID"),
    mp_listing("Playstation 5 slim digital edition", 52999, "MIN_BID"),
    mp_listing("PlayStation 5 slim digital | 6 maanden garantie", 53999),
    mp_listing("Zeernette Playstation 5 Slim Compleet lees goed", 48000),
    mp_listing("Sony PlayStation 5 Slim - Zo goed als nieuw", 45000, "MIN_BID"),
    mp_listing("PlayStation 5, Disc edition, Slim met 1 controller", 50000, "MIN_BID"),
    mp_listing("PlayStation 5 Slim (met kuren)", 30000, "MIN_BID"),
    mp_listing("Witte Cover Plates - PlayStation 5 slim disk editie", 2500),  # accessory -> excluded word
    mp_listing("Sony - Playstation 2 (PS2) slim roze", 3500),  # wrong console -> no match
    mp_listing("Gezocht: playstation 5 slim", 30000),
    mp_listing("PlayStation 5 slim defect", 9000),
    mp_listing("PlayStation 5 slim NIEUW gouden editie", 999900),  # outlier
]


def test_comparable_prices_filters_noise():
    prices = marktplaats.comparable_prices(PS5_LISTINGS, "playstation 5 slim", [])
    assert sorted(prices) == [300.0, 450.0, 480.0, 500.0, 529.99, 539.99, 569.99, 9999.0]
    est = marktplaats.summarize(prices, "playstation 5 slim")
    assert est.count == 6  # 9999 and the cheap "met kuren" one are outliers
    assert 480 <= est.median <= 530
    assert est.url == "https://www.marktplaats.nl/q/playstation+5+slim/"


def test_estimate_keeps_the_price_spread():
    listings = [dict(mp_listing(f"PlayStation 5 slim nr {i}", c), vipUrl=f"/v/games/m{i}-ps5")
                for i, c in enumerate([45000, 48000, 50000, 52999, 53999, 56999, 999900, 9000])]
    found = marktplaats.comparable_listings(listings, "playstation 5 slim", [])
    est = marktplaats.summarize(found, "playstation 5 slim")
    assert est.prices == [450.0, 480.0, 500.0, 529.99, 539.99, 569.99]
    assert est.outliers == [90.0, 9999.0]
    assert [l["price"] for l in est.listings] == est.prices  # cheapest first, outliers left out
    assert est.listings[0]["url"] == "https://www.marktplaats.nl/v/games/m0-ps5"
    assert est.listings[0]["kind"] == "fixed" and est.listings[0]["title"] == "PlayStation 5 slim nr 0"
    # the cache round-trips the new fields, and old cache entries without them still load
    from dataclasses import asdict
    assert marktplaats.PriceEstimate(**asdict(est)) == est
    old = {"median": 500, "low": 480, "high": 530, "count": 6, "query": "x"}
    assert marktplaats.PriceEstimate(**old).prices == []


def test_summarize_needs_enough_listings():
    assert marktplaats.summarize([100, 120, 130], "x") is None


def test_candidate_queries_from_lot_title():
    def q(keywords, title):
        return candidate_queries(WatchItem(name="x", keywords=keywords), Lot("s", "1", title, "u", 1, None))

    assert q(["thinkpad"], 'Lenovo - ThinkPad T580 - I5 / 8GB / 256GB / 15,3" Laptop') == [
        "thinkpad t580 i5 8gb", "thinkpad t580 i5", "thinkpad t580", "thinkpad"]
    assert q(["iphone"], "Apple iPhone 13 Pro 256 GB zwart") == [
        "iphone 13 pro apple", "iphone 13 pro", "iphone 13", "iphone"]
    assert q(["playstation 5", "ps5"], "Sony PlayStation 5 Slim 1TB") == [
        "playstation 5 slim 1tb sony", "playstation 5 slim 1tb", "playstation 5 slim", "playstation 5"]
    assert q(["dyson"], "2 x Dyson V15 Detect stofzuiger 60 cm") == [
        "dyson v15 detect stofzuiger", "dyson v15 detect", "dyson v15", "dyson"]
    assert q(["festool"], "Festool TS 55 invalzaag") == ["festool ts 55 invalzaag", "festool ts 55", "festool ts",
                                                         "festool"]
    custom = WatchItem(name="PS5", keywords=["ps5"], marktplaats_query="playstation 5 console")
    assert candidate_queries(custom, Lot("s", "1", "PS5", "u", 1, None)) == ["playstation 5 console"]


def test_price_finder_falls_back_and_caches():
    calls = []

    def search(m, u, kw):
        calls.append(u)
        return {"listings": PS5_LISTINGS}

    http = FakeHttp([(url_has("marktplaats.nl/lrp/api/search"), search)])
    cache = {}
    finder = PriceFinder(http, cache, NOW)
    item = WatchItem(name="PS5", keywords=["playstation 5"])
    lot = Lot("x", "1", "Sony Playstation 5 slim console", "u", 100, None)
    # "playstation 5 slim console sony" finds too few comparable listings, so the same search results
    # are re-used with fewer words: one request is enough
    est = finder.for_lot(item, lot)
    assert est.query == "playstation 5 slim" and est.as_of == NOW.isoformat()
    assert len(calls) == 1
    assert finder.for_lot(item, lot).median == est.median
    assert len(calls) == 1  # the price is saved for 3 days
    PriceFinder(http, cache, NOW + timedelta(days=2)).for_lot(item, lot)
    assert len(calls) == 1
    PriceFinder(http, cache, NOW + timedelta(days=4)).for_lot(item, lot)  # older than 3 days: check again
    assert len(calls) == 2


def test_second_search_only_when_the_first_finds_too_little():
    calls = []

    def search(m, u, kw):
        calls.append(u)
        return {"listings": [] if "detect" in u else [mp_listing(f"Dyson V15 nr {i}", c) for i, c in
                                                     enumerate([40000, 42000, 39000, 45000, 41000])]}

    finder = PriceFinder(FakeHttp([(url_has("marktplaats.nl/lrp/api/search"), search)]), {}, NOW)
    est = finder.for_lot(WatchItem(name="Dyson", keywords=["dyson"]), Lot("s", "1", "Dyson V15 Detect Absolute", "u", 1, None))
    assert [("detect" in u) for u in calls] == [True, False]  # specific search, then "dyson v15"
    assert est.query == "dyson v15" and est.count == 5


def test_block_stops_all_lookups_and_uses_saved_prices():
    calls = []

    def blocked(m, u, kw):
        calls.append(u)
        raise BlockedError("www.marktplaats.nl is blocking automated requests (HTTP 403)")

    item = WatchItem(name="PS5", keywords=["playstation 5"])
    old_lot = Lot("x", "1", "PlayStation 5 slim", "u", 100, None)
    saved = marktplaats.PriceEstimate(median=500, low=450, high=540, count=7, query="playstation 5 slim",
                                      as_of=(NOW - timedelta(days=5)).isoformat())
    from dataclasses import asdict
    cache = {marktplaats.cache_key("playstation 5 slim", []): {"at": (NOW - timedelta(days=5)).isoformat(),
                                                               "est": asdict(saved)}}
    finder = PriceFinder(FakeHttp([(url_has("marktplaats"), blocked)]), cache, NOW)
    new_lot = Lot("x", "2", "PlayStation 5 digital edition", "u", 100, None)
    assert finder.for_lot(item, new_lot) is None  # nothing saved for this one
    assert finder.blocked and len(calls) == 1
    assert finder.for_lot(item, old_lot).median == 500  # 5-day-old saved price instead of a new search
    assert len(calls) == 1 and finder.stale_used == 1
    # saved prices older than 14 days are not used
    finder2 = PriceFinder(FakeHttp([(url_has("marktplaats"), blocked)]), cache, NOW + timedelta(days=10))
    assert finder2.for_lot(item, old_lot) is None


def test_price_finder_survives_errors():
    def boom(m, u, kw):
        raise RuntimeError("HTTP 403")

    finder = PriceFinder(FakeHttp([(url_has("marktplaats"), boom)]), {}, NOW)
    assert finder.for_lot(WatchItem(name="x", keywords=["x"]), Lot("s", "1", "x y", "u", 1, None)) is None
    assert finder.errors >= 1


def test_costs_and_verdicts():
    fees = Fees(premium=0.22, vat=0.21)
    lot = Lot("pjb", "1", "PlayStation 5 slim", "u", 200.0, None)
    assert total_cost(200, fees, lot) == pytest.approx(200 * 1.22 * 1.21)

    est = marktplaats.PriceEstimate(median=500, low=450, high=540, count=7, query="playstation 5 slim")
    settings = Settings(target_return=0.30, min_profit=50, resale_factor=0.85)
    ps5 = WatchItem(name="PS5", keywords=["ps5"])
    v = evaluate(ps5, lot, fees, settings, est)
    # resale 425; 30% return caps the total at 425/1.3 = 326.92 (tighter than 425-50) -> bid 221.47
    assert v.is_deal and v.max_bid == 221
    assert v.profit == pytest.approx(425 - 295.24, abs=0.01)
    assert v.margin == pytest.approx((425 - 295.24) / 295.24, abs=0.001)
    # paying the max bid really gives the target return
    pay = total_cost(v.max_bid, fees, lot)
    assert (425 - pay) / pay >= 0.30
    # a higher target return lowers the max bid
    assert evaluate(ps5, lot, fees, Settings(target_return=0.6, min_profit=50), est).max_bid < 221
    # min profit wins for cheap items: resale 42.5 - 25 profit leaves at most 17.50 total
    cheap = marktplaats.PriceEstimate(median=50, low=45, high=55, count=5, query="x")
    small = Lot("pjb", "3", "PS5 game", "u", 5.0, None)
    assert evaluate(ps5, small, fees, Settings(target_return=0.3, min_profit=25), cheap).max_bid == 11
    # current bid above the max bid -> not a deal
    expensive = Lot("pjb", "2", "PlayStation 5 slim", "u", 260.0, None)
    assert not evaluate(ps5, expensive, fees, settings, est).is_deal
    # max_price rule works without Marktplaats data
    capped = WatchItem(name="PS5", keywords=["ps5"], max_price=300)
    v2 = evaluate(capped, lot, fees, settings, None)
    assert v2.is_deal and v2.max_bid == 203
    # no price info at all -> no max bid
    v3 = evaluate(WatchItem(name="x", keywords=["x"]), lot, fees, settings, None)
    assert not v3.is_deal and v3.max_bid is None
    # manual market price
    manual = WatchItem(name="x", keywords=["x"], market_price=1000)
    assert evaluate(manual, lot, fees, settings, None).is_deal
    # older watchlists used min_margin
    assert Settings.from_dict({"min_margin": 0.4}).target_return == 0.4
