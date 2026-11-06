import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import FakeHttp
from test_main import FakeTelegram

from scanner import deals, private_repo
from scanner.http import BlockedError

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)  # 10:00 in Amsterdam


def listing(item_id, title, cents, date="Vandaag", ptype="FIXED", priority="NONE", traits=None, website=False,
            city="Utrecht", meters=-1000):
    return {"itemId": item_id, "title": title, "priceInfo": {"priceCents": cents, "priceType": ptype},
            "vipUrl": f"/v/x/{item_id}-x", "date": date, "priorityProduct": priority,
            "traits": traits or ["PACKAGE_FREE"], "imageUrls": ["//images.marktplaats.com/1.jpg"],
            "sellerInformation": {"showWebsiteUrl": website, "sellerName": "someone"},
            "location": {"cityName": city, "distanceMeters": meters, "abroad": False}}


NEW = [
    listing("m1", "Makita DHR243 combihamer", 9000, meters=20000),                     # bargain: others ask ~€250
    listing("m2", "Makita DHR243 boorhamer zo goed als nieuw", 24000),   # normal price
    listing("m3", "Makita DHR243 topdeal", 8000, priority="DAGTOPPER"),  # paid placement: a shop
    listing("m4", "Makita DHR243 nieuw", 8000, website=True),            # shop with a website
    listing("m5", "Makita DHR243 defect", 3000),                         # damaged
    listing("m6", "Makita DHR243", 7000, date="3 nov 26"),               # older: not new on a first search
    listing("m7", "Makita DHR243", 0, ptype="FAST_BID"),                 # no price
]
COMPARABLES = [listing(f"c{i}", f"Makita DHR243 combihamer {c}", cents, date="2 nov 26")
               for i, (c, cents) in enumerate([("a", 24000), ("b", 25000), ("c", 26000), ("d", 23000), ("e", 27000)])]


@pytest.fixture
def root(tmp_path, monkeypatch):
    for name in ("config.yml", "watchlist.yml"):
        shutil.copy(ROOT / name, tmp_path / name)
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("INVENTORY_KEY", "test-key")
    for var in ("HOME_ADDRESS", "HOME_ADDRESS_2", "TELEGRAM_BOT_TOKEN", "PRIVATE_REPO_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    saved = {}
    monkeypatch.setattr(private_repo, "put_json", lambda path, data, msg, config=None, session=None:
                        saved.__setitem__(path, data) or True)
    monkeypatch.setattr(deals, "searches", lambda items, cfg: ["makita dhr243"])
    return tmp_path, saved


def http_factory(blocked=False):
    def route(method, url, kw):
        if blocked:
            raise BlockedError("403")
        return {"listings": NEW if "sortBy=SORT_INDEX" in url else COMPARABLES}
    return lambda **kw: FakeHttp([(lambda m, u, kw: "lrp/api/search" in u, route),
                                  (lambda m, u, kw: "pdok" in u, {"response": {"docs": [{"postcode": "3511 AB"}]}})])


def test_finds_only_new_cheap_listings_of_private_sellers(root):
    root, saved = root
    tg = FakeTelegram()
    assert deals.run_deals(root, NOW, http_cls=http_factory(), telegram_cls=tg) == 0
    assert len(tg.sent) == 1
    msg = tg.sent[0]
    assert "m1-x" in msg and "asks €90" in msg and "≈26 km" in msg
    for other in ("m2-x", "m3-x", "m4-x", "m5-x", "m6-x", "m7-x"):
        assert other not in msg
    lots = saved["data/marktplaats-deals.json"]["lots"]
    assert [l["key"] for l in lots] == ["mpdeal:m1"]
    assert lots[0]["premium"] == 0 and lots[0]["vat"] == 0 and lots[0]["siteName"] == "Marktplaats (private seller)"
    assert "someone" not in str(lots)  # no seller names saved
    # the memory is encrypted, and a second run doesn't report the same listing again
    assert (root / "data" / "deals.enc").exists()
    assert b"Makita" not in (root / "data" / "deals.enc").read_bytes()
    tg2 = FakeTelegram()
    deals.run_deals(root, NOW, http_cls=http_factory(), telegram_cls=tg2)
    assert tg2.sent == []


def test_stops_when_refused(root):
    root, _ = root
    tg = FakeTelegram()
    assert deals.run_deals(root, NOW, http_cls=http_factory(blocked=True), telegram_cls=tg) == 0
    assert tg.sent == []


def test_sleeps_at_night(root):
    root, _ = root
    tg = FakeTelegram()
    night = datetime(2026, 11, 6, 2, 0, tzinfo=timezone.utc)
    assert deals.run_deals(root, night, http_cls=lambda **kw: pytest.fail("no requests at night"), telegram_cls=tg) == 0


def test_searches_near_home(root, monkeypatch):
    root, _ = root
    monkeypatch.setenv("HOME_ADDRESS", "Domplein 1, Utrecht")
    made = []
    base = http_factory()

    def factory(**kw):
        made.append(base(**kw))
        return made[-1]
    deals.run_deals(root, NOW, http_cls=factory, telegram_cls=FakeTelegram())
    urls = [u for h in made for _, u, _ in h.calls if "SORT_INDEX" in u]
    assert urls and all("postcode=3511AB" in u and "distanceMeters=100000" in u for u in urls)


def test_rotation_takes_the_oldest_searches():
    assert deals.next_searches(["a", "b", "c"], {"a": "2026-11-06T08:00", "b": "2026-11-06T07:00"}, 2) == ["c", "b"]
