"""Apple products, laptops and phones from before 2020 are left out; iPhone and Pixel searches."""
import pytest

from scanner.age import age_filter, device_kind, release_year, too_old
from scanner.identify import phone_plan, plan_for
from scanner.models import Lot, WatchItem
from scanner.util import normalize

LAPTOP = WatchItem.from_dict({"name": "Laptop", "keywords": ["laptop", "thinkpad", "latitude"]})
PHONE = WatchItem.from_dict({"name": "Phone", "keywords": ["iphone", "galaxy", "pixel"]})
POWER = WatchItem.from_dict({"name": "Power tools", "keywords": ["makita"]})


def lot(title, description=""):
    return Lot("hnvi", "1", title, "https://x/1", 10.0, None, description=description)


@pytest.mark.parametrize("title, year", [
    ("Apple MacBook Pro 13 inch 2017", 2017),
    ("MacBook Air (Mid 2015) 8GB", 2015),
    ("MacBook Pro 16 Core i7 9th Gen 32 GB", 2019),
    ("MacBook Air M1 8GB 256GB", 2020),
    ("MacBook Pro 14 M3 Pro", 2023),
    ("Apple MacBook Pro A1708", 2017),
    ("iPad 6th Gen. 32GB", 2018),
    ("Apple iPad Air 5 64GB", 2022),
    ("iPad Pro 10,5 inch", 2017),
    ("iPad Pro 12.9 3rd gen", 2018),
    ("iPhone 11 Pro 64GB", 2019),
    ("Apple iPhone X 256GB", 2017),
    ("iPhone 12 mini", 2020),
    ("iPhone SE 2e generatie", 2020),
    ("Apple Watch Series 5", 2019),
    ("Samsung Galaxy S10e", 2019),
    ("Samsung Galaxy S21+ 5G", 2021),
    ("Samsung Galaxy A50", 2019),
    ("Samsung Galaxy A52s", 2021),
    ("Google Pixel 4a", 2020),
    ("Pixel 3", 2018),
    ("OnePlus 7T", 2019),
    ("Huawei P30 Pro", 2019),
    ("HP EliteBook 840 G5 i5", 2018),
    ("HP ProBook 450 G7", 2020),
    ("HP 250 G7 laptop", 2019),
    ("Lenovo ThinkPad T480s", 2018),
    ("Lenovo ThinkPad X1 Carbon Gen 7", 2019),
    ("Dell Latitude 5490", 2018),
    ("Dell Latitude 7410", 2020),
    ("Dell Latitude E7450", 2016),
    ("Dell XPS 13 9380", 2017),
    ("Laptop i5-8250U 8GB", 2018),
    ("Laptop Intel Core i7 10510U", 2020),
    ("Laptop i5-1135G7", 2021),
    ("Laptop i7 8e generatie", 2018),
    ("Laptop AMD Ryzen 5 3500U", 2019),
    ("Microsoft Surface Pro 7", 2018),
    ("Microsoft Surface Pro 7+", 2021),
    ("Surface Laptop 3", 2019),
])
def test_release_year(title, year):
    assert release_year(title)[0] == year


@pytest.mark.parametrize("title", ["MacBook Pro", "Laptop HP", "iPhone SE 64GB", "iPad Pro 11 inch", "Samsung Galaxy",
                                   "Laptop met Office 2016", "Lenovo ThinkPad T14 Gen 2"])
def test_unknown_age_stays(title):
    year = release_year(title)
    assert year is None or year[0] >= 2020


def test_year_from_the_description():
    assert release_year("Laptop Dell", "Latitude 5490, i5-8350U, 8 GB")[0] == 2018
    assert release_year("Laptop Lenovo ThinkBook", "type: 15 g2 itl") is None  # ThinkBooks started in 2019/2020
    assert release_year("MacBook Pro", "MacBook Pro Late 2013, 8 GB") == (2013, "2013")
    assert release_year("MacBook Pro", "Ophalen op 12-10-2026") is None  # a date is not the model year


def test_too_old_only_for_apple_laptops_and_phones():
    assert too_old(LAPTOP, lot("Lenovo ThinkPad T480"), 2020)
    assert not too_old(LAPTOP, lot("Lenovo ThinkPad T14 Gen 1"), 2020)
    assert too_old(PHONE, lot("Apple iPhone 8 64GB"), 2020)
    assert not too_old(PHONE, lot("Apple iPhone 13 128GB"), 2020)
    assert not too_old(POWER, lot("Makita accuboormachine 2015"), 2020)  # power tools have no cutoff
    monitor = WatchItem.from_dict({"name": "Monitor", "keywords": ["monitor"]})
    assert not too_old(monitor, lot("Laptop Dell Latitude 7400 + monitor Samsung 24inch"), 2020)  # priced as a monitor
    macbook = WatchItem.from_dict({"name": "MacBook", "keywords": ["macbook"]})
    assert too_old(macbook, lot("1 x Apple MacBook Pro laptop, type A1990"), 2020)
    assert not too_old(macbook, lot("Apple MacBook Pro 16, Apple M1 Max, 32 GB RAM"), 2020)
    assert device_kind("Partij smartphones") == "phone" and device_kind("Makita DHP482") is None
    assert age_filter({}) == 2020 and age_filter({"age_filter": {"enabled": False}}) is None
    assert age_filter({"age_filter": {"min_year": 2021}}) == 2021


def test_phone_plan_tells_variants_apart():
    plan = plan_for(PHONE, lot("Apple iPhone 13 Pro 128GB"))
    assert plan.exact and plan.searches == ["iphone 13 pro 128gb", "iphone 13 pro"]
    ok = [r.matches(normalize(t)) for r in plan.rules for t in ("iPhone 13 Pro 128GB zgan",)]
    assert ok == [True, True]
    for other in ("iPhone 13 Pro Max 128GB", "iPhone 13 128GB", "iPhone 13 Pro scherm", "iPhone 131"):
        assert not any(r.matches(normalize(other)) for r in plan.rules), other
    assert not plan.rules[0].matches(normalize("iPhone 13 Pro 256GB")) and plan.rules[1].matches(normalize("iPhone 13 Pro 256GB"))
    se = plan_for(PHONE, lot("iPhone SE 2020 64GB"))
    assert se.exact and se.rules[-1].matches(normalize("iPhone SE 2e generatie")) and \
        not se.rules[-1].matches(normalize("iPhone SE 2022"))
    assert not phone_plan("iPhone SE").exact
    pixel = phone_plan("Google Pixel 6")
    assert pixel.rules[0].matches("google pixel 6 128gb") and not pixel.rules[0].matches("google pixel 6a")
    assert phone_plan("Samsung Galaxy S21") is None  # Galaxy models have a type number: the usual exact search
