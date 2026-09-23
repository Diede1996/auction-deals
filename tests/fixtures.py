"""Page snippets captured from the real sites on 22 Sep 2026 (whitespace collapsed, trimmed)."""
import json

# ---------------------------------------------------------------- Troostwijk
TW_AUCTION_LIST = [
    {"id": "5123f26c", "displayId": "A1-49861",
     "name": "Audiovisueel- en horeca materiaal wegens faillissement Virtulan bv",
     "urlSlug": "audiovisueel-en-horeca-materiaal-wegens-faillissement-virtulan-bv-A1-49861",
     "biddingStatus": "BIDDING_OPEN", "endDate": 1791205200, "minEndDate": 1791205200, "lotCount": 70},
    {"id": "9462b240", "displayId": "A1-50096", "name": "Faillissement fulfilment magazijn",
     "urlSlug": "faillissement-fulfilment-magazijn-A1-50096", "biddingStatus": "BIDDING_OPEN",
     "endDate": 1790772000, "minEndDate": 1790772000, "lotCount": 400},
    {"id": "028dbacb", "displayId": "A1-49452", "name": "LED binnen en buitenverlichting",
     "urlSlug": "led-binnen-en-buitenverlichting-A1-49452", "biddingStatus": "BIDDING_OPEN",
     "endDate": 1790096400, "minEndDate": 1790096400, "lotCount": 467},
]

TW_SEARCH_LOTS = [
    {"id": "65ba6ec3", "displayId": "A1-50096-100091",
     "title": "Lenovo - ThinkPad T580 - I5 / 8GB / 256GB / 15,3\" Laptop",
     "urlSlug": "lenovo-thinkpad-t580-i5-8gb-256gb-15-3%22-laptop-A1-50096-100091",
     "startDate": 1789653600, "endDate": 1790772000, "bidsCount": 2,
     "currentBidAmount": {"currency": "EUR", "cents": 10000}, "biddingStatus": "BIDDING_OPEN",
     "location": {"city": "PURMEREND", "countryCode": "nl"},
     "image": {"url": "https://media.tbauctions.com/image-media/cfc0ec28/file"}},
    {"id": "aaaa", "displayId": "A1-49452-77", "title": "Lenovo ThinkPad X1 Carbon",
     "urlSlug": "lenovo-thinkpad-x1-A1-49452-77", "endDate": 1790096400, "bidsCount": 0,
     "currentBidAmount": {"currency": "EUR", "cents": 5000}, "biddingStatus": "BIDDING_OPEN",
     "location": {"city": "Puurs", "countryCode": "be"}, "image": {"url": "x"}},
]


def next_page(page_props: dict) -> str:
    data = {"props": {"pageProps": page_props}, "page": "/x", "buildId": "b"}
    return ('<html><body><div id="__next"></div><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(data) + "</script></body></html>")


# ---------------------------------------------------------------- ProVeiling
PV_HOME = """
<div><p> <a class="home-link" id="small-15732" href="/Alle-kavels/15732/Veiling">Faillissementsveiling voorraad en inventaris “Fundering Expertise Nederland B.V.” te Waddinxveen</a><br> </p></div>
<a href="https://www.proveiling.nl/15732/Faillissementsveiling-voorraad-en-inventaris-Fundering-Expertise-Nederland-BV-te-Waddinxveen/AuctionGroup.aspx">Meer informatie...</a>
<div><p> <a class="home-link" id="small-15724" href="/Alle-kavels/15724/Veiling">Gratis verzendveiling binnen NL: Tuinmeubilair, dierbenodigdheden &amp; gereedschap</a><br> <span class="small">Betreft: Openbare veiling</span> </p></div>
<div><p> <a class="home-link" id="15724" href="/Alle-kavels/15724/Veiling">Gratis verzendveiling binnen NL: Tuinmeubilair, dierbenodigdheden &amp; gereedschap</a></p></div>
<a href="https://www.proveiling.nl/15724/Gratis-verzendveiling-binnen-NL-Tuinmeubilair-dierbenodigdheden---gereedschap/AuctionGroup.aspx">Veiling informatie</a>
<div><p> <a class="home-link" id="small-15725" href="/Alle-kavels/15725/Veiling">Faillissementsveiling horeca-apparatuur &amp; meubilair - Opgeslagen te Emmeloord</a><br> <span class="small">Betreft:
                                        Executie veiling</span> </p></div>
<a href="https://www.proveiling.nl/15725/Faillissementsveiling-horeca-apparatuur---meubilair---Opgeslagen-te-Emmeloord/AuctionGroup.aspx">Veiling informatie</a>
<div><p> <a class="home-link" id="small-15732" href="/Alle-kavels/15732/Veiling">Faillissementsveiling voorraad en inventaris “Fundering Expertise Nederland B.V.” te Waddinxveen</a><br> <span class="small">Betreft: Executie veiling</span> </p></div>
"""

PV_INFO = """<div>Datums Tip! Klik op de datum om hem aan uw agenda toe te voegen. Start: zaterdag 12 september 2026vanaf 17:00
Sluiting: maandag 28 september 2026vanaf 20:00 Kijkdag(en): maandag 28 september 2026van 10:00 tot 11:00 Type veiling: Executie veiling</div>"""


def pv_row(lot_id, title, bid, start, end, bids=1):
    return f"""<div class="row" data-href="https://www.proveiling.nl/x/{lot_id}/detail" id="tr{lot_id}" data-isbidder="False" itemscope="" itemtype="http://schema.org/Product"> <div class="three columns text-center hide-for-small"> <a id="id{lot_id}"></a> <a href="https://www.proveiling.nl/x/{lot_id}/detail" itemprop="url"> <img original="https://img.proveiling.nl/Image.aspx?img=15725|165597|{lot_id}|FirstImage.jpg&amp;x=200&amp;y=200&amp;stretch=1" alt="{title}" class="thumbview-list" src="https://img.proveiling.nl/Image.aspx?img=15725|165597|{lot_id}|FirstImage.jpg&amp;x=200&amp;y=200&amp;stretch=1"> </a> </div> <div class="six mobile-two columns"> <div> <p> <strong><a class="article-link" href="https://www.proveiling.nl/x/{lot_id}/detail"><span id="art_name_{lot_id}" class="editorName" itemprop="name">{title}</span></a></strong> </p> </div> <div> <p> <span class="small lotnr">Kavelnr: 17433-1306926</span><br><span class="small condition">Conditie: Nieuw</span><br> </p><div> <strong><span class="endtime" style="font-size: 14px;">Kavel sluit: {end}</span></strong> </div> </div> </div> <div class="three mobile-two columns text-right"> <p class="bids"> Biedingen: <strong><span id="NumberOfBids" class="alertspan">{bids}</span></strong> <br> <span style="color: #999">Startbod: <span class="alertspan">€{start}</span></span></p> <p class="currentbid"> Huidig bod: <span id="AlertSpan" class=""><span id="AlertIcon" class="icon "></span>€&nbsp;<span id="CurrentBid">{bid}</span></span></p> <p class="location"> Locatie: <span class="alertspan"><strong>Emmeloord</strong></span> </p> </div> </div>"""


def pv_page(rows, pages=3):
    options = "".join(f'<option value="{i}">{i}</option>' for i in range(1, pages + 1))
    return f"""<form method="post" action="./Veiling" id="aspnetForm">
<input type="hidden" name="__EVENTTARGET" id="__EVENTTARGET" value="" />
<input type="hidden" name="__EVENTARGUMENT" id="__EVENTARGUMENT" value="" />
<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="abc123" />
<input type="hidden" name="__VIEWSTATEGENERATOR" id="__VIEWSTATEGENERATOR" value="C2EE9ABB" />
<input type="hidden" name="__EVENTVALIDATION" id="__EVENTVALIDATION" value="xyz" />
<select name="ctl00$myCenterContentPanel$ALCItems$AspNetPager1_input" onchange="__doPostBack('ctl00$myCenterContentPanel$ALCItems$AspNetPager1','')">{options}</select>
{''.join(rows)}
</form>"""


# ---------------------------------------------------------------- HNVI
HNVI_HOME = """<ul>
<li> <a href="/online-veiling/faillissement-spectrik-bv/1877" title="Faillissement Spectrik BV"> <img src="x.jpg" class="thumb ui-default-thumb" alt=""> </a> <div class="ui-auction-data-container"> <h2 class="ui-h3-title"><a href="/online-veiling/faillissement-spectrik-bv/1877">Faillissement Spectrik BV</a></h2> <span class="ui-title-01"><span>Locatie :</span> Eindhoven <span>Einddatum :</span> 23 September 2026</span> <p class="ui-par-01">Online veilig i.o.v. curator mr. A.C.S. Tan van Berg Jeths Advocaten te Eindhoven<br>Met o.a. vibratie isolatie werktafel, spectrum analysers</p> <span id="countdown_1877"></span> </div> </li>
<li> <a href="/online-veiling/bedrijfsbeeindiging-hoveniersbedrijf-valkenswaard/1881"></a> <div class="ui-auction-data-container"> <h2 class="ui-h3-title"><a href="/online-veiling/bedrijfsbeeindiging-hoveniersbedrijf-valkenswaard/1881">Bedrijfsbeëindiging hoveniersbedrijf Valkenswaard</a></h2> <span class="ui-title-01"><span>Locatie :</span> Valkenswaard <span>Einddatum :</span> 30 September 2026</span> <p class="ui-par-01">Online veiling wegens bedrijfsbeëindiging</p> </div> </li>
<li> <a href="/online-veiling/combinatieveiling-div-faillissementen-september-2026/1880"></a> <div class="ui-auction-data-container"> <h2 class="ui-h3-title"><a href="/online-veiling/combinatieveiling-div-faillissementen-september-2026/1880">Combinatieveiling div. faillissementen september 2026</a></h2> <span class="ui-title-01"><span>Locatie :</span> Loon op Zand <span>Einddatum :</span> 5 Oktober 2026</span> <p class="ui-par-01">Online veiling i.o.v. diverse curatoren van diverse faillissementen</p> </div> </li>
</ul>"""


def hnvi_item(lot_id, slug, title, price):
    short = title[:45] + " ..."
    return f"""<div class="ui-box-type1 ui-product-box-list-item"> <a href="/veiling-kavel/{slug}/{lot_id}"> <img src="https://assets.hnvi.nl/assets/xl/{lot_id}.jpg" class="ui-default-thumb" alt=""> </a> <p class="ui-product-box-title ui-product-box-default-text"><a href="/veiling-kavel/{slug}/{lot_id}" title="{title}">{short}</a></p> <p class="ui-product-box-code ui-product-box-default-text"> Kavel: C001</p> <p class="ui-product-box-price ui-product-box-default-text">
        Prijs:
        &euro; {price}</p>
    <p class="ui-product-box-countdown ui-product-box-default-text">Resterend:
        <span id="countdown_{lot_id}"></span>
    </p>
</div>
<script type="text/javascript">
//<![CDATA[
        $(function() {{
            var countdown_{lot_id}s_timer = new Date('Oct 5, 2026 19:30:00 +0200');
            $('#countdown_{lot_id}').countdown({{
                serverSync: function() {{ return new Date('Sep 22, 2026 21:34:03 +0200'); }},
                until: countdown_{lot_id}s_timer, compact: true, format: 'd h m s'
            }});
        }});
//]]>
</script>"""


# ---------------------------------------------------------------- Plaats Je Bod
PJB_AUCTIONS = """<h1>Veilingen</h1>
<div class="row"> <div class="col-sm-3 auction-image"> <a href="/nl/lots/auction/faillissementsveiling-tandartsenpraktijk-nieuwe-steen-bv-te-hoorn"> <div class="lot-count-container"> <span class="auction-lot-count"> 99 kavels</span> </div> <img src="/media/lot/26-371.jpg"> </a> </div> <div class="col-sm-9"> <h3> <a class="name" href="/nl/lots/auction/faillissementsveiling-tandartsenpraktijk-nieuwe-steen-bv-te-hoorn"> Faillissementsveiling Tandartsenpraktijk Nieuwe Steen B.V. te Hoorn </a> </h3> <span class="auction-location"><label>Locatie: </label> Nieuwe Steen 25 te Hoorn</span> <div class="endDate"> <label>Veiling eindigt:</label> 28 sep. 2026 20:00:00 (CEST) </div> <div class="lots"> <label>kavels: </label> 99 </div> <div class="description"> <p>In opdracht van de curator in het faillissement van Tandartsenpraktijk Nieuwe Steen B.V. ...</p> </div> <a class="button view-lots-btn" href="/nl/lots/auction/faillissementsveiling-tandartsenpraktijk-nieuwe-steen-bv-te-hoorn">Bekijk kavels</a> </div> </div>
<div class="row"> <div class="col-sm-9"> <h3> <a class="name" href="/nl/lots/auction/online-veiling-grote-voorraad-car-audio-en-toebehoren"> Online veiling grote voorraad Car audio en toebehoren </a> </h3> <div class="endDate"> <label>Veiling eindigt:</label> 29 sep. 2026 20:00:00 (CEST) </div> <div class="description"><p>Voorraad van een groothandel.</p></div> </div> </div>
<h2>Gesloten Veilingen</h2>
<div class="row"> <div class="col-sm-9"> <h3> <a class="name" href="/nl/lots/auction/faillissementsveiling-prefab-bv-afzetcontainers"> Faillissementsveiling Prefab BV afzetcontainers </a> </h3> <div class="endDate"> <label>Veiling eindigt:</label> 15 sep. 2026 20:00:00 (CEST) </div> <div class="description"><p>In opdracht van de curator.</p></div> </div> </div>
"""


def pjb_lot(lot_id, slug, title, bid, end="28 Sep 2026 20:05:00 (CEST)", bids=8):
    return f"""<div class="lot lot-{lot_id}"><div class="itemwrap"><h3><a href="/nl/lots/{slug}">Kavel 371-002: {title}</a></h3><div class="main-image"><a href="/nl/lots/{slug}"><img src="/media/lot/thumbnail/9999/{slug}.jpg" alt="Preview image"></a><span class="biddingStatus inline"></span></div><div class="itemBidding"><form method="post" action="/nl/lots/{slug}" class="bidForm"><table><tbody><tr class="currentBid"><th class="label">Huidige bieding:</th><td class=""><div class="bootstrap-target-container current-bid-container"><big>€ {bid}</big><div></div></div></td></tr><tr class="increment"><th class="label">Verhoging:</th><td>€ <span class="value">10</span></td></tr><tr class="bidCount"><th class="label">Biedingen:</th><td class="value">{bids}</td></tr><tr class="endDate"><th class="label">Eindigt in:</th><td><span class="value">{end}</span> <span>(</span><span class="remaining">5d 22h</span><span>)</span></td></tr></tbody></table><div class="info">Let op! Je bod wordt verhoogd met 22% opgeld en 21% BTW.</div></form></div></div></div>"""


# ---------------------------------------------------------------- Onlineveilingmeester
OVM_AUCTIONS = {"geslotenCount": 0, "komendeCount": 3, "openCount": 3, "veilingen": [
    {"id": 9472, "type": "DRZ", "naam": "Heren polshorloge Richard Mille RM 61-01 Yohan Blake",
     "omschrijving": "<p>Online veiling in opdracht van Domeinen Roerende Zaken.</p>",
     "sluitingsDatumISO": "2026-09-24T18:30:40Z", "totaalKavels": 1},
    {"id": 9527, "type": "FAILLISEMENT", "naam": "Faillissement van: DN design B.V. ",
     "omschrijving": "<p>Online veiling met design banken afkomstig uit het faillissement van DN design B.V.</p>",
     "sluitingsDatumISO": "2026-09-30T17:30:00Z", "totaalKavels": 6},
    {"id": 9168, "type": "NORMAAL", "naam": "Thuisbezorgveiling: keukens consumentengoederen & partijhandel",
     "omschrijving": "<p>Veiling van diverse goederen afkomstig uit magazijnopruimingen.</p>",
     "sluitingsDatumISO": "2026-09-29T17:30:00Z", "totaalKavels": 295},
]}

OVM_LOT = {"aantalBiedingen": 7, "btwPercentage": 21, "handelingskosten": 0, "hoogsteBod": 76, "id": 1898104,
           "imageList": ["2026-09-08/c13bb75a-3718-4020-ba10-c2de0a6222e1/74084.jpg"],
           "naam": "3-zits Design bank, Dutch New Design, Lefkas", "openingsBod": 10,
           "sluitingsDatumISO": "2026-09-30T17:46:24Z", "type": "B", "verhoging": 5, "volgNummer": "6",
           "veiling": {"id": 9527, "naam": "Faillissement van: DN design B.V. ", "type": "FAILLISEMENT"}}

# ---------------------------------------------------------------- Marktplaats
def mp_listing(title, cents, ptype="FIXED"):
    return {"itemId": "m1", "title": title, "priceInfo": {"priceCents": cents, "priceType": ptype},
            "vipUrl": "/v/x"}
