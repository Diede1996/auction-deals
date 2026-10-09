"""Lots with a defect are left out; storage, memory and 5G from the lot are used on Marktplaats."""
import pytest

from scanner.condition import defect, defect_filter, lot_text
from scanner.identify import lot_options, plan_for, ram_gb, storage_gb
from scanner.models import Lot, WatchItem
from scanner.util import normalize

PHONE = WatchItem.from_dict({"name": "Phone", "keywords": ["iphone", "galaxy", "google pixel"]})
LAPTOP = WatchItem.from_dict({"name": "Laptop", "keywords": ["laptop", "latitude", "elitebook"]})
MONITOR = WatchItem.from_dict({"name": "Monitor", "keywords": ["monitor"]})


def lot(title, description=""):
    return Lot("openbareverkopen", "1", title, "https://x/1", 14.0, None, description=description)


@pytest.mark.parametrize("title, description", [
    ("Samsung Galaxy A12", "64 GB , Scherm beschadigd"),  # Diede's screenshot, 9 Oct
    ("Laptop", "Verkocht zonder garantie, defect"),
    ("Monitor", "Beschadigde doos, scherm werkt niet"),
    ("iPad", "iCloud locked"),
    ("Makita accu", "Accu defect"),
    ("DeWalt", "Conditie: Defect Garantie: Geen"),
    ("Laptop", "niet getest scherm beschadigd"),
    ("Laptop", "waterschade"),
    ("Laptop HP voor onderdelen", ""),
    ("iPhone 12 kapot", ""),
    ("iPhone", "Scherm gebarsten"),
    ("iPhone", "Batterij is stuk"),
    ("Koffiemachine", "Niet werkend"),
    ("Koffiemachine", "werkt niet meer"),
    ("Ordinateur portable", "écran cassé"),
    ("Ordinateur portable", "ne fonctionne pas"),
    ("Laptop", "pincode onbekend"),
    ("MacBook", "Activatieslot aan"),
])
def test_defects(title, description):
    assert defect(title, description)


@pytest.mark.parametrize("title, description", [
    ("Samsung Galaxy A12", "64 GB"),
    ("iPhone 12", "Geen schade, werkt perfect"),
    ("iPhone 12", "Toestel is niet beschadigd"),
    ("Laptop", "Zonder krassen of schade"),
    ("Kostuum", "zonder enige vorm van schade"),
    ("Monitor", "Doos beschadigd, monitor nieuw"),
    ("Monitor", "Beschadigde verpakking"),
    ("Monitor", "Kavels worden verkocht zonder garantie op eventuele defecten"),
    ("iPad", "iCloud vrij, gereset"),
    ("Laptop", "1 stuk, lichte krassen"),
    ("Laptop", "schadevrij"),
    ("Laptop", "niet getest"),
    ("Plant", "Plant in pot, kans op schade tijdens transport"),
    ("Makita 2 stuks", "Makita gereedschapskoffers; aantal 2 stuks; zie afbeeldingen. Conditie: Gebruikt Garantie: Geen"),
    # the auction house's standard text after the lot's own description doesn't count
    ("Laptop", "HP laptop 8GB. Voor meer informatie: eventuele beschadigingen en defecten zijn voor rekening koper"),
    ("Laptop", "Dit betreft een tijdens transport kwetsbaar product! Risico op beschadiging"),
])
def test_not_defects(title, description):
    assert defect(title, description) is None


def test_lot_text_stops_at_standard_text():
    assert lot_text("1 x Apple MacBook Pro laptop, 2019 Tijdens de veiling kunnen wij actieve bieders ...") == \
        "1 x Apple MacBook Pro laptop, 2019 "
    assert defect_filter({}) and not defect_filter({"condition": {"hide_defects": False}})


def test_storage_and_memory():
    assert storage_gb(normalize("4GB RAM, 64GB")) == "64" and storage_gb(normalize("6/128GB")) == "128"
    assert storage_gb(normalize("Samsung Galaxy A12")) is None
    assert ram_gb(normalize("i5, 256GB SSD, 8GB RAM")) == "8" and ram_gb(normalize("i5 16GB 512GB")) == "16"
    assert ram_gb(normalize("512 GB SSD")) is None


def test_options_from_title_or_description():
    assert lot_options(lot("Samsung Galaxy A12", "64 GB , Scherm beschadigd"), PHONE) == ("64gb",)
    assert lot_options(lot("Samsung Galaxy A13 5G", "4GB RAM 64GB"), PHONE) == ("5g", "64gb")
    assert lot_options(lot("Dell Latitude 5410", "i5, 16 GB RAM, 256 GB SSD"), LAPTOP) == ("16gb",)
    assert lot_options(lot("Dell 24 inch monitor", "type U2419 HC"), MONITOR) == ()
    # the auction house's text isn't about the lot
    assert lot_options(lot("Samsung Galaxy A12", "Voor meer informatie: 128 GB"), PHONE) == ()


def test_options_in_the_marktplaats_search():
    plan = plan_for(PHONE, lot("Samsung Galaxy A12", "64 GB"))
    assert plan.searches[:2] == ["galaxy a12 64gb", "galaxy a12"] and plan.options == ("64gb",)
    first, fallback = plan.rules[0], plan.rules[-1]
    assert first.matches("samsung galaxy a12 64gb zwart") and not first.matches("samsung galaxy a12 32gb")
    assert fallback.matches("samsung galaxy a12 32gb")
    plan = plan_for(PHONE, lot("Samsung Galaxy A13 5G", "128 GB"))
    assert plan.searches[0] == "galaxy a13 5g 128gb"
    assert [r.matches("galaxy a13 5g 128gb") for r in plan.rules] == [True, True, True]
    assert [r.matches("galaxy a13 128gb") for r in plan.rules] == [False, False, True]
    assert "128 GB" in plan.note
