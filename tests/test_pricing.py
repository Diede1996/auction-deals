from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest
from conftest import FakeHttp, url_has
from fixtures import mp_listing
from scanner import marktplaats
from scanner.evaluate import Fees, Settings, evaluate, total_cost
from scanner.http import BlockedError
from scanner.identify import Rule, plan_for
from scanner.models import Lot, WatchItem
from scanner.pricing import PriceFinder

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
    mp_listing("Online Veiling: PlayStation 5 slim", 12500),  # an auction house advertising its own lot
    mp_listing("PlayStation 5 slim NIEUW gouden editie", 999900),  # outlier
]
PS5_SLIM = Rule([("playstation 5",), ("slim",)], label="playstation 5 slim")


def prices(found):
    return sorted(x["price"] for x in found)


def test_comparable_listings_filter_noise():
    found = marktplaats.comparable_listings(PS5_LISTINGS, PS5_SLIM, [])
    assert prices(found) == [300.0, 450.0, 480.0, 500.0, 529.99, 539.99, 569.99, 9999.0]
    est = marktplaats.summarize(found, "playstation 5 slim")
    assert est.count == 6  # 9999 and the cheap "met kuren" one are outliers
    assert 480 <= est.median <= 530
    assert est.url == "https://www.marktplaats.nl/q/playstation+5+slim/"


def test_estimate_keeps_the_price_spread():
    listings = [dict(mp_listing(f"PlayStation 5 slim nr {i}", c), vipUrl=f"/v/games/m{i}-ps5", itemId=f"m{i}")
                for i, c in enumerate([45000, 48000, 50000, 52999, 53999, 56999, 999900, 9000])]
    found = marktplaats.comparable_listings(listings + listings[:2], PS5_SLIM, [])  # duplicates from 2 searches
    assert len(found) == 8
    est = marktplaats.summarize(found, "playstation 5 slim")
    assert est.prices == [450.0, 480.0, 500.0, 529.99, 539.99, 569.99]
    assert est.outliers == [90.0, 9999.0]
    assert [l["price"] for l in est.listings] == est.prices  # cheapest first, outliers left out
    assert est.listings[0]["url"] == "https://www.marktplaats.nl/v/games/m0-ps5"
    assert est.listings[0]["kind"] == "fixed" and est.listings[0]["title"] == "PlayStation 5 slim nr 0"
    # the cache round-trips the new fields, and old cache entries without them still load
    assert marktplaats.PriceEstimate(**asdict(est)) == est
    old = {"median": 500, "low": 480, "high": 530, "count": 6, "query": "x"}
    assert marktplaats.PriceEstimate(**old).prices == [] and marktplaats.PriceEstimate(**old).kind == "general"


def test_summarize_needs_enough_listings():
    assert marktplaats.summarize([100, 120, 130], "x") is None
    assert marktplaats.summarize([100, 120], "x", min_count=2).median == 110


MONITORS = [mp_listing(t, c) for t, c in [
    ("Samsung S27C366EAU curved monitor 27 inch", 9500),
    ("Samsung 27 inch curved S27C366EAUXEN zgan", 11000),
    ("Samsung curved monitor 27 inch C27F390", 8000),  # another Samsung model
    ("Samsung Odyssey G5 27 inch", 22000),
    ("Dell P2419H 24 inch monitor", 6000),
    ("Monitor arm voor Samsung S27C366EAU", 2500),  # accessory: excluded by the watchlist
]]


def test_exact_model_only_compares_that_model():
    """The complaint: prices of all kinds of monitors instead of the one in the auction."""
    calls = []

    def search(m, u, kw):
        calls.append(u)
        return {"listings": MONITORS}

    monitor = WatchItem(name="Monitor", keywords=["monitor", "beeldscherm"], exclude=["arm", "monitorarm"])
    lot = Lot("hnvi", "1", "Curved beeldscherm 27 inch SAMSUNG S27C366EAU. Krasje in scherm", "u", 15, None)
    est = PriceFinder(FakeHttp([(url_has("marktplaats.nl/lrp/api/search"), search)]), {}, NOW).for_lot(monitor, lot)
    assert est.kind == "exact" and est.model == "S27C366EAU"
    assert est.prices == [95.0, 110.0] and est.count == 2  # two listings of the same model are enough
    assert all("S27C366EAU" in l["title"].upper() for l in est.listings)
    assert "samsung+s27c366eau" in calls[0] and len(calls) == 1
    assert est.url == "https://www.marktplaats.nl/q/samsung+s27c366eau/"


def test_exact_model_not_on_marktplaats_gives_no_price():
    finder = PriceFinder(FakeHttp([(url_has("marktplaats"), {"listings": MONITORS})]), {}, NOW)
    monitor = WatchItem(name="Monitor", keywords=["beeldscherm"])
    assert finder.for_lot(monitor, Lot("hnvi", "2", "Beeldscherm 27 inch ACER RG270", "u", 15, None)) is None
    assert finder.lookups == 2  # "acer rg270", then "rg270" alone


def test_rough_price_without_type_number():
    listings = [mp_listing(f"Lenovo {t} 22 inch", c) for t, c in
                [("monitor", 4000), ("beeldscherm", 4500), ("monitor L22e", 5000), ("monitor", 3500)]]
    finder = PriceFinder(FakeHttp([(url_has("marktplaats"), {"listings": listings})]), {}, NOW)
    est = finder.for_lot(WatchItem(name="Monitor", keywords=["monitor", "beeldscherm"]),
                         Lot("hnvi", "3", "Beeldscherm LENOVO 22 inch, voedingskabel ontbreekt", "u", 5, None))
    assert est.kind == "general" and est.count == 4  # "monitor" and "beeldscherm" both count
    assert est.query == "monitor lenovo"


def test_price_finder_caches():
    calls = []

    def search(m, u, kw):
        calls.append(u)
        return {"listings": PS5_LISTINGS}

    http = FakeHttp([(url_has("marktplaats.nl/lrp/api/search"), search)])
    cache = {}
    item = WatchItem(name="PS5", keywords=["playstation 5"])
    lot = Lot("x", "1", "Sony Playstation 5 slim console", "u", 100, None)
    est = PriceFinder(http, cache, NOW).for_lot(item, lot)
    # "playstation 5 slim console sony" finds nothing comparable, "playstation 5 slim" does, on the same results
    assert est.query == "playstation 5 slim" and est.as_of == NOW.isoformat() and est.kind == "exact"
    assert len(calls) == 1
    PriceFinder(http, cache, NOW + timedelta(days=2)).for_lot(item, lot)
    assert len(calls) == 1  # the price is saved for 3 days
    PriceFinder(http, cache, NOW + timedelta(days=4)).for_lot(item, lot)
    assert len(calls) == 2


def test_second_search_writes_the_model_differently():
    calls = []

    def search(m, u, kw):
        calls.append(u)
        if "jr3030t" in u:
            return {"listings": [mp_listing(f"Makita JR3030T reciprozaag nr {i}", c) for i, c in enumerate([7000, 8000])]}
        return {"listings": [mp_listing("Makita JR 3030T", 6500)]}

    finder = PriceFinder(FakeHttp([(url_has("marktplaats.nl/lrp/api/search"), search)]), {}, NOW)
    est = finder.for_lot(WatchItem(name="Tools", keywords=["makita"]), Lot("h", "1", "Makita JR 3030T reciprozaag", "u", 25, None))
    assert ["makita+jr+3030t" in calls[0], "makita+jr3030t" in calls[1]] == [True, True]
    assert est.count == 3 and est.model == "JR 3030T"  # both spellings count


def test_brand_word_alone_is_never_a_comparison():
    """Marktplaats search is fuzzy: "hilti pua 25" also returns other Hilti products."""
    mixed = [mp_listing(t, c) for t, c in [
        ("Hilti X-FB 20 C27 leidingbeugels", 3500), ("Hilti Doorslijpschijf AC-D", 5000),
        ("Battery Hilti SFB150 SFB155", 7100), ("Hilti PUA 36 statief", 10000),
        ("Hilti PUA 250 laser", 24000), ("Hilti DC 230-S doorslijpmachine", 45000)]]
    calls = []

    def search(m, u, kw):
        calls.append(u)
        return {"listings": mixed}

    finder = PriceFinder(FakeHttp([(url_has("marktplaats"), search)]), {}, NOW)
    est = finder.for_lot(WatchItem(name="Hilti", keywords=["hilti"]), Lot("pv", "1", "Hilti Meetstatief PUA 25", "u", 20, None))
    assert est is None  # no price is better than a wrong one
    assert len(calls) == 2 and "pua25" in calls[1]
    assert plan_for(WatchItem(name="Hilti", keywords=["hilti"]), Lot("pv", "1", "Hilti", "u", 1, None)) is None


def test_block_stops_all_lookups_and_uses_saved_prices():
    calls = []

    def blocked(m, u, kw):
        calls.append(u)
        raise BlockedError("www.marktplaats.nl is blocking automated requests (HTTP 403)")

    item = WatchItem(name="PS5", keywords=["playstation 5"])
    old_lot = Lot("x", "1", "PlayStation 5 slim", "u", 100, None)
    plan = plan_for(item, old_lot)
    saved = marktplaats.PriceEstimate(median=500, low=450, high=540, count=7, query="playstation 5 slim",
                                      as_of=(NOW - timedelta(days=5)).isoformat())
    cache = {marktplaats.cache_key(f"{plan.kind}:{plan.searches[0]}", []): {
        "at": (NOW - timedelta(days=5)).isoformat(), "est": asdict(saved)}}
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
        raise RuntimeError("HTTP 500")

    finder = PriceFinder(FakeHttp([(url_has("marktplaats"), boom)]), {}, NOW)
    assert finder.for_lot(WatchItem(name="iPad", keywords=["ipad"]), Lot("s", "1", "iPad Air 5", "u", 1, None)) is None
    assert finder.errors >= 1


def test_costs_and_verdicts():
    fees = Fees(premium=0.22, vat=0.21)
    lot = Lot("pjb", "1", "PlayStation 5 slim", "u", 200.0, None)
    assert total_cost(200, fees, lot) == pytest.approx(200 * 1.22 * 1.21)

    est = marktplaats.PriceEstimate(median=500, low=450, high=540, count=7, query="playstation 5 slim")
    settings = Settings(min_profit=50, resale_factor=0.85)
    ps5 = WatchItem(name="PS5", keywords=["ps5"])
    v = evaluate(ps5, lot, fees, settings, est)
    # sell at 85% of 500 = 425; keep at least 50 profit -> pay at most 375 -> bid 375 / 1.21 / 1.22 = 254.0
    assert v.is_deal and v.max_bid == 254
    assert v.profit == pytest.approx(425 - 295.24, abs=0.01)
    assert v.margin == pytest.approx((425 - 295.24) / 295.24, abs=0.001)
    # at the max bid the margin is (just) the minimum profit, and it's reported in euros and %
    assert v.profit_at_max == pytest.approx(425 - 254 * 1.22 * 1.21, abs=0.01) and v.profit_at_max >= 50
    assert v.margin_at_max == pytest.approx(v.profit_at_max / (254 * 1.22 * 1.21), abs=0.001)
    # selling at only 50% of the median: max bid drops below the current bid
    half = evaluate(ps5, lot, fees, Settings(min_profit=50, resale_factor=0.5), est)
    assert half.max_bid == 135 and not half.is_deal and half.profit < 0
    # a higher minimum profit lowers the max bid
    assert evaluate(ps5, lot, fees, Settings(min_profit=100), est).max_bid < 254
    # cheap items: 85% of 50 = 42.50 - 25 profit leaves at most 17.50 total
    cheap = marktplaats.PriceEstimate(median=50, low=45, high=55, count=5, query="x")
    small = Lot("pjb", "3", "PS5 game", "u", 5.0, None)
    assert evaluate(ps5, small, fees, Settings(min_profit=25), cheap).max_bid == 11
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
    # older watchlists with target_return / min_margin still load; those keys are ignored
    old = Settings.from_dict({"target_return": 0.3, "min_margin": 0.4, "resale_factor": 0.5})
    assert old.resale_factor == 0.5 and old.min_profit == 25


def test_driving_costs_and_quantity_in_the_max_bid():
    fees = Fees(premium=0.22, vat=0.21)
    est = marktplaats.PriceEstimate(median=500, low=450, high=540, count=7, query="playstation 5 slim")
    ps5 = WatchItem(name="PS5", keywords=["ps5"])
    lot = Lot("pjb", "1", "PlayStation 5 slim", "u", 200.0, None, trip_cost=30.0)
    v = evaluate(ps5, lot, fees, Settings(min_profit=50), est)
    # the €30 trip comes off what you can pay: 375 - 30 = 345 -> bid 345 / 1.21 / 1.22 = 233.7
    assert v.max_bid == 233 and v.total_cost == pytest.approx(200 * 1.22 * 1.21 + 30)
    assert evaluate(ps5, lot, fees, Settings(min_profit=50, include_trip=False), est).max_bid == 254
    # two consoles in one lot: resale 2 x 425 = 850
    two = evaluate(ps5, Lot("pjb", "2", "2 x PlayStation 5 slim", "u", 200.0, None), fees, Settings(min_profit=50), est, 2)
    assert two.resale == pytest.approx(850) and two.max_bid == 541
