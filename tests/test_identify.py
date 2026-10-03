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
