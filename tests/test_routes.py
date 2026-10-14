"""One drive past several pickup addresses close to each other on the same day."""
from scanner.geo import Trip
from scanner.routes import combined_pickups
from scanner.scan import combos_message, digest_message, pickup_groups, too_far, worthwhile_combos

from test_long_trips import NOW, row
from scanner.evaluate import Settings

RULES = [(30, 1), (90, 3), (120, 6)]
TRIPS = {"Veghel": Trip(km=50, minutes=40, cost=12), "Uden": Trip(km=55, minutes=45, cost=13),
         "Breda": Trip(km=120, minutes=100, cost=30)}
COORDS = {"Veghel": (51.617, 5.549), "Uden": (51.660, 5.619), "Breda": (51.589, 4.776)}


def test_nearby_addresses_on_the_same_day_are_one_drive():
    rows = [row("v1", "Veghel"), row("v2", "Veghel"), row("u1", "Uden"), row("b1", "Breda"),
            row("u2", "Uden", deal=False)]
    combos = combined_pickups(pickup_groups(rows, TRIPS, set()), TRIPS, COORDS)
    assert len(combos) == 1
    c = combos[0]
    assert [p for p, _, _ in c.places] == ["Veghel", "Uden"] and c.lots == 3 and c.keys == {"s:v1", "s:v2", "s:u1"}
    assert 85 < c.minutes < 100 and c.separate == 170  # 40 there, ~7 between, 45 back
    # alone: 2 lots at 40 min and 1 at 45 min are too few (3 needed); together 3 lots at ~46 min one way
    assert set(too_far(rows, TRIPS, set(), RULES)) >= {"s:v1", "s:v2", "s:u1", "s:u2"}
    good = worthwhile_combos(combos, RULES)
    far = too_far(rows, TRIPS, set(), RULES, combos=good)
    assert not {"s:v1", "s:v2", "s:u1", "s:u2"} & set(far) and "s:b1" in far
    text = digest_message(rows, set(), Settings(), NOW, None, far=far, far_rules=RULES, combos=good)
    assert "One drive, several pickups" in text and "Veghel (2) → Uden (1)" in text
    assert "together worth the drive" in text and "instead of 2h 50m" in text


def test_other_day_or_too_few_lots_or_off():
    rows = [row("v1", "Veghel"), row("u1", "Uden", when="Tue 20 Oct, 10:00–12:00")]
    assert combined_pickups(pickup_groups(rows, TRIPS, set()), TRIPS, COORDS) == []
    rows = [row("v1", "Veghel"), row("u1", "Uden")]
    combos = combined_pickups(pickup_groups(rows, TRIPS, set()), TRIPS, COORDS)
    assert len(combos) == 1 and worthwhile_combos(combos, RULES) == []  # 2 lots, 3 needed
    assert combined_pickups(pickup_groups(rows, TRIPS, set()), TRIPS, COORDS, within_km=0) == []
    assert combos_message([], RULES) is None
