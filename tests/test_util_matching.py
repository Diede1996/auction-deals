from datetime import datetime, timezone

from scanner.matching import bankruptcy_matcher, match_lots, matches, search_terms
from scanner.models import Lot, WatchItem
from scanner.util import AMS, parse_dutch_datetime, parse_money, parse_relative_close

NOW = datetime(2026, 9, 22, 19, 30, tzinfo=timezone.utc)  # 21:30 in Amsterdam


def test_parse_money():
    assert parse_money("€ 1.250,00") == 1250.0
    assert parse_money("110,00") == 110.0
    assert parse_money("€ 60") == 60.0
    assert parse_money("€\xa0110") == 110.0
    assert parse_money("€ 1.250") == 1250.0
    assert parse_money("") is None
    assert parse_money(None) is None


def test_parse_dutch_datetime():
    assert parse_dutch_datetime("28 sep. 2026 20:00:00 (CEST)") == datetime(2026, 9, 28, 20, 0, tzinfo=AMS)
    assert parse_dutch_datetime("28 Sep 2026 20:05:00 (CEST)") == datetime(2026, 9, 28, 20, 5, tzinfo=AMS)
    assert parse_dutch_datetime("5 Oktober 2026") == datetime(2026, 10, 5, 23, 59, tzinfo=AMS)
    assert parse_dutch_datetime("maandag 28 september 2026 vanaf 20:00") == datetime(2026, 9, 28, 20, 0, tzinfo=AMS)
    assert parse_dutch_datetime("12 mrt 2027 09:30") == datetime(2027, 3, 12, 9, 30, tzinfo=AMS)
    assert parse_dutch_datetime("nonsense") is None


def test_parse_relative_close():
    assert parse_relative_close("morgen vanaf 20:25", NOW) == datetime(2026, 9, 23, 20, 25, tzinfo=AMS)
    assert parse_relative_close("vandaag vanaf 23:10", NOW) == datetime(2026, 9, 22, 23, 10, tzinfo=AMS)
    assert parse_relative_close("6 dagen", NOW) == datetime(2026, 9, 28, 19, 30, tzinfo=timezone.utc)
    assert parse_relative_close("2 uur", NOW) == datetime(2026, 9, 22, 21, 30, tzinfo=timezone.utc)
    assert parse_relative_close("??", NOW) is None


def test_keyword_matching():
    ps5 = WatchItem(name="PS5", keywords=["playstation 5", "ps5"], exclude=["controller", "game"])
    assert matches(ps5, "Sony PlayStation 5 Slim 1TB")
    assert matches(ps5, "PS5 disc edition")
    assert not matches(ps5, "PlayStation 5 controller DualSense")
    assert not matches(ps5, "PlayStation 4 Pro")  # "5" must be a whole number
    assert not matches(ps5, "Playstation 55 inch tv stand")
    iphone = WatchItem(name="iPhone 15", keywords=["iphone 15"])
    assert matches(iphone, "Apple iPhone15 Pro 128GB")  # glued together
    assert matches(iphone, "Apple iPhone 15 Pro")
    assert not matches(iphone, "Apple iPhone 150")
    assert not matches(iphone, "Apple iPhone150")
    assert matches(iphone, "APPLE IPHONE15PRO")
    dyson = WatchItem(name="Dyson", keywords=["dyson"])
    assert matches(dyson, "Dyson V15 Detect stofzuiger")
    assert matches(WatchItem(name="é", keywords=["senseo"]), "Philips Sénseo koffiezetapparaat")


def test_match_lots_first_item_wins():
    items = [WatchItem(name="MacBook Pro", keywords=["macbook pro"]), WatchItem(name="MacBook", keywords=["macbook"])]
    lots = [Lot("s", "1", "Apple MacBook Pro 14 M1", "u", 10, None),
            Lot("s", "2", "MacBook Air 2020", "u", 10, None),
            Lot("s", "3", "Stoel", "u", 10, None)]
    result = match_lots(items, lots)
    assert [(i.name, l.lot_id) for i, l in result] == [("MacBook Pro", "1"), ("MacBook", "2")]


def test_search_terms_dedupes():
    items = [WatchItem(name="a", keywords=["PS5", "playstation 5"]), WatchItem(name="b", keywords=["ps5"])]
    assert search_terms(items) == ["PS5", "playstation 5"]


def test_bankruptcy_matcher():
    is_b = bankruptcy_matcher(["faillissement", "failliet", "curator"])
    assert is_b("Faillissementsveiling horeca-apparatuur")
    assert is_b("Online veiling i.o.v. diverse curatoren")
    assert is_b("Restaurant ‘Havenkwartier by Waggie’ uit faillissement")
    assert not is_b("Bedrijfsbeëindiging hoveniersbedrijf")
    assert not is_b("Verzendveiling: Huishoudelijke artikelen")


def test_shipped_config_includes_business_closures():
    from pathlib import Path
    import yaml
    config = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yml").read_text())
    is_b = bankruptcy_matcher(config["auction_keywords"])
    assert is_b("Bedrijfsbeëindiging hoveniersbedrijf Valkenswaard")
    assert is_b("Bedrijfsbeeindiging: Showroomkeukens en inbouwapparatuur")
    assert is_b("Veiling van domeinnamen wegens algehele liquidatie van Curatoren.nl B.V.")
    assert is_b("Faillissementsveiling horeca-apparatuur")
    assert not is_b("Verzendveiling: Huishoudelijke artikelen, audio, witgoed")
    assert not is_b("Thuisbezorgveiling hardhout, douglas, vuren en overig")
