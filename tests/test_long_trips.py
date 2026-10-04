"""A long drive only for several lots (driving.long_trip_minutes / long_trip_min_lots)."""
from datetime import datetime, timezone

from scanner.evaluate import Settings, Verdict
from scanner.geo import Trip
from scanner.models import Lot, WatchItem
from scanner.scan import digest_message, too_far

NOW = datetime(2026, 10, 4, 5, 0, tzinfo=timezone.utc)
ITEM = WatchItem(name="Monitor", keywords=["monitor"])
TRIPS = {"Zele, België": Trip(km=95, minutes=70, cost=25), "Veghel": Trip(km=12, minutes=15, cost=3),
         "Gent": Trip(km=60, minutes=45, cost=15)}


def row(key, place, deal=True, when="Mon 19 Oct, 13:00–15:30", email=False):
    lot = Lot("s", key, f"Dell monitor {key}", "https://x/" + key, 20.0, datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc),
              pickup=place, pickup_when=when, bid_from_email=email)
    v = Verdict(is_deal=deal, reason="", bid=20, total_cost=30, resale=80, max_bid=40)
    return (ITEM, lot, v, None)


def test_far_pickups_need_three_lots():
    rows = [row("a1", "Zele, België"), row("a2", "Zele, België"),              # 70 min, 2 lots: too far
            row("b1", "Veghel"),                                               # 15 min: always fine
            row("c1", "Gent"), row("c2", "Gent"), row("c3", "Gent", deal=False),
            row("c4", "Gent", deal=False, email=True)]                         # 45 min, 2 deals + 1 to check = 3
    far = too_far(rows, TRIPS, set(), 30, 3)
    assert set(far) == {"s:a1", "s:a2"} and far["s:a1"] == (70, 2)
    # a favorite counts and is never left out itself
    rows.append(row("a3", "Zele, België", deal=False))
    assert too_far(rows, TRIPS, {"s:a3"}, 30, 3) == {}
    # another pickup day at the same address is another trip
    rows2 = [row("a1", "Zele, België"), row("a2", "Zele, België"), row("a3", "Zele, België", when="Tue 20 Oct, 10:00–12:00")]
    assert set(too_far(rows2, TRIPS, set(), 30, 3)) == {"s:a1", "s:a2", "s:a3"}
    # off
    assert too_far(rows2, TRIPS, set(), 0, 3) == {}


def test_digest_leaves_far_lots_out():
    rows = [row("a1", "Zele, België"), row("b1", "Veghel")]
    far = too_far(rows, TRIPS, set(), 30, 3)
    text = digest_message(rows, {"s:a1", "s:b1"}, Settings(), NOW, None, trips=TRIPS, far=far, far_rule=(30, 3))
    assert "Dell monitor b1" in text and "Dell monitor a1" not in text
    assert "<b>1 with room to bid</b>" in text
    assert "1 lot with room to bid left out: more than 30 min drive with fewer than 3 lots to collect there" in text


def test_lots_at_one_pickup_share_the_trip():
    from scanner.evaluate import Fees, evaluate
    from scanner.marktplaats import PriceEstimate
    from scanner.scan import share_trips

    est = PriceEstimate(median=100, low=90, high=110, count=6, query="dell u2419h")
    settings = Settings(min_margin=0.30, resale_factor=0.85)
    fees = Fees(premium=0.19, vat=0.21)

    def again(item, lot, e):
        return evaluate(item, lot, fees, settings, e)

    def lot(k, bid, place="Zele, België", when="Mon 19 Oct, 13:00–15:30"):
        lt = Lot("s", k, f"Dell U2419H {k}", "u", bid, None, pickup=place, pickup_when=when)
        lt.trip_cost = TRIPS[place].cost  # €25 for the whole trip
        return lt

    # resale 85; 2 x €25 trip alone leaves no room at a €30 bid, but three lots that share it do
    lots = [lot("a", 30), lot("b", 30), lot("c", 30), lot("d", 70), lot("e", 30, when="Tue 20 Oct, 10:00–12:00")]
    rows = [(ITEM, x, evaluate(ITEM, x, fees, settings, est), est) for x in lots]
    assert [r[2].is_deal for r in rows] == [False, False, False, False, False]
    rows = share_trips(rows, TRIPS, set(), again)
    by = {r[1].lot_id: r for r in rows}
    assert [by[k][1].trip_cost for k in "abc"] == [8.33, 8.33, 8.33] and by["a"][1].trip_lots == 3
    assert all(by[k][2].is_deal for k in "abc")
    assert by["d"][1].trip_cost == 6.25 and not by["d"][2].is_deal  # too expensive anyway: as if it were the 4th
    assert by["e"][1].trip_cost == 25.0 and not by["e"][2].is_deal  # another day, another trip
    # a far pickup with 3 lots that share the trip is no longer "too far" (the other day still is)
    assert too_far(rows, TRIPS, set(), 30, 3).keys() == {"s:e"}
