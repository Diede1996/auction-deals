import yaml

from scanner.evaluate import Fees, Settings, evaluate
from scanner.models import Lot, WatchItem

ITEM = WatchItem.from_dict({"name": "Suit / formalwear", "keywords": ["colbert", "smoking"], "price_table": [
    {"match": ["hugo smoking colbert"], "price": 125, "unless": ["pantalon"]},
    {"match": ["hugo smoking", "boss smoking"], "price": 250},
    {"match": ["hugo colbert"], "price": 125},
]})


def test_first_match_wins_and_unless():
    assert ITEM.table_price("HUGO Arti224E1X smoking colbert zwart 52") == 125
    assert ITEM.table_price("HUGO smoking (colbert + pantalon) 52") == 250
    assert ITEM.table_price("Suitsupply colbert") is None


def test_round_trip():
    assert WatchItem.from_dict(ITEM.to_dict()).price_table == ITEM.price_table


def test_table_price_values_the_lot_without_marktplaats():
    lot = Lot("troostwijk", "1", "HUGO colbert Arti253X grijs 48", "https://x/1", 20.0, None)
    v = evaluate(ITEM, lot, Fees(), Settings(resale_factor=0.75, min_margin=0.25, min_profit=25), None)
    assert v.max_bid and v.max_bid > 0  # €125 x 75% leaves room at €20


def test_watchlist_tables_parse():
    items = [WatchItem.from_dict(i) for i in yaml.safe_load(open("watchlist.yml"))["items"]]
    by = {i.name: i for i in items}
    assert by["Suit / formalwear"].table_price("HUGO pak blauw 52 (colbert + pantalon)") == 250
    assert by["Outdoor jacket"].table_price("Barbour Bedale waxjas") == 100
    assert by["Outdoor jacket"].table_price("Barbour trui") is None
