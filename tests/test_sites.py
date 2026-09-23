from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from conftest import FakeHttp, url_has
from fixtures import (HNVI_HOME, OVM_AUCTIONS, OVM_LOT, PJB_AUCTIONS, PV_HOME, PV_INFO, TW_AUCTION_LIST,
                      TW_SEARCH_LOTS, hnvi_item, next_page, pjb_lot, pv_page, pv_row)
from scanner.matching import bankruptcy_matcher
from scanner.sites import hnvi, onlineveilingmeester, plaatsjebod, proveiling, troostwijk
from scanner.sites.base import SiteContext
from scanner.util import AMS

NOW = datetime(2026, 9, 22, 19, 30, tzinfo=timezone.utc)
IS_B = bankruptcy_matcher(["faillissement", "failliet", "faillite", "curator", "bankrupt", "insolvent"])


def ctx(http, **kw):
    return SiteContext(http=http, now=NOW, is_bankruptcy=IS_B, **kw)


def test_troostwijk():
    def search(m, u, kw):
        q = parse_qs(urlparse(u).query)
        assert q["searchTerm"] == ["thinkpad"] and q["countries"] == ["nl"]
        return next_page({"lots": TW_SEARCH_LOTS, "searchTotalSize": 2, "pageSize": 48})

    http = FakeHttp([
        (url_has("/nl/auctions?"), next_page({"listData": TW_AUCTION_LIST, "totalSize": 3, "pageSize": 48})),
        (url_has("/nl/search?"), search),
    ])
    lots = troostwijk.fetch_lots(ctx(http, search_terms=["thinkpad"], settings={"countries": ["nl"]}))
    # the X1 belongs to a non-bankruptcy auction and must be dropped
    assert len(lots) == 1
    lot = lots[0]
    assert lot.lot_id == "A1-50096-100091"
    assert lot.current_bid == 100.0
    assert lot.auction_title == "Faillissement fulfilment magazijn"
    assert lot.url == "https://www.troostwijkauctions.com/nl/l/lenovo-thinkpad-t580-i5-8gb-256gb-15-3%22-laptop-A1-50096-100091"
    assert lot.closes_at == datetime.fromtimestamp(1790772000, tz=timezone.utc)
    assert lot.location == "Purmerend"


def test_troostwijk_display_id():
    assert troostwijk.auction_display_id("A1-50096-100091") == "A1-50096"


def test_proveiling_home_and_lots():
    auctions = {a.auction_id: a for a in proveiling.parse_home(PV_HOME)}
    assert set(auctions) == {"15732", "15724", "15725"}
    assert auctions["15725"].kind == "Executie veiling"
    assert auctions["15732"].kind == "Executie veiling"  # label found on a later duplicate link
    assert auctions["15725"].url.endswith("/AuctionGroup.aspx")
    assert proveiling.parse_closing(PV_INFO) == datetime(2026, 9, 28, 20, 0, tzinfo=AMS)

    pages = {
        "1": pv_page([pv_row(3285247, "Royal Pelletkachel ILENA AIR 60", "110,00", "50,00", "6 dagen", 10),
                      pv_row(3285237, "PROBAT koffiebrander", "0,00", "20,00", "morgen vanaf 20:25", 0)]),
        "2": pv_page([pv_row(3270500, "Magnetron", "12,00", "10,00", "6 dagen")]),
        "3": pv_page([pv_row(3270501, "Gram horeca koelkast", "1.110,00", "100,00", "6 dagen")]),
    }

    def post(m, u, kw):
        data = kw["data"]
        assert data["__EVENTTARGET"] == proveiling.PAGER_TARGET
        assert data["__VIEWSTATE"] == "abc123"
        return pages[data["__EVENTARGUMENT"]]

    http = FakeHttp([
        (lambda m, u, kw: m == "GET" and u == "https://www.proveiling.nl/", PV_HOME),
        (url_has("AuctionGroup.aspx"), PV_INFO),
        (url_has("/Alle-kavels/", method="GET"), pages["1"]),
        (url_has("/Alle-kavels/", method="POST"), post),
    ])
    lots = proveiling.fetch_lots(ctx(http))
    # two bankruptcy auctions x 4 lots each (the fake serves the same pages for both)
    assert len(lots) == 8
    by_id = {l.lot_id: l for l in lots}
    pellet = by_id["3285247"]
    assert pellet.title == "Royal Pelletkachel ILENA AIR 60"
    assert pellet.current_bid == 110.0 and pellet.bids == 10
    assert pellet.closes_at == datetime(2026, 9, 28, 20, 0, tzinfo=AMS)  # "6 dagen" -> auction closing time
    assert pellet.image.startswith("https://img.proveiling.nl/")
    assert pellet.location == "Emmeloord"
    probat = by_id["3285237"]
    assert probat.current_bid == 20.0  # no bids yet -> starting bid
    assert probat.closes_at == datetime(2026, 9, 23, 20, 25, tzinfo=AMS)
    assert by_id["3270501"].current_bid == 1110.0
    assert not any("15724" in u for _, u, _ in http.calls)  # non-bankruptcy auction skipped


def test_hnvi():
    items = [hnvi_item(196542, "antieke-wereldbol-met-kompas", "Antieke wereldbol met kompas. Doorsnede 43 cm. Hoogte ca. 80 cm", "60"),
             hnvi_item(196556, "transporter-aanhanger-hapert", "Transporter aanhanger HAPERT Indigo", "1.250")]
    page1 = "".join(items) + '<a href="/online-veiling/x/1880/page:2">2</a>'
    page2 = hnvi_item(196600, "laptop-hp", "Laptop HP EliteBook 840 G5", "85")
    http = FakeHttp([
        (lambda m, u, kw: u == "https://www.hnvi.nl/", HNVI_HOME),
        (url_has("/page:2"), page2),
        (url_has("/page:"), ""),
        (url_has("/online-veiling/"), page1),
    ])
    auctions = hnvi.parse_home(HNVI_HOME)
    assert [a.auction_id for a in auctions] == ["1877", "1881", "1880"]
    lots = hnvi.fetch_lots(ctx(http))
    assert not any("1881" in u for _, u, _ in http.calls)  # bedrijfsbeëindiging is not a bankruptcy
    globe = next(l for l in lots if l.lot_id == "196542")
    assert globe.title == "Antieke wereldbol met kompas. Doorsnede 43 cm. Hoogte ca. 80 cm"
    assert globe.current_bid == 60.0
    assert globe.closes_at == datetime(2026, 10, 5, 19, 30, tzinfo=AMS)
    assert globe.url == "https://www.hnvi.nl/veiling-kavel/antieke-wereldbol-met-kompas/196542"
    assert next(l for l in lots if l.lot_id == "196556").current_bid == 1250.0
    assert any(l.lot_id == "196600" for l in lots)


def test_plaatsjebod():
    slug = "faillissementsveiling-tandartsenpraktijk-nieuwe-steen-bv-te-hoorn"
    http = FakeHttp([
        (url_has("/nl/auctions/"), PJB_AUCTIONS),
        (url_has(f"/nl/lots/auction/{slug}?pagination=100&page=1"),
         pjb_lot(57971, "2-x-tom-vac-stoelen-van-vitra", "2 x Tom Vac stoelen van Vitra", "110")
         + pjb_lot(57972, "apple-imac", "Apple iMac 24 inch M1", "1.050")),
    ])
    lots = plaatsjebod.fetch_lots(ctx(http))
    # car audio auction isn't a bankruptcy, Prefab auction already closed
    assert [l.lot_id for l in lots] == ["57971", "57972"]
    chairs = lots[0]
    assert chairs.title == "2 x Tom Vac stoelen van Vitra"
    assert chairs.current_bid == 110.0 and chairs.bids == 8
    assert chairs.closes_at == datetime(2026, 9, 28, 20, 5, tzinfo=AMS)
    assert chairs.url == "https://www.plaatsjebod.nl/nl/lots/2-x-tom-vac-stoelen-van-vitra"
    assert chairs.image.startswith("https://www.plaatsjebod.nl/media/")
    assert lots[1].current_bid == 1050.0


def test_onlineveilingmeester():
    no_bid = dict(OVM_LOT, id=1898105, hoogsteBod=0, openingsBod=10, volgNummer="7", naam="Fauteuil")
    http = FakeHttp([
        (url_has("/rest/nl/veilingen?"), OVM_AUCTIONS),
        (url_has("/kavels?page=1"), {"content": [OVM_LOT, no_bid], "last": True}),
    ])
    lots = onlineveilingmeester.fetch_lots(ctx(http))
    assert len(lots) == 2
    assert all("/veilingen/9527/kavels" in u for _, u, _ in http.calls[1:])  # only the FAILLISEMENT auction
    bank = lots[0]
    assert bank.title == "3-zits Design bank, Dutch New Design, Lefkas"
    assert bank.current_bid == 76.0
    assert bank.url == "https://onlineveilingmeester.nl/nl/veilingen/9527/kavels/6"
    assert bank.image == "https://onlineveilingmeester.nl/images/original/2026-09-08/c13bb75a-3718-4020-ba10-c2de0a6222e1/74084.jpg"
    assert bank.closes_at == datetime(2026, 9, 30, 17, 46, 24, tzinfo=timezone.utc)
    assert lots[1].current_bid == 10.0
