from datetime import datetime, timedelta, timezone

import pytest
from conftest import FakeHttp, url_has
from scanner.geo import DrivingCosts, TripPlanner, fuel_price, geocode, parse_fuel_price

NOW = datetime(2026, 9, 27, 4, 20, tzinfo=timezone.utc)
HOME = "Dorpsstraat 1, Veghel"


def pdok(point, name):
    return {"response": {"numFound": 1, "docs": [{"centroide_ll": point, "weergavenaam": name, "type": "adres"}]}}


PDOK_ANSWERS = {
    "Dorpsstraat": pdok("POINT(5.54 51.61)", "Dorpsstraat 1, 5461AA Veghel"),
    "Produktieweg": pdok("POINT(5.72 52.70)", "Produktieweg 9, 8304AV Emmeloord"),
    "Hooffstraat": pdok("POINT(5.47 51.44)", "Jan van Hooffstraat 3, 5611ED Eindhoven"),
}


def pdok_route(m, u, kw):
    for word, answer in PDOK_ANSWERS.items():
        if word in u:
            return answer
    return pdok("POINT(4.0 52.0)", "Kerkstraat 1, 1234AB Ergens")  # PDOK always finds *something*


def osrm(m, u, kw):
    n = u.split("/driving/")[1].split("?")[0].count(";")
    return {"code": "Ok", "distances": [[0] + [100000 * (i + 1) for i in range(n)]],
            "durations": [[0] + [3600 * (i + 1) for i in range(n)]]}


def test_geocode_checks_the_town_and_falls_back_to_openstreetmap():
    http = FakeHttp([(url_has("api.pdok.nl"), pdok_route),
                     (url_has("nominatim.openstreetmap.org"), [{"lat": "51.05", "lon": "3.72"}])])
    assert geocode(http, "Produktieweg 9, 8304AV Emmeloord") == (52.70, 5.72)
    # PDOK only knows the Netherlands and answered with another town: ask OpenStreetMap instead
    assert geocode(http, "Kerkstraat 1, 9000 Gent") == (51.05, 3.72)
    # an address abroad goes straight to OpenStreetMap
    http.calls.clear()
    geocode(http, "Veldstraat 1, Gent, België")
    assert [("pdok" in u, "nominatim" in u) for _, u, _ in http.calls] == [(False, True)]
    assert http.calls[0][2]["headers"]["User-Agent"].startswith("auction-deals")


def test_trip_planner_routes_all_pickups_in_one_request_and_never_stores_home():
    http = FakeHttp([(url_has("api.pdok.nl"), pdok_route), (url_has("router.project-osrm.org"), osrm)])
    cache = {}
    costs = DrivingCosts(km_per_liter=16, fuel_price=2.0)
    planner = TripPlanner(http, cache, NOW, costs, HOME)
    trips = planner.plan({"Produktieweg 9, 8304AV Emmeloord": None,
                          "Jan van Hooffstraat 3, Eindhoven": None,
                          "Zuiderweg 21, 3769AB Soesterberg": (52.117204, 5.316177)})  # coordinates from the site
    assert sum("router.project-osrm.org" in u for _, u, _ in http.calls) == 1
    emmeloord = trips["Produktieweg 9, 8304AV Emmeloord"]
    assert emmeloord.km == 100 and emmeloord.minutes == 60 and not emmeloord.approx
    assert emmeloord.cost == pytest.approx(200 / 16 * 2.0)  # there and back, 1 op 16, €2/l = €25
    assert trips["Zuiderweg 21, 3769AB Soesterberg"].km == 300
    # pickup addresses are remembered; the home address is not stored anywhere
    assert set(cache) == {"Produktieweg 9, 8304AV Emmeloord", "Jan van Hooffstraat 3, Eindhoven"}
    assert "Dorpsstraat" not in str(cache)
    http.calls.clear()
    TripPlanner(http, cache, NOW, costs, HOME).plan({"Produktieweg 9, 8304AV Emmeloord": None})
    assert sum("Produktieweg" in u for _, u, _ in http.calls) == 0  # looked up once


def test_trip_planner_without_route_planner_uses_straight_line():
    def down(m, u, kw):
        raise RuntimeError("HTTP 503")

    http = FakeHttp([(url_has("api.pdok.nl"), pdok_route), (url_has("router.project-osrm.org"), down)])
    planner = TripPlanner(http, {}, NOW, DrivingCosts(), HOME)
    trip = planner.plan({"Produktieweg 9, 8304AV Emmeloord": None})["Produktieweg 9, 8304AV Emmeloord"]
    assert trip.approx and 140 < trip.km < 160  # ~120 km in a straight line x 1.3
    assert planner.error


def test_no_home_address_no_trips():
    http = FakeHttp([])
    assert TripPlanner(http, {}, NOW, DrivingCosts(), "").plan({"Produktieweg 9, 8304AV Emmeloord": None}) == {}
    assert http.calls == []


ENERGIA = """<table><tr><th>Product</th><th>Maximumprijs</th><th>Geldig</th></tr>
<tr><td>Benzine 95 RON - E10</td><td>€/l 2.1080</td><td>vanaf 2026-09-26</td></tr>
<tr><td>Benzine 98 RON - E5</td><td>€/l 2.2470</td><td>vanaf 2026-09-26</td></tr>
<tr><td>Diesel B7</td><td>€/l 2.0460</td><td>vanaf 2026-09-25</td></tr></table>"""


def test_fuel_price_from_the_official_maximum_prices():
    assert parse_fuel_price(ENERGIA) == (2.108, "2026-09-26")
    assert parse_fuel_price("<p>Super 95 (E10) 2,0450 €/l</p>") == (2.045, None)
    assert parse_fuel_price("<p>geen prijzen</p>") is None


def test_fuel_price_setting_cache_and_fallback():
    state = {}
    http = FakeHttp([(url_has("energiafed.be"), ENERGIA)])
    assert fuel_price(http, state, NOW, 1.95, 2.1) == (1.95, "your setting")
    price, source = fuel_price(http, state, NOW, "auto", 2.1)
    assert price == 2.108 and "Euro 95 E10" in source and len(http.calls) == 1
    fuel_price(http, state, NOW + timedelta(hours=2), "auto", 2.1)
    assert len(http.calls) == 1  # read once a day

    def down(m, u, kw):
        raise RuntimeError("HTTP 500")

    broken = FakeHttp([(url_has("energiafed.be"), down)])
    assert fuel_price(broken, state, NOW + timedelta(days=2), "auto", 2.1)[0] == 2.108  # saved price
    assert fuel_price(broken, {}, NOW, "auto", 2.1) == (2.1, "fallback in config.yml")


def test_two_addresses_each_lot_from_the_closer_one():
    """HOME_ADDRESS in the Netherlands, HOME_ADDRESS_2 in Ghent: Belgian pickups are driven from Ghent."""
    def nominatim(m, u, kw):
        assert "0202" not in u  # the flat number ("14-0202") is left out of the lookup
        return [{"lat": "51.02", "lon": "3.69"}]

    def two_starts(m, u, kw):
        coords = u.split("/driving/")[1].split("?")[0].split(";")
        start, places = coords[0], coords[1:]
        from_ghent = start.startswith("3.69")
        # Lokeren is near Ghent, Emmeloord is far from it
        km = {"3.990000,51.100000": 30 if from_ghent else 150, "5.720000,52.700000": 300 if from_ghent else 140}
        return {"code": "Ok", "distances": [[0] + [km[p] * 1000 for p in places]],
                "durations": [[0] + [km[p] * 60 for p in places]]}

    http = FakeHttp([(url_has("api.pdok.nl"), pdok_route), (url_has("nominatim.openstreetmap.org"), nominatim),
                     (url_has("router.project-osrm.org"), two_starts)])
    cache = {}
    planner = TripPlanner(http, cache, NOW, DrivingCosts(km_per_liter=16, fuel_price=2.0), HOME,
                          second_address="Raymonde de Larochelaan 14-0202, Gent, België")
    trips = planner.plan({"Industriepark 1, Lokeren": (51.1, 3.99), "Produktieweg 9, 8304AV Emmeloord": None})
    lokeren, emmeloord = trips["Industriepark 1, Lokeren"], trips["Produktieweg 9, 8304AV Emmeloord"]
    assert (lokeren.km, lokeren.origin) == (30, "Gent") and (emmeloord.km, emmeloord.origin) == (140, "home")
    assert lokeren.as_dict()["from"] == "Gent"
    assert "Larochelaan" not in str(cache) and "Dorpsstraat" not in str(cache)  # home addresses are never stored
    # with one address there is no "from"
    one = TripPlanner(http, {}, NOW, DrivingCosts(), HOME).plan({"Industriepark 1, Lokeren": (51.1, 3.99)})
    assert "from" not in one["Industriepark 1, Lokeren"].as_dict()
