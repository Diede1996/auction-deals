"""Vlavem, BellAuction, Veilingwinnaar, Inventarisveilingen and Nedveiling: markup and JSON as seen on the
live sites on 3 Oct 2026 (shortened)."""
from datetime import datetime, timezone

import yaml
from pathlib import Path

from conftest import FakeHttp, url_has
from scanner.matching import bankruptcy_matcher
from scanner.sites import bellauction, inventarisveilingen, nedveiling, proveiling, veilingwinnaar, vlavem
from scanner.sites.base import SiteContext
from scanner.util import AMS, parse_numeric_datetime

NOW = datetime(2026, 10, 3, 4, 20, tzinfo=timezone.utc)
KEYWORDS = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yml").read_text())["auction_keywords"]
IS_B = bankruptcy_matcher(KEYWORDS)


def ctx(http, **kw):
    return SiteContext(http=http, now=NOW, is_bankruptcy=IS_B, **kw)


def test_numeric_dates():
    assert parse_numeric_datetime("07-10-2026 13:00:00") == datetime(2026, 10, 7, 13, 0, tzinfo=AMS)
    assert parse_numeric_datetime("3-10-2026 20:30") == datetime(2026, 10, 3, 20, 30, tzinfo=AMS)
    assert parse_numeric_datetime("9-10-2026") == datetime(2026, 10, 9, 23, 59, tzinfo=AMS)
    assert parse_numeric_datetime("no date") is None


# ---------------------------------------------------------------- Vlavem (ProVeiling software)

VLAVEM_HOME = """<div class="twelve mobile-four columns"> <p> <a class="home-link" id="small-6229" href="/Alle-kavels/6229/Veiling">DIV. NIEUWE DECORATIE, KEUKENTOEBEHOREN, SPEELGOED ENZ. (GRATIS LEVERING) (6229)</a><br> <span class="small">224 kavels | Betreft: Openbare veiling</span> </p> </div>
<a href="https://www.vlavem.com/6229/DIV-NIEUWE-DECORATIE-6229/AuctionGroup.aspx">info</a>
<div class="twelve mobile-four columns"> <p> <a class="home-link" id="small-6235" href="/Alle-kavels/6235/Veiling">STOPZETTINGSVEILING: LUXE HAARDEN, SIERSCHOUWEN, NATUURSTEEN, BLAUWE HARDSTEEN, PROF. GEREEDSCHAP ENZ. (MELSELE) (6235)</a><br> <span class="small">36 kavels | Betreft:
                                    Openbare veiling</span> </p> </div>
<a href="https://www.vlavem.com/6235/STOPZETTINGSVEILING-LUXE-HAARDEN-6235/AuctionGroup.aspx">info</a>
<div class="twelve mobile-four columns"> <p> <a class="home-link" id="small-6234" href="/Alle-kavels/6234/Veiling">PRIVÉ INBOEDEL: WO. ANTIEK, BRONZEN SCULPTUREN, KUNSTWERKEN (MELSELE) (6234)</a><br> <span class="small">71 kavels | Betreft: Openbare veiling</span> </p> </div>
<a href="https://www.vlavem.com/6234/PRIV-INBOEDEL-6234/AuctionGroup.aspx">info</a>"""

VLAVEM_INFO = """<div><h4>Datums</h4><p class="small">Tip! Klik op de datum om hem aan uw agenda toe te voegen.</p>
<p><strong>Start:</strong><br><a href="#">woensdag 16 september 2026</a>vanaf 10:00</p>
<p><strong>Sluiting:</strong><br><a href="#">woensdag 07 oktober 2026</a>vanaf 19:00</p>
<p> <strong>Ophaaldag(en):</strong><br /> <a title="Voeg toe aan uw kalender" href="iCalendar.ashx?agid=6234&cat=ophaal&dag=1">vrijdag 16 oktober 2026<br/>van 17:00 tot 19:00</a><br/><a title="Voeg toe aan uw kalender" href="iCalendar.ashx?agid=6234&cat=ophaal&dag=2"><br/>zaterdag 17 oktober 2026<br/>van 09:00 tot 12:00</a><br/> </p>
<h4>Algemene informatie</h4><p>Aantal kavels: 36</p>
<div class="container-box"> <h1>Locatie</h1> <p> Fortstraat 10<br /> 9120, Melsele </p> <div id="map_canvas"></div></div><h4>Categorie</h4></div>"""


def vlavem_row(lot_id, title, bid, start="6,00", bids=1, end="4 dagen"):
    return f"""<div class="row" id="tr{lot_id}"> <a href="https://www.vlavem.com/x/{lot_id}/detail" itemprop="url"><img src="https://img.vlavem.com/{lot_id}.jpg"></a>
<strong><a class="article-link" href="https://www.vlavem.com/x/{lot_id}/detail"><span class="editorName">{title}</span></a></strong>
<span class="endtime">Kavel sluit: {end}</span>
<p class="bids"> Biedingen: <strong><span id="NumberOfBids">{bids}</span></strong> <br> <span>Startbod: <span>€{start}</span></span></p>
<p class="currentbid"> Huidig bod: €&nbsp;<span id="CurrentBid">{bid}</span></p>
<p class="location"> Locatie: <span><strong>Melsele</strong></span> </p></div>"""


def vlavem_page(rows, next_link=True):
    pager = ("""<a class="otherPages" href="javascript:__doPostBack('ctl00$myCenterContentPanel$ALCItems$DataPagerParcels1$ctl01$ctl01','')">2</a>
<a class="otherPages" href="javascript:__doPostBack('ctl00$myCenterContentPanel$ALCItems$DataPagerParcels1$ctl02$ctl00','')">Volgende</a>"""
             if next_link else "")
    return f"""<form method="post" action="./Veiling" id="aspnetForm">
<input type="hidden" name="__EVENTTARGET" id="__EVENTTARGET" value="" />
<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="vs{len(rows)}" />
{''.join(rows)}{pager}</form>"""


def test_vlavem():
    page1 = vlavem_page([vlavem_row(1046090, "koperen ketel", "7,00"), vlavem_row(1046091, "Makita boorhamer", "0,00", "25,00", 0)])
    page2 = vlavem_page([vlavem_row(1046092, "Festool cirkelzaag", "60,00")], next_link=False)

    def post(m, u, kw):
        assert kw["data"]["__EVENTTARGET"] == "ctl00$myCenterContentPanel$ALCItems$DataPagerParcels1$ctl02$ctl00"
        assert kw["data"]["__VIEWSTATE"] == "vs2"
        return page2

    http = FakeHttp([
        (lambda m, u, kw: u == "https://www.vlavem.com/", VLAVEM_HOME),
        (url_has("AuctionGroup.aspx"), VLAVEM_INFO),
        (url_has("/Alle-kavels/", method="GET"), page1),
        (url_has("/Alle-kavels/", method="POST"), post),
    ])
    lots = {l.lot_id: l for l in vlavem.fetch_lots(ctx(http))}
    # 6235 (stopzetting) and 6234 (inboedel) are read, the new-goods auction 6229 is not; 3 lots each
    assert not any("6229" in u for _, u, _ in http.calls)
    assert set(lots) == {"1046090", "1046091", "1046092"}
    ketel = lots["1046090"]
    assert ketel.site == "vlavem" and ketel.url == "https://www.vlavem.com/x/1046090/detail"
    assert ketel.current_bid == 7.0 and ketel.bids == 1 and lots["1046091"].current_bid == 25.0
    assert ketel.closes_at == datetime(2026, 10, 7, 19, 0, tzinfo=AMS)
    assert ketel.pickup == "Fortstraat 10, 9120, Melsele, België"
    assert ketel.pickup_when == "Fri 16 Oct, 17:00–19:00"
    assert ketel.auction_title.startswith("PRIVÉ INBOEDEL") or ketel.auction_title.startswith("STOPZETTINGSVEILING")
    assert not ketel.auction_title.endswith(")") or "(6234)" not in ketel.auction_title


def test_vlavem_titles_and_delivery():
    auctions = {a.auction_id: a for a in proveiling.parse_home(VLAVEM_HOME, "vlavem", vlavem.BASE)}
    assert auctions["6235"].title.endswith("(MELSELE)")  # the "(6235)" at the end is dropped
    assert auctions["6229"].delivery and not auctions["6235"].delivery
    assert auctions["6235"].url.startswith("https://www.vlavem.com/6235/")


# ---------------------------------------------------------------- BellAuction (JSON)

def bell_auction(aid, title, start, end, location, collection="Dinsdag 13 oktober van 10u tot 12u", comment=None):
    return {"tableId": 4, "id": aid, "docNr": f"VP26{aid}", "description": title, "comment": comment,
            "startTime": start, "endTime": end, "viewingDays": "op afspraak", "collectionDays": collection,
            "productsLocation": location, "auctionFee": None, "auctionDescription": None}


BELL_AUCTIONS = [
    bell_auction(4294, "Stopzetting restaurant Sint-Anna in Brugge", "2026-09-25T09:00:00", "2026-10-06T19:00:00",
                 "Noorderboomgaard, 8000 Koolkerke"),
    bell_auction(4300, "Inboedel biljartcafé Den Argos", "2026-10-02T22:00:00", "2026-10-19T19:30:00",
                 "Den Argos, Antwerpsesteenweg 550, 9040 Gent", "Maandag 26 oktober van 14u tot 16u"),
    bell_auction(4288, "Kantoormeubilair & werkplaatsmateriaal", "2026-09-17T17:00:00", "2026-10-05T19:00:00",
                 "3401 Landen", comment="Deze veiling omvat een volledige uitruiming van kantoor en werkplaats"),
    bell_auction(4289, "Huis- en tuinverlichting", "2026-09-24T09:00:00", "2026-10-08T19:00:00",
                 "Rue de la Station 142/20-22, \r\n7070 Le Roeulx"),
    bell_auction(4301, "Stopzetting schrijnwerkerij", "2026-10-09T19:00:00", "2026-10-21T19:30:00", "Wakken"),  # not started
    bell_auction(4100, "Stopzetting artisanale bakkerij", "2026-06-01T19:00:00", "2026-07-02T19:00:00", "Brugge"),  # ended
]


def bell_lot(lot_id, nr, title, start, highest=None, bids=0, end="2026-10-06T19:00:00", active=True):
    return {"auctionLotNr": nr, "id": lot_id, "code": str(lot_id), "description": title, "endTime": end,
            "isActive": active, "nrOfBids": bids, "startingBid": start, "highestBid": highest,
            "shortDescription": "Recente Italiaanse convectieoven van Vesta (model 0L00464M)",
            "thumbnailAbsoluteFileUrl": f"https://webshopblobstorage.blob.core.windows.net/bellauction/thumbnails/{lot_id}.jpg",
            "webshopUrlDescription": "x"}


def test_bellauction():
    http = FakeHttp([
        (url_has("/auctions/"), BELL_AUCTIONS),
        (url_has("/auctionlots/auction/4294/count"), "2"),
        (url_has("/auctionlots/auction/4300/count"), "0"),
        (url_has("/auctionlots/auction/4288/count"), "1"),
        (url_has("/auctionlots/auction/4294/1/100"), [
            bell_lot(22074, 1, "Recente convectieoven met stoomfunctie (Vesta, aangekocht eind 2024)", 750),
            bell_lot(22075, 2, "Koelwerkbank", 100, 140, 3, active=False)]),
        (url_has("/auctionlots/auction/4288/1/100"), [
            bell_lot(21740, 16, "Uitlaatgassenafzuiging (2 stuks)", 75, 75, 1, "2026-10-05T19:15:00")]),
    ])
    lots = {l.lot_id: l for l in bellauction.fetch_lots(ctx(http))}
    assert set(lots) == {"22074", "21740"}  # inactive lot, verlichting, not started and ended auctions left out
    assert not any("/4289/" in u or "/4301/" in u for _, u, _ in http.calls)
    oven = lots["22074"]
    assert oven.current_bid == 750.0 and oven.bids == 0  # no bids yet: the starting bid
    assert oven.url == "https://www.bellauction.be/auctionlot/22074/recente-convectieoven-met-stoomfunctie-vesta-aangekocht-eind-2024"
    assert oven.closes_at == datetime(2026, 10, 6, 19, 0, tzinfo=AMS)
    assert oven.pickup == "Noorderboomgaard, 8000 Koolkerke, België" and oven.location == "Koolkerke"
    assert oven.pickup_when == "Dinsdag 13 oktober van 10u tot 12u"
    assert oven.description.startswith("Recente Italiaanse convectieoven") and oven.site == "bellauction"
    assert all((kw.get("headers") or {}).get("Origin") == "https://www.bellauction.be" for _, _, kw in http.calls)
    afz = lots["21740"]
    assert afz.current_bid == 75.0 and afz.bids == 1 and afz.pickup == "3401 Landen, België"


def test_bellauction_address():
    assert bellauction._address("Den Argos, Antwerpsesteenweg 550, 9040 Gent") == "Antwerpsesteenweg 550, 9040 Gent, België"
    assert bellauction._address("Rue de la Station 142/20-22, \r\n7070 Le Roeulx") == \
        "Rue de la Station 142/20-22, 7070 Le Roeulx, België"
    assert bellauction._address("Wakken") == "Wakken, België" and bellauction._address(None) is None


# ---------------------------------------------------------------- Veilingwinnaar (Four Auctions software)

def vw_card(aid, title, closes):
    return f"""<div class="auction-card "> <div class="auction-card__body"> <div class="auction-card__title_container pb-0 mb-0">
<h3 class="auction-card__title"><a href="/auctions/{aid}/" tabindex="-1"> {aid}: {title} </a></h3> </div>
<div class="p-0 m-0 pb-1"> Startdatum: 28 september 2026 12:00<br /> Sluitingsdatum: {closes}<br /> </div>
<a href="/auctions/{aid}/" class="auction-card__btn btn btn-primary btn-block"> Bekijk veiling </a> </div> </div>"""


VW_AUCTIONS = "".join([
    vw_card(384, "Veiling horeca inventaris keukenapparatuur Amsterdam", "6 oktober 2026 17:00"),
    vw_card(387, "Veiling bakkerij te Amersfoort wegens bedrijfsbeëindiging", "22 oktober 2026 16:00"),
    vw_card(381, "Stopzetting lunchroom", "29 september 2026 16:00"),  # closed
])


def vw_lot(lot_id, title, bid, count, closes="2026-10-22T16:00:00+02:00"):
    return f"""<div class="lot-card js-card-lot " data-lot-id="{lot_id}"> <div class="lot-card__image "> <a href="/auctions/387/lots/{lot_id}/" tabindex="-1">
<img src="https://fd-cdn.nl/12602-veilingwinnaar-prd/media/cache/{lot_id}.jpg" width="360" height="240" alt="Afbeelding van {title}"> </a> </div>
<div class="lot-card__body"> <div class="lot-card__title_container pb-0 mb-0"> <h3 class="lot-card__title"><a href="/auctions/387/lots/{lot_id}/" tabindex="-1"> {title} </a></h3>
<div class="lot-card__lot_number">{lot_id}</div> </div>
<div class="lot-card__info js-lot-card-info js-websocket-biddable" data-biddable-pk="{lot_id}" data-current-bid="{bid}" data-bid-count="{count}" data-show-lot-final-price="True" data-starting-date="2026-09-28T12:00:00+02:00" data-closing-date="{closes}"> </div>
<a href="/auctions/387/lots/{lot_id}/" class="lot-card__btn btn btn-primary btn-block"> Bekijk kavel </a> </div> </div>"""


def vw_page(description, lots, more=False):
    return f"""<main class="wrapper"><div class="page-auction"> <div class="page-auction__header"><h1>387: x</h1></div>
<div class="page-auction__description"><p>{description}</p></div>
<div class="page-auction__dates"> <dl class="page-auction__dates-list"> <dt>Startdatum:</dt> <dd>12 Okt 2026 12:00</dd>
<dt>Sluitingsdatum:</dt> <dd>22 Okt 2026 16:00</dd> </dl> <dl class="page-auction__dates-list"> <dt>Ophaaldagen:</dt>
<dd> 27 Okt 2026: 10:00-16:00 uur<br/> </dd> </dl> </div>{''.join(lots)}{'<a href="?page=2">2</a>' if more else ''}</div></main>"""


def test_veilingwinnaar():
    cache = {}
    http = FakeHttp([
        (url_has("/auctions/387/?page=2"), vw_page("x", [vw_lot(11902, "Rational SCC 61 combisteamer", "850.00", "4")])),
        (url_has("/auctions/387/"), vw_page("Bakkerij in Amersfoort sluit. Locatie Amersfoort. De biedprijs is excl. 21 % BTW en 18% Veilingkosten.",
                                            [vw_lot(11900, "Kegelopboller Dafrom", "200.00", "0"),
                                             vw_lot(11901, "2 x weegschaal bakkerij", "125.00", "2")], more=True)),
        (url_has("/auctions/384/"), vw_page("In opdracht veilen wij nieuwe- en gebruikte apparatuur. Locatie Amsterdam.", [])),
        (url_has("/auctions/"), VW_AUCTIONS),
    ])
    lots = {l.lot_id: l for l in veilingwinnaar.fetch_lots(ctx(http, cache=cache))}
    assert set(lots) == {"11900", "11901", "11902"}
    oven = lots["11902"]
    assert oven.current_bid == 850.0 and oven.bids == 4 and oven.site == "veilingwinnaar"
    assert oven.url == "https://veilingwinnaar.nl/auctions/387/lots/11902/"
    assert oven.closes_at == datetime(2026, 10, 22, 16, 0, tzinfo=AMS)
    assert oven.auction_title == "Veiling bakkerij te Amersfoort wegens bedrijfsbeëindiging"
    assert oven.pickup == "Amersfoort" and oven.pickup_when == "Tue 27 Oct, 10:00–16:00"
    assert "bankrupt" not in cache


def test_veilingwinnaar_announced_auction_without_lots():
    page = vw_page("Stopzetting restaurant grillroom afhaalInformatie volgt zsm", [])
    http = FakeHttp([(url_has("/auctions/388/"), page),
                     (url_has("/auctions/"), vw_card(388, "Stopzetting restaurant grillroom afhaal", "19 oktober 2026 16:00"))])
    assert veilingwinnaar.fetch_lots(ctx(http)) == []  # lots appear when the auction starts


# ---------------------------------------------------------------- Inventarisveilingen

def iv_row(aid, title, description, closes):
    return f"""<tr class="even"> <td class="image"> <a href="https://www.inventarisveilingen.nl/veiling/index/view/id/{aid}/"><img src="x.jpg"></a> </td>
<td class="description"> <h3><a href="https://www.inventarisveilingen.nl/veiling/index/view/id/{aid}/">{title}</a></h3>
<div class="description"> {description} </div> </td> <td class="date a-center"> {closes} <div class="city">Nieuwegein</div> <div class="country">Nederland</div> </td> </tr>"""


IV_LIST = "<table>" + "".join([
    iv_row(88, "088: Style fitnessapparatuur", "Nu op de veiling een partij Style fitnessapparatuur online.", "06-10-2026 13:00:00"),
    iv_row(128, "127: Cisco it apparatuur", "Uit een faillissement bieden wij o.a. een partij nieuwe Cisco &amp; HP apparatuur online.", "07-10-2026 13:00:00"),
]) + "</table>"

IV_AUCTION = """<div class="col-main" id="main"> <div class='page-title auctions-title'> <h1>127: Cisco it apparatuur</h1> </div>
<div class='auction-view'> <div class='description'> <div style='float: right; width: 475px;'> Uit een faillissement bieden wij o.a. een partij nieuwe Cisco & HP apparatuur online.
<div style='margin-top: 10px'> <span class='day-title'>Kijkdag</span> <span class='day-text'>-</span> </div>
<div style='margin-top: 1px'> <span class='day-title'>Afhaaldag</span> <span class='day-text'>09-10-2026 tussen 13:00 en 13:00 uur</span> </div>
<h4 class='pickup-title'>Afhaaladres</h4> <ul> <li>Cisco apparatuur</li> <li> Sluyterslaan 209 </li> <li>3434BD Nieuwegein</li> <li>Nederland</li> </ul> </div> </div>
<table class='category-table auction-table'> <tr> <th>Categorie</th> <th>Aantal kavels</th> </tr>
<tr> <td class='description'> <a href="https://www.inventarisveilingen.nl/veiling/index/productlist/id/128/catid/39/"> Bouwmaterialen </a> </td> <td class='count a-center'>0</td> </tr>
<tr> <td class='description'> <a href="https://www.inventarisveilingen.nl/veiling/index/productlist/id/128/catid/48/"> ICT </a> </td> <td class='count a-center'>13</td> </tr>
</table></div>"""


def iv_lot(prod, title, bid, description="Een nieuwe HP batterij. model: SKO-Batt 3C 56WH. <a href='x'>klik hier voor meer info</a>"):
    return f"""<tr> <td class='id a-center'>13</td> <td class='image'><img src="https://www.inventarisveilingen.nl/media/{prod}.jpg" alt="" /></td>
<td class='product'> <h3><a href="https://www.inventarisveilingen.nl/veiling/index/productview/id/128/prodid/{prod}/">{title}</a></h3>
<div class='description'><p>{description}</p></div> </td> <td class='bid a-center'> €&nbsp;{bid} </td> <td class='date a-center'> 07-10-2026 13:00:00 </td> </tr>"""


def iv_page(rows, next_url=None):
    pager = (f"""<div class="pager"><ol><li><a title="Volgende" href="{next_url}" class="next"><img alt="Volgende"></a></li></ol></div>"""
             if next_url else "")
    return f"""<div class="hot-auctions"><a href="https://www.inventarisveilingen.nl/veiling/index/productview/id/88/prodid/2302/">hot</a></div>
{pager}<table class='auction-table'>{''.join(rows)}</table>"""


def test_inventarisveilingen():
    p2 = "https://www.inventarisveilingen.nl/veiling/index/productlist/id/128/catid/48/p/2/"
    http = FakeHttp([
        (url_has("/catid/48/p/2/"), iv_page([iv_lot(3440, "Cisco ASA 5516 X Security appliance", "150,00")])),
        (url_has("/catid/48/"), iv_page([iv_lot(3313, "Nieuwe HP laptop batterij", "0,00")], p2)),
        (url_has("/view/id/128/"), IV_AUCTION),
        (url_has("/veiling/"), IV_LIST),
    ])
    lots = {l.lot_id: l for l in inventarisveilingen.fetch_lots(ctx(http))}
    assert set(lots) == {"3313", "3440"}  # fitness auction (88) skipped, empty group 39 not opened
    assert not any("/id/88/" in u or "/catid/39/" in u for _, u, _ in http.calls)
    bat = lots["3313"]
    assert bat.current_bid is None  # €0,00: no bids yet
    assert bat.description == "Een nieuwe HP batterij. model: SKO-Batt 3C 56WH."
    assert bat.url == "https://www.inventarisveilingen.nl/veiling/index/productview/id/128/prodid/3313/"
    assert bat.pickup == "Sluyterslaan 209, 3434BD Nieuwegein" and bat.pickup_when == "Fri 9 Oct, 13:00–13:00"
    assert bat.closes_at == datetime(2026, 10, 7, 13, 0, tzinfo=AMS) and bat.auction_title == "Cisco it apparatuur"
    assert lots["3440"].current_bid == 150.0


# ---------------------------------------------------------------- Nedveiling

def ned_card(aid, title, lots, place, closes, pickup):
    return f"""<a href="https://www.nedveiling.nl/{aid},project_id,categories" alt="{title}" class="text-decoration"> <div class="auction-card card-front">
<h4 class="text-blue">{title}</h4> <ul><li><i class="fas fa-bars" title="Aantal kavels"></i> {lots} kavels</li>
<li><i class="fas fa-map-marker-alt" title="Land"></i> {place}</li><li><i class="far fa-clock" title="Eindtijd"></i> {closes}</li>
<li><i class="fas fa-warehouse" title="Ophaaldag"></i> {pickup}</li></ul></div></a>"""


NED_HOME = "".join([
    ned_card(4194, "Diverse Veiling Hulten 8J", 112, "Hulten - Nederland", "03-10-2026 20:30", "9-10-2026 9.00 - 12.00 uur"),
    ned_card(4201, "Faillissementsveiling gereedschap Veldhoven", 80, "Veldhoven - Nederland", "08-10-2026 21:00", "14-10-2026 10.00 - 13.00 uur"),
    ned_card(4202, "Diverse veiling Vroomshoop", 150, "Vroomshoop - Nederland", "09-10-2026 21:00", "16-10-2026 9.00 - 14.00 uur"),
    ned_card(4190, "Diverse Veiling Hulten 8H (faillissement)", 23, "Hulten - Nederland", "29-09-2026 20:30", "2-10-2026 9.00 - 12.00 uur"),
])


def ned_info(text):
    return f"""<div class="auction-card auction-description"> <h3>Veiling informatie</h3> <ul> <li><b>Veiling: </b>x</li>
<li><b>Locatie: </b>Veldhoven</li></ul> <p><div>{text}</div></p> </div>"""


def ned_lot(lot_id, title, bid, bids, left):
    return f"""<a href="https://www.nedveiling.nl/{title.replace(' ', '-')},name,{lot_id},auction_id,auction_details" class="text-decoration"><div class="row p-2 card border rounded">
<div class="col-lg-2 mt-1"><img class="img-fluid" src="thumbnail.php?pic=auction_images/4201/{lot_id}.jpg&amp;w=500&amp;sq=Y" alt="{title}"></div>
<div class="col-lg-6 mt-1"><h3 class="text-blue">{title}</h3><p class="description-text">{title}...</p></div>
<div class="col-lg-4"><ul class="auction-information-list"><li><p><b>Biedingen:</b> {bids}</p></li><li><p><b>Huidig bod: </b>€{bid}</p></li>
<li><p><b>Resterende tijd: </b>{left}</p></li></ul></div></div></a>"""


NED_LOT_PAGE = """<div class="viewday"> <b>Bezichtiging:<br /></b> Heiberg 1<br /> 5504PA Veldhoven<br /> Nederland<br /> 13-10-2026 9.00 - 12.00 uur </div>
<div class="pickupday"> <b>Afhalen:<br /></b> Heiberg 1<br /> 5504PA Veldhoven<br /> Nederland<br /> 14-10-2026 10.00 - 13.00 uur </div>"""


def test_nedveiling():
    cache = {}
    http = FakeHttp([
        (url_has("categories.php", "project_id=4201", "start=0&"), "".join(
            [ned_lot(1180001, "Makita accuboormachine", "45,00", 3, "5d 16h 40m 12s"),
             ned_lot(1180002, "Bosch GBH 2-26 boorhamer", "5,00", 0, "5d 16h 41m 12s")])),
        (url_has("categories.php", "project_id=4201", "start=2&"), ned_lot(1180003, "Hilti TE 30", "120,00", 6, "5d 16h 42m")),
        (url_has("categories.php", "project_id=4201", "start=3&"), ""),
        (url_has(",auction_id,auction_details"), NED_LOT_PAGE),
        (url_has("/4201,project_id,categories"), ned_info("Uit faillissement van een bouwbedrijf.")),
        (url_has("/4194,project_id,categories"), ned_info("In deze veiling bieden wij een breed assortiment restanten aan.")),
        (url_has("/4202,project_id,categories"), ned_info("Restanten en diverse goederen.")),
        (lambda m, u, kw: u == "https://www.nedveiling.nl/", NED_HOME),
    ])
    lots = {l.lot_id: l for l in nedveiling.fetch_lots(ctx(http, cache=cache))}
    assert set(lots) == {"1180001", "1180002", "1180003"}
    assert cache["bankrupt"] == {"4194": False, "4201": True, "4202": False}  # 4190 has closed
    makita = lots["1180001"]
    assert makita.current_bid == 45.0 and makita.bids == 3 and makita.site == "nedveiling"
    assert makita.url == "https://www.nedveiling.nl/Makita-accuboormachine,name,1180001,auction_id,auction_details"
    assert makita.image == "https://www.nedveiling.nl/thumbnail.php?pic=auction_images/4201/1180001.jpg&w=500&sq=Y"
    assert makita.pickup == "Heiberg 1, 5504PA Veldhoven" and lots["1180003"].pickup == "Heiberg 1, 5504PA Veldhoven"
    assert makita.pickup_when == "Wed 14 Oct, 10:00–13:00"
    assert abs((makita.closes_at - NOW).total_seconds() - (5 * 86400 + 16 * 3600 + 40 * 60)) < 1
    assert sum(",auction_id,auction_details" in u and "categories.php" not in u for _, u, _ in http.calls) == 1


def test_nedveiling_pickup_in_belgium():
    page = NED_LOT_PAGE.replace("5504PA Veldhoven", "3930 Hamont").replace("Nederland", "Belgie")
    assert nedveiling.parse_pickup_address(page) == "Heiberg 1, 3930 Hamont, België"


# ---------------------------------------------------------------- IT auctions (extra_auctions)

def test_it_auctions_by_name_only():
    from scanner.matching import auction_filter
    config = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yml").read_text())
    wanted = auction_filter(config)
    for name in ["IT en multimedia: servers, laptops, netwerk", "Computers, Tablets, Desktops & Monitoren",
                 "Ex-lease laptops en monitoren", "Apple MacBooks partij", "Cisco it apparatuur"]:
        assert wanted(name), name
    for name in ["Uitverkoop tuinmeubelen", "Vitamine tabletten partij", "Emob: Sofa's, tafels & stoelen, tuin"]:
        assert not wanted(name), name
    # IT words in a description don't count (boilerplate like "bieden via uw computer"); bankruptcy words do
    assert not wanted("Diverse veiling Hulten", "Bieden kan via uw computer of smartphone")
    assert wanted("Diverse veiling Hulten", "Uit faillissement van een bouwbedrijf")
    off = auction_filter({**config, "extra_auctions": {**config["extra_auctions"], "enabled": False}})
    assert not off("Computers, Tablets, Desktops & Monitoren") and off("Faillissement Spectrik BV")


def test_auction_decisions_forgotten_when_filter_words_change(monkeypatch):
    from scanner import scan

    def fetch(ctx):
        ctx.cache.setdefault("bankrupt", {})["1"] = ctx.is_bankruptcy("Computers en laptops")
        return []

    monkeypatch.setattr(scan, "SITES", {"x": fetch})
    state = {"site_cache": {"x": {"bankrupt": {"1": False, "2": False}}}}
    config = {"auction_keywords": ["faillissement"], "extra_auctions": {"words": ["computer"]}}
    scan.scan_sites(config, [], state, lambda: FakeHttp([]), NOW)
    assert state["site_cache"]["x"]["bankrupt"] == {"1": True}  # old decisions dropped, re-checked
    state["site_cache"]["x"]["bankrupt"]["2"] = False
    scan.scan_sites(config, [], state, lambda: FakeHttp([]), NOW)
    assert state["site_cache"]["x"]["bankrupt"] == {"1": True, "2": False}  # same words: kept
