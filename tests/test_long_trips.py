"""A long drive only for several lots (driving.long_trip_minutes / long_trip_min_lots)."""
from datetime import datetime, timezone

from scanner.evaluate import Settings, Verdict
from scanner.geo import Trip
from scanner.models import Lot, WatchItem
from scanner.scan import digest_message, lots_needed, too_far, trip_rules

NOW = datetime(2026, 10, 4, 5, 0, tzinfo=timezone.utc)
ITEM = WatchItem(name="Monitor", keywords=["monitor"])
TRIPS = {"Zele, België": Trip(km=95, minutes=70, cost=25), "Veghel": Trip(km=12, minutes=15, cost=3),
         "Gent": Trip(km=60, minutes=45, cost=15), "Breda": Trip(km=120, minutes=105, cost=30),
         "Groningen": Trip(km=224, minutes=177, cost=58)}
RULES = [(30, 1), (90, 3), (120, 6)]


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
    far = too_far(rows, TRIPS, set(), RULES)
    assert set(far) == {"s:a1", "s:a2"} and far["s:a1"] == (70, 2, 3)
    # a favorite counts and is never left out itself
    rows.append(row("a3", "Zele, België", deal=False))
    assert too_far(rows, TRIPS, {"s:a3"}, RULES) == {}
    # another pickup day at the same address is another trip
    rows2 = [row("a1", "Zele, België"), row("a2", "Zele, België"), row("a3", "Zele, België", when="Tue 20 Oct, 10:00–12:00")]
    assert set(too_far(rows2, TRIPS, set(), RULES)) == {"s:a1", "s:a2", "s:a3"}
    # off
    assert too_far(rows2, TRIPS, set(), []) == {}


def test_digest_leaves_far_lots_out():
    rows = [row("a1", "Zele, België"), row("b1", "Veghel")]
    far = too_far(rows, TRIPS, set(), RULES)
    text = digest_message(rows, {"s:a1", "s:b1"}, Settings(), NOW, None, trips=TRIPS, far=far, far_rules=RULES)
    assert "Dell monitor b1" in text and "Dell monitor a1" not in text
    assert "<b>1 with room to bid</b>" in text
    assert "Lots with room to bid left out: 1 where too few lots are worth that drive" in text


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
    assert too_far(rows, TRIPS, set(), RULES).keys() == {"s:e"}


def test_how_far_for_how_many_lots():
    assert [lots_needed(m, RULES) for m in (15, 30, 45, 90, 105, 120, 121, 177)] == [1, 1, 3, 3, 6, 6, None, None]
    assert trip_rules({"trip_rules": [[90, 3], [30, 1], [120, 6]]}) == RULES
    assert trip_rules({"long_trip_minutes": 30, "long_trip_min_lots": 3, "max_minutes": 90}) == [(30, 1), (90, 3)]
    breda5 = [row(f"br{i}", "Breda") for i in range(5)]  # 1h 45m: more than 5 lots needed
    assert len(too_far(breda5, TRIPS, set(), RULES)) == 5
    breda6 = breda5 + [row("br5", "Breda")]
    assert too_far(breda6, TRIPS, set(), RULES) == {}
    groningen = [row(f"gr{i}", "Groningen") for i in range(10)]  # 2h 57m: never, however many lots
    far = too_far(groningen, TRIPS, {"s:gr0"}, RULES)
    assert len(far) == 9 and far["s:gr1"] == (177, 10, None) and "s:gr0" not in far  # the favorite stays
    text = digest_message(groningen, set(), Settings(), NOW, None, trips=TRIPS, far=far, far_rules=RULES)
    assert "9 more than 2h 00m drive away" in text


def test_transport_beyond_the_last_rule():
    from scanner.evaluate import Fees, evaluate
    from scanner.marktplaats import PriceEstimate
    from scanner.scan import share_trips, transport_cost, transport_settings

    transport = transport_settings({"transport": {"first_lot": 75, "extra_lot": 25}})
    assert transport == {"first": 75.0, "extra": 25.0} and transport_cost(transport, 3) == 125
    assert transport_settings({"transport": {"enabled": False}}) is None and transport_settings({}) is None
    # 2h 57m away: not left out when a transporter can bring it
    groningen = [row(f"gr{i}", "Groningen") for i in range(2)]
    assert too_far(groningen, TRIPS, set(), RULES, transport=True) == {}
    assert len(too_far(groningen, TRIPS, set(), RULES)) == 2

    est = PriceEstimate(median=235, low=200, high=260, count=4, query="makita djr186")
    settings, fees = Settings(min_margin=0.30, resale_factor=0.85), Fees(premium=0.17, vat=0.21)
    lots = [Lot("onlineveilingmeester", k, f"Makita DJR186 {k}", "u", 18.0, None, bids=5, next_bid=20.0,
                pickup="Groningen", pickup_when="Thu 15 Oct, 09:00–15:00", transport=True) for k in "abc"]
    rows = [(ITEM, x, evaluate(ITEM, x, fees, settings, est), est) for x in lots]

    def cost(place, n):
        return transport_cost(transport, n)

    rows = share_trips(rows, TRIPS, set(), lambda i, x, e: evaluate(i, x, fees, settings, e), cost)
    assert [r[1].trip_cost for r in rows] == [41.67, 41.67, 41.67] and rows[0][1].trip_lots == 3  # (75 + 2 x 25) / 3
    text = digest_message(rows, {"onlineveilingmeester:a"}, Settings(), NOW, None, trips=TRIPS)
    assert "🚚 transport ≈ " in text and "🚗" not in text
