"""Which item is this lot exactly? Titles are real lot titles from the scans of 24-27 Sep 2026."""
import pytest
from scanner.identify import brand_in, model_code, model_in, plan_for, quantity
from scanner.models import Lot, WatchItem
from scanner.util import normalize

MONITOR = WatchItem(name="Monitor", keywords=["monitor", "beeldscherm"])
LAPTOP = WatchItem(name="Laptop", keywords=["thinkpad", "elitebook", "surface pro", "laptop", "notebook"])
TOOLS = WatchItem(name="Power tools", keywords=["makita", "dewalt", "hilti", "metabo", "festool"])
COFFEE = WatchItem(name="Coffee machine", keywords=["jura", "nespresso", "koffiemachine", "volautomaat"])
SUITS = WatchItem(name="Suit", keywords=["suitsupply", "hugo boss", "kostuum", "colbert"])
PLANTS = WatchItem(name="Plants", keywords=["kunstplant", "kunstbloemen", "olijfboom"])


def plan(item, title):
    return plan_for(item, Lot("s", "1", title, "u", 1, None))


@pytest.mark.parametrize("title, model", [
    ("Curved beeldscherm 27 inch SAMSUNG S27C366EAU. Krasje in scherm", "s27c366eau"),
    ("Twee 27 inch beeldschermen ACER V277", "v277"),
    ("Twee beeldschermen HP COMPAQ LA2306x . In hoogte verstelbaar en draaibaar", "la2306x"),
    ("Laptop Lenovo THINKPAD T440S. 128 GB HDD. 8 GB RAM. Intel Core i5", "t440s"),
    ("Laptop TERRA Mobile 1513A. 233 GB HDD. 4 GB werkgeheugen.", "1513a"),
    ("Hilti DX 460 kruitschiethamer in koffer", "dx 460"),
    ("Makita JR 3030T reciprozaag", "jr 3030t"),
    ("Makita AVT Breekhamer HM1812", "hm1812"),
    ("DEWALT Accuboormachine DC910; 28V, incl. accu & oplaadstation", "dc910"),
    ("DEWALT XRP Accu klopboormachine DC925 493905-01", "dc925"),
    ("Makita Acculader DC18RC T incl. Accu 5.0Ah, 18V Lithium-ion", "dc18rc"),
    ("Hilti Meetstatief PUA 25", "pua 25"),
    ("Philips HD7695/90 Intense koffiemachine", "hd7695"),
    ("De'Longhi Magnifica S ECAM22.110.B", "ecam22"),
    ("Festool TS 55 invalzaag in systainer", "ts 55"),
    # specs, processors and sizes are not model numbers
    ("Laptop HP i5-fcoxx. 238 GB opslag. 8GB RAM. AMD Ryzen 3 7320u 2.40 GHz processor", None),
    ("Beeldscherm LENOVO 22 inch, voedingskabel ontbreekt", None),
    ("Colbert, maat 50, Dsquared2", None),
    ("2 x Kunstplant in pot. Hoogte ca. 240 cm", None),
    ("Bouwradio DEWALT ( exclusief accu )", None),
    ("Apple iPad Air 5 64GB A2588 wifi", None),
])
def test_model_code(title, model):
    found = model_code(title)
    assert (found[0] if found else None) == model


def test_exact_plans_search_brand_and_model():
    p = plan(MONITOR, "Curved beeldscherm 27 inch SAMSUNG S27C366EAU. Krasje in scherm")
    assert p.kind == "exact" and p.searches == ["samsung s27c366eau", "s27c366eau"] and p.model == "S27C366EAU"
    # a short model number needs the brand in the listing too
    p = plan(MONITOR, "Twee 27 inch beeldschermen ACER V277")
    assert p.searches == ["acer v277"] and p.rules[0].groups == [("acer",)]
    assert p.rules[0].matches(normalize("Acer V277 27 inch full hd"))
    assert not p.rules[0].matches(normalize("Philips V277 lamp"))
    # the brand closest to the model: "HP COMPAQ LA2306x" -> compaq
    assert plan(MONITOR, "Twee beeldschermen HP COMPAQ LA2306x").searches[0] == "compaq la2306x"
    # the watchlist keyword is the brand
    assert plan(TOOLS, "Makita JR 3030T reciprozaag").searches == ["makita jr 3030t", "makita jr3030t"]
    assert plan(LAPTOP, "Microsoft Surface Pro 1796 i5").searches == ["surface pro 1796"]


def test_general_plans_are_rough_and_never_just_a_brand():
    p = plan(MONITOR, "Beeldscherm LENOVO 22 inch, voedingskabel ontbreekt")
    assert p.kind == "general" and p.searches == ["monitor lenovo"]
    assert p.rules[0].matches(normalize("Lenovo beeldscherm 22 inch"))  # monitor = beeldscherm
    p = plan(TOOLS, "Bouwradio MAKITA zonder accu")
    assert [r.label for r in p.rules] == ["makita bouwradio accu", "makita bouwradio"]  # never "makita" alone
    p = plan(SUITS, "Colbert, maat 50, Loro Piana")
    assert p.searches == ["colbert loro piana"] and p.rules[0].matches(normalize("Loro Piana blazer maat 50"))
    p = plan(PLANTS, "Kunstplant olijfboom 180 cm in pot")
    assert p.searches[-1] == "kunstplant olijfboom"
    assert plan(LAPTOP, "Laptop HP i5-fcoxx. 8GB RAM. AMD Ryzen 3 7320u").searches[-1] == "laptop hp"
    assert plan(COFFEE, "Jura E8 volautomaat").searches == ["jura e8 volautomaat", "jura e8"]
    assert plan(TOOLS, "Makita") is None


def test_custom_marktplaats_query_wins():
    item = WatchItem(name="PS5", keywords=["ps5"], marktplaats_query="playstation 5 console")
    p = plan(item, "PS5 met controller")
    assert p.kind == "custom" and p.searches == ["playstation 5 console"]


def test_model_in_handles_spaces_dashes_and_variants():
    assert model_in("jr3030t", normalize("Makita JR-3030T reciprozaag"))
    assert model_in("jr3030t", normalize("Makita JR 3030 T"))
    assert model_in("dx460", normalize("Hilti DX460 schiethamer"))
    assert not model_in("dx460", normalize("Hilti DX 4600"))
    assert model_in("s27c366eau", normalize("Samsung S27C366EAUXEN"))
    assert model_in("hd7695", normalize("Philips HD 7695/90"))
    assert not model_in("t440s", normalize("Lenovo T440 laptop"))


def test_brand_in():
    assert brand_in("Twee beeldschermen HP COMPAQ LA2306x", before=4) == "compaq"
    assert brand_in("Colbert, maat 50, Loro Piana") == "loro piana"
    assert brand_in("Koffiemachine zonder merk") is None


@pytest.mark.parametrize("title, n", [
    ("2 x Kunstplant in pot", 2), ("2x Vaas met kunstbloemen", 2), ("Twee 27 inch beeldschermen ACER V277", 2),
    ("Makita gereedschapskoffers - 2 stuks", 2), ("Ca. 41x Man, overjassen en colberts", 41),
    ("40x Colbert heren", 40), ("40 x Colbert", 40), ("Colberts heren (40x)", 40), ("Colberts (40 stuks)", 40),
    ("Colberts x40", 40), ("Colberts 40x", 40),
    ("1 x HP Desktop Mini met 2 x 24-inch monitor", 1), ("Beeldscherm 27 inch ACER RG270", 1),
    ("Tafel 180 x 90 cm", 1),  # a size, not a quantity
])
def test_quantity(title, n):
    assert quantity(title) == n


def test_short_keywords_match_whole_words_only():
    from scanner.matching import matches
    plants = WatchItem(name="Plants", keywords=["plant", "planten", "pot"], exclude=["kunstplant"])
    assert not matches(plants, "2010 Pottinger Jumbo 7210 D Opraapwagen")  # "pot" is not "Pottinger"
    assert matches(plants, "Terracotta pot") and matches(plants, "3 potten met olijfboom") and matches(plants, "Potjes")
    assert matches(plants, "Plantenbak met vulling")  # longer words still match the start of a word
    # "poten" (legs) is the plural of "poot", not of "pot"; "plantaardig" is not a plant
    assert not matches(plants, "Eettafel Indra rechthoek 180x90cm - Acacia blad en zwarte poten")
    assert not matches(plants, "Witte Melamine Gecoate Eettafel met Metalen Poten")
    assert not matches(plants, "Ca. 24x Plantaardige Billendoekjes - 50 pack Bipsje")
    assert matches(plants, "Set Oranje Thee-Potten") and matches(plants, "Partij Gekleurde Potjes")
    leds = WatchItem(name="LED", keywords=["led"])
    assert matches(leds, "Partij leds") and not matches(leds, "Stoelen voor leden")
    bags = WatchItem(name="Bags", keywords=["tas"])
    assert matches(bags, "Laptop tassen") and matches(bags, "Tasje") and not matches(bags, "Tasman")
    assert model_code("Eettafel Indra rechthoek 180x90cm - Acacia blad") is None  # a size, not a type number
    laptop = WatchItem(name="Laptop", keywords=["laptop"], exclude=["tas", "arm"])
    assert not matches(laptop, "Laptop tassen 5 stuks") and matches(laptop, "Laptop met armatuur")


@pytest.mark.parametrize("title, kind, searches, model", [
    ("Apple MacBook Pro 16”, Apple M1 Max, 32 GB RAM, 1 TB NVMe Laptop", "exact",
     ["macbook pro 16 m1 max", "macbook pro m1 max"], "MacBook Pro 16 M1 MAX"),
    ("Apple MacBook Air 13 inch M2 2022 8GB", "exact", ["macbook air 13 m2", "macbook air m2"], "MacBook Air 13 M2"),
    ("Apple MacBook Pro 16“ Core(TM) i7 9th Gen, 32 GB RAM, 1 TB NVMe, AMD Radeon RX 5500 4GB Laptop", "general",
     ["macbook pro 16", "macbook pro intel"], None),
])
def test_macs_by_chip_and_size(title, kind, searches, model):
    p = plan(WatchItem(name="MacBook", keywords=["macbook"]), title)
    assert (p.kind, p.searches, p.model) == (kind, searches, model)


def test_mac_rules():
    m1max = plan(WatchItem(name="MacBook", keywords=["macbook"]), "Apple MacBook Pro 16”, Apple M1 Max, 32 GB").rules[0]
    assert m1max.matches(normalize("MacBook Pro 16 inch M1 Max 32GB 1TB"))
    assert m1max.matches(normalize('Apple Macbook Pro 16" M1 MAX 64gb'))
    assert not m1max.matches(normalize("Macbook pro 14 m1 max 32gb"))  # other size
    assert not m1max.matches(normalize("MacBook Pro M1 Pro 16 GB 14 inch"))  # 16 GB is memory, not the screen
    intel = plan(WatchItem(name="MacBook", keywords=["macbook"]), "Apple MacBook Pro 16“ Core(TM) i7 9th Gen").rules[0]
    assert intel.matches(normalize("MacBook Pro 16 inch 2019 i9 32GB"))
    assert not intel.matches(normalize("MacBook Pro 16 inch M1 Pro"))
    # an inch mark makes a number a size, not a model code
    assert model_code("Apple MacBook Pro 16” laptop") is None


@pytest.mark.parametrize("title, kind, searches, model", [
    ("iPad Pro 10,5 inch", "exact", ["ipad pro 10.5"], "iPad Pro 10.5"),
    ("iPad 6th Gen.", "exact", ["ipad 6", "ipad 2018"], "iPad 6th gen"),
    ("Apple iPad (6e generatie) 32GB", "exact", ["ipad 6", "ipad 2018"], "iPad 6th gen"),
    ("Apple iPad Air 5 64GB A2588 wifi", "exact", ["ipad air 5", "ipad air 2022"], "iPad Air 5th gen"),
    ("iPad Pro 11 M1 128GB", "exact", ["ipad pro m1"], "iPad Pro 11 M1"),
    ("iPad 10,2 inch 32GB", "general", ["ipad 10.2"], None),
])
def test_ipads_by_line_generation_and_size(title, kind, searches, model):
    p = plan(WatchItem(name="iPad", keywords=["ipad"]), title)
    assert (p.kind, p.searches, p.model) == (kind, searches, model)


def test_ipad_rules():
    pro = plan(WatchItem(name="iPad", keywords=["ipad"]), "iPad Pro 10,5 inch").rules[0]
    assert pro.matches(normalize("Apple iPad Pro 10.5 64GB wifi")) and pro.matches(normalize("iPad Pro 10,5 inch 256GB"))
    assert not pro.matches(normalize("iPad Pro 12.9 2017")) and not pro.matches(normalize("iPad Pro 10 5G"))
    six = plan(WatchItem(name="iPad", keywords=["ipad"]), "iPad 6th Gen.").rules[0]
    assert six.matches(normalize("Apple iPad 6e generatie 32GB")) and six.matches(normalize("iPad 2018 128gb"))
    assert not six.matches(normalize("iPad Pro 12.9 6e generatie")) and not six.matches(normalize("iPad 9 64GB"))


def test_accessories_are_not_the_device():
    from scanner.marktplaats import comparable_listings
    rule = plan(WatchItem(name="iPad", keywords=["ipad"]), "iPad Pro 10,5 inch").rules[0]

    def li(title, euros):
        return {"title": title, "priceInfo": {"priceType": "FIXED", "priceCents": euros * 100}, "itemId": title}

    found = comparable_listings([li("Apple Smart Keyboard iPad 7-9/Air 3/Pro 10.5 (K83)", 40),
                                 li("Tempered Glass iPad Air (2019)/iPad Pro 10.5", 8),
                                 li("Hoes voor iPad Pro 10.5", 15),
                                 li("Apple iPad Pro 10.5 64GB wifi", 180),
                                 li("iPad Pro 10,5 inch 256GB space grey", 240)], rule, [])
    assert [f["price"] for f in found] == [180, 240]


@pytest.mark.parametrize("title, searches, model", [
    ("HP ZBook Firefly G10 14”, Core(TM) i7 13th Gen, 32 GB", ["zbook firefly 14 g10", "zbook firefly g10"], "ZBook Firefly 14 G10"),
    ("Laptop HP EliteBook 840 G5 i5 8GB", ["elitebook 840 g5"], "EliteBook 840 G5"),
    ("HP ZBook Studio 16 G10 i7", ["zbook studio 16 g10", "zbook studio g10"], "ZBook Studio 16 G10"),
    ("HP 250 G8 laptop", ["hp 250 g8"], "HP 250 G8"),
])
def test_hp_line_model_and_generation(title, searches, model):
    p = plan(WatchItem(name="Laptop", keywords=["laptop", "zbook", "elitebook"]), title)
    assert (p.kind, p.searches, p.model) == ("exact", searches, model)


def test_zbook_g10_is_not_any_g10():
    rule = plan(WatchItem(name="Laptop", keywords=["zbook"]), "HP ZBook Firefly G10 14”, Core(TM) i7 13th Gen").rules[0]
    assert rule.matches(normalize("HP ZBook Firefly 14 G10 i7 32GB")) and rule.matches(normalize("HP ZBook Firefly 14 inch G10"))
    for other in ["HP ZBook Power 15 G10 - Core i7 / 32GB", "HP ZBook FireFly 16 G10 - Core i7-1365", "HP ZBook Fury 16 G10 - i9",
                  "HP ZBook Firefly 14 G9"]:
        assert not rule.matches(normalize(other)), other


def test_a_battery_is_not_a_drill():
    from scanner.marktplaats import comparable_listings
    p = plan(WatchItem(name="Power tools", keywords=["makita"]), "Accu met lader Makita, 12V 1.9Ah")
    assert p.searches == ["makita accu 12v"]

    def li(title, euros):
        return {"title": title, "priceInfo": {"priceType": "FIXED", "priceCents": euros * 100}, "itemId": title}

    found = comparable_listings([li("Accuhouder voor Makita LXT", 4), li("Worx Accu Converter Makita 18V", 13),
                                 li("Makita Accu Adapter voor Einhell 18V", 14),
                                 li("Makita 60120 Accuboormachine 12V met Koffer (zonder accu)", 15),
                                 li("Makita 6271D 12V Accu Boor-/Schroefmachine met Oplader", 20),
                                 li("Makita 1220 12V accu, 123accu huismerk", 20),
                                 li("Makita BL1815N 18V LXT accu", 25),
                                 li("Makita accu 12V 1.9Ah Ni-MH", 18), li("Originele Makita 12 volt accu 1220", 22)],
                                p.rules[0], [])
    assert [f["price"] for f in found] == [18, 22]


def test_listing_titles_are_unescaped_and_cases_left_out():
    from scanner.marktplaats import comparable_listings
    rule = plan(WatchItem(name="iPad", keywords=["ipad"]), "iPad Pro 10,5 inch").rules[0]
    found = comparable_listings([
        {"title": "Incipio Faraday iPad Pro 10.5&quot; 2017 - Black", "priceInfo": {"priceType": "FIXED", "priceCents": 3500}},
        {"title": "Apple iPad Pro 10.5&quot; 64GB", "priceInfo": {"priceType": "FIXED", "priceCents": 18000}}], rule, [])
    assert [f["title"] for f in found] == ['Apple iPad Pro 10.5" 64GB']
