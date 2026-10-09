"""Lots with a defect are left out; storage, memory and 5G from the lot are used on Marktplaats."""
import pytest

from scanner.condition import defect, defect_filter, lot_text
from scanner.identify import lot_options, plan_for, ram_gb, storage_gb
from scanner.models import Lot, WatchItem
from scanner.util import normalize

PHONE = WatchItem.from_dict({"name": "Phone", "keywords": ["iphone", "galaxy", "google pixel"]})
LAPTOP = WatchItem.from_dict({"name": "Laptop", "keywords": ["laptop", "latitude", "elitebook"]})
MONITOR = WatchItem.from_dict({"name": "Monitor", "keywords": ["monitor"]})
MACBOOK = WatchItem.from_dict({"name": "MacBook", "keywords": ["macbook"]})


def lot(title, description=""):
    return Lot("openbareverkopen", "1", title, "https://x/1", 14.0, None, description=description)


# Wording seen on Dutch, Flemish and Walloon auction sites (and found in review, 9 Oct 2026)
DEFECTS = [
 ("Samsung Galaxy A12","64 GB , Scherm beschadigd"), ("Samsung Galaxy A12","64 GB\nZonder lader\nScherm beschadigd"),
 ("Samsung Galaxy A12","64 GB Zonder lader Scherm beschadigd"), ("x","Geen simkaart Scherm gebarsten"),
 ("Samsung Galaxy A12","64 GB - Zonder lader - Scherm beschadigd"), ("iPhone 11 64GB","Verkocht zonder garantie - werkt niet"),
 ("iPhone 11 64GB","iCloud vrij - accu defect"), ("iPhone 11","Niet getest / defect"), ("iPad","Geen hoes – scherm gebarsten"),
 ("iPhone 11 zonder lader scherm kapot",""), ("iPhone 11","Geen krassen maar scherm gebarsten"), ("x","Geen adapter defect"),
 ("x","Zonder lader defect"), ("x","zonder voeding werkt niet"), ("x","Zonder doos Scherm beschadigd"),
 ("iPhone 11 64GB","64 GB. Let op: scherm gebarsten"), ("x","LET OP: toestel is iCloud locked!"), ("x","Let op : werkt niet"),
 ("x","Werkend: Nee"), ("x","Geen lader, scherm beschadigd"), ("x","Geen lader, behuizing heeft deuken en schade"),
 ("x","Zonder lader, krassen en schade op behuizing"), ("x","Zonder lader, scherm gebarsten en accu defect"),
 ("Laptop","Verkocht zonder garantie, defect"), ("x","Zonder garantie, behuizing gebarsten of beschadigd"),
 ("x","Retourgoed, mogelijk defect"), ("x","Mogelijk beschadigd scherm"), ("x","Mogelijk iCloud locked"), ("x","Mogelijk waterschade"),
 ("x","Eventueel defect"), ("x","Functioneert niet"), ("x","Niet functionerend"), ("x","Niet volledig werkend"), ("x","Deels werkend"),
 ("x","Werkt maar half"), ("x","Gaat niet meer aan"), ("x","Doet niets meer"), ("x","Laadt niet op"), ("x","Touchpad reageert niet"),
 ("x","Geen beeld"), ("x","Dode pixels en strepen in beeld"), ("x","Met gebreken"), ("x","Geeft storing"), ("x","Pompdefect"),
 ("x","Moederborddefect"), ("x","Accudefect"), ("x","Scherm beschadigt"), ("x","Scherm gebarst"), ("x","Scherm gesprongen"),
 ("x","Scherm gescheurd"), ("x","Accu bol"), ("x","BIOS wachtwoord, BIOS locked"), ("x","Zoek mijn iPhone staat aan"),
 ("x","Écran fissuré"), ("x","Écran brisé"), ("x","En panne"), ("x","Ne fonctionne plus"), ("x","Ne s'allume pas"),
 ("x","Bloqué iCloud"), ("x","Dégâts des eaux"), ("x","À réparer"), ("x","Does not work"), ("x","Doesn't turn on, screen smashed"),
 ("Partij telefoons, deels defect",""), ("x","Kleine barst in het scherm"), ("DeWalt","Conditie: Defect Garantie: Geen"),
 ("Makita accu","Accu defect"), ("Laptop HP voor onderdelen",""), ("x","waterschade"), ("x","pincode onbekend"),
 ("x","Activatieslot aan"), ("x","iCloud lock: onbekend"), ("Koffiemachine","Niet werkend"), ("Monitor","Beschadigde doos, scherm werkt niet"),
 ("x", "Samsung Galaxy S21 128GB; zie afbeeldingen. Lees a.u.b. ook de extra veilinginformatie voor deze veiling!. Conditie: Defect Garantie: Geen"),
 ("Smeg waterkoker", "Kleur: gebroken wit, scherm gebroken"),
]

NOT_DEFECTS = [
 ("Samsung Galaxy A12","64 GB"), ("iPhone 12","Geen schade, werkt perfect"), ("iPhone 12","Toestel is niet beschadigd"),
 ("Laptop","Zonder krassen of schade"), ("Kostuum","zonder enige vorm van schade"), ("Monitor","Doos beschadigd, monitor nieuw"),
 ("Monitor","Beschadigde verpakking"), ("Monitor","Kavels worden verkocht zonder garantie op eventuele defecten"),
 ("iPad","iCloud vrij, gereset"), ("Laptop","1 stuk, lichte krassen"), ("Laptop","schadevrij"), ("Laptop","niet getest"),
 ("Plant","Plant in pot, kans op schade tijdens transport"), ("x","geen zichtbare schade"), ("x","vrij van schade"), ("x","no damage"),
 ("x","iCloud lock: Nee"), ("x","iCloud locked: Nee"), ("x","iCloud vergrendeld: nee"), ("x","Activation lock: off"),
 ("x","Activatieslot: uit"), ("x","Activatieslot is uit"), ("x","Activatieslot uitgeschakeld, gereset"), ("x","Activatieslot niet actief"),
 ("x","iCloud lock verwijderd, fabrieksinstellingen"), ("x","Schade: geen"), ("x","Defect: Nee"), ("x","Waterschade: nee"),
 ("x","Beschadigingen: geen zichtbare"), ("iPhone 13","Geen krassen, deuken of beschadigingen"), ("iPhone 13","Zonder krassen, deuken of schade"),
 ("Monitor","Geen zichtbare krassen, deuken of beschadigingen"), ("Monitor","Vrij van krassen, deuken en schade"),
 ("Monitor","In nieuwstaat, zonder gebruikssporen, krassen of schade"), ("Laptop","Geen accu, lader of schade"),
 ("Dell monitor U2722D","Nieuw in doos. De doos is licht beschadigd"), ("x","Nieuw, verpakking is enigszins beschadigd"),
 ("x","Nieuw, kapotte doos"), ("x","Nieuw met verpakkingsschade"), ("x","Retour. Verpakking: beschadigd"), ("x","Nieuw, omverpakking beschadigd"),
 ("x","Nieuw, buitenverpakking beschadigd"), ("x","Nieuw, lichte transportschade aan de doos"), ("x","Nieuw, schade aan de buitenverpakking"),
 ("x","Neuf, carton endommagé"), ("Smeg waterkoker KLF03 gebroken wit",""), ("Smoking gebroken wit maat 50",""),
 ("Hugo Boss colbert","Kleur gebroken wit, nieuw met label"), ("Elho pot 40 cm","Pot gebroken wit, Ø 40 cm"),
 ("Smeg waterkoker KLF03","Kleur: gebroken wit"), ("Colbert","Kleur: off-white / gebroken wit"),
 ("Partij 20 iPhones","Toestellen zijn stuk voor stuk getest en werken"), ("Monitor","Alle monitoren zijn stuk voor stuk gecontroleerd"),
 ("Smartphone","Toolkit voor reparatie van smartphones"), ("Laptop HP ProBook","Veilinghuis is niet aansprakelijk voor schade"),
 ("Monitor Philips","Licht beschadigd aan de voet, scherm perfect"), ("iPhone 14","Kleine beschadiging op de hoek, scherm zonder krassen"),
 ("Dyson airfryer","B-keuze met lichte schade aan de behuizing"), ("MacBook Air","Lichte schade aan de onderkant"),
 ("Makita 2 stuks", "Makita gereedschapskoffers; aantal 2 stuks; zie afbeeldingen. Conditie: Gebruikt Garantie: Geen"),
 ("Laptop","HP laptop 8GB. Voor meer informatie: eventuele beschadigingen en defecten zijn voor rekening koper"),
 ("Laptop","Dit betreft een tijdens transport kwetsbaar product! Risico op beschadiging"),
 ("x","Koper aanvaardt verborgen gebreken"), ("Samsung Galaxy A12","Opslag: 64 GB, Werkgeheugen: 4 GB"),
 ("DeWALT TSTAK","Conditie: Nieuw"), ("x","Staat: Geen schade"), ("Smeg toaster","Pastel blauw, nieuwstaat"),
 ("iPhone 12 128GB","Getest en werkend, accu 89%"), ("Laptop Dell","i5 8GB 256GB SSD, Windows 11"),
]


@pytest.mark.parametrize("title, description", DEFECTS)
def test_defects(title, description):
    assert defect(title, description)


@pytest.mark.parametrize("title, description", NOT_DEFECTS)
def test_not_defects(title, description):
    assert defect(title, description) is None


def test_condition_field():
    assert defect("DeWalt TSTAK", "", condition="Defect") and not defect("DeWalt TSTAK", "", condition="Nieuw")
    assert not defect("x", "", condition="Gebruikt")


def test_lot_text_stops_at_standard_text():
    assert lot_text("1 x Apple MacBook Pro laptop, 2019 Tijdens de veiling kunnen wij actieve bieders ...") == \
        "1 x Apple MacBook Pro laptop, 2019 "
    assert defect_filter({}) and not defect_filter({"condition": {"hide_defects": False}})


def test_storage_and_memory():
    assert storage_gb(normalize("4GB RAM, 64GB")) == "64" and storage_gb(normalize("6/128GB")) == "128"
    assert storage_gb(normalize("Samsung Galaxy A12")) is None
    assert ram_gb(normalize("i5, 256GB SSD, 8GB RAM")) == "8" and ram_gb(normalize("i5 16GB 512GB")) == "16"
    assert ram_gb(normalize("512 GB SSD")) is None


@pytest.mark.parametrize("item, title, description, options", [
    (PHONE, "Apple iPhone 13 5G 128GB", "", ("5g", "128gb")),
    (PHONE, "Partij 10 x Apple iPhone 8 64GB / 256GB", "", ()),  # mixed sizes: no storage
    (PHONE, "3 x iPhone 12 64GB en 128GB", "", ()),
    (PHONE, "Samsung Galaxy A12", "32GB, inclusief 64GB micro SD kaart", ("32gb",)),  # not the memory card
    (PHONE, "Samsung Galaxy A12", "128GB, uitbreidbaar tot 512 GB", ("128gb",)),
    (PHONE, "Samsung Galaxy A12", "Opslag: 64 GB, Werkgeheugen: 4 GB", ("64gb",)),
    (MACBOOK, 'Apple MacBook Pro 16" i7 2019 Radeon Pro 5300M 4GB, 16GB', "", ("16gb",)),  # not the graphics card
    (MACBOOK, "Apple MacBook Pro 16”, Apple M1 Max, 32 GB", "", ("32gb",)),
    (LAPTOP, "Lenovo Legion 5 laptop", "Processor: AMD Ryzen 7 5800H, Werkgeheugen: 16 GB, Opslag: 512 GB SSD, "
                                       "Videokaart: RTX 3070 8 GB", ("16gb",)),
    (LAPTOP, "Lenovo ThinkPad T14 Gen 2", "Werkgeheugen: 8 GB, Max. geheugen: 32 GB, SSD 256 GB", ("8gb",)),
    (MONITOR, "Dell P2419H monitor + Dell Latitude 5490 laptop 8GB", "", ()),  # priced as a monitor
])
def test_options_found(item, title, description, options):
    assert lot_options(lot(title, description), item) == options


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
    # 5G + 128 GB, 128 GB, 5G, any: storage counts more than 5G
    assert [r.label for r in plan.rules] == ["galaxy a13 5g 128gb", "galaxy a13 128gb", "galaxy a13 5g", "galaxy a13"]
    assert [r.matches("galaxy a13 5g 128gb") for r in plan.rules] == [True, True, True, True]
    assert [r.matches("galaxy a13 128gb") for r in plan.rules] == [False, True, False, True]
    assert "128 GB" in plan.note
