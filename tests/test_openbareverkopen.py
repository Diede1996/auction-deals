"""openbare-verkopen.be: markup and JSON as seen on the live site on 3 Oct 2026 (shortened)."""
from datetime import datetime, timezone

from conftest import FakeHttp, url_has
from scanner.matching import bankruptcy_matcher
from scanner.sites.base import SiteContext
from scanner.sites.openbareverkopen import fetch_lots, parse_auctions, parse_pickup

NOW = datetime(2026, 10, 3, 4, 20, tzinfo=timezone.utc)


def teaser(aid, title, place, end, lots):
    return f"""<div class="auction-teaser"><div class="auction-info-text">
  <div class="field field--name-initial-end-time"><div class="field__label">Eindtijd</div>
    <div class="field__item"><time datetime="{end}" class="datetime">x</time> CET</div></div>
  <div class="field field--name-title field--type-string field--label-hidden field__item">{title}</div>
  <div class="location-lots"><ul><li><svg></svg><span> {place} </span></li></ul>
    <div class="field field--name-number"><div class="field__label">Veilingnummer</div><div class="field__item">5428</div></div>
    <div class="numner-of-lots"> {lots} kavels </div></div></div>
  <a class="link-overlay" href="https://www.openbare-verkopen.be/auction/{aid}"></a></div>"""


AUCTIONS = f"""<html><body><h2>Lopende veilingen</h2>
{teaser(5162, "IT en multimedia: servers, laptops, netwerk", "8710 Wielsbeke", "2026-10-14T18:00:00Z", 30)}
{teaser(5137, "Stopzetting decoratie en interieurwinkel", "8790 Waregem", "2026-10-08T17:00:00Z", 171)}
{teaser(5170, "Stad Gent rollend materieel", "8710 Wielsbeke", "2026-10-12T17:00:00Z", 23)}
<h2 id="upcoming">Komende veilingen</h2>
{teaser(5200, "Faillissement bakkerij", "9000 Gent", "2026-10-20T17:00:00Z", 40)}
<h2 id="ended">Afgelopen veilingen</h2>
{teaser(5001, "Faillissementsvoertuigen September", "8710 Wielsbeke", "2026-09-29T17:00:00Z", 7)}
</body></html>"""


def auction_page(body):
    return f"""<html><body><h1>t</h1><div class="field field--name-number">Veilingnummer 1</div>
<div class="clearfix text-formatted field field--name-body field--type-text-with-summary field--label-hidden field__item">
<p>{body}</p></div><script>auctions={{}};lots={{}}</script></body></html>"""


PICKUP = """<html><body><main><div>Home / Kijk- en afhaaldagen</div>
<div>Opgelet: er zijn meerdere locaties en/of tijdstippen die niet voor elk kavel van toepassing zijn.</div>
<h3>Bezoekdag(en)</h3><div>30/09/2026 - 10:00 tot 17:00</div>
<div><p>Gentseweg 715<br>8790 Waregem<br>België</p></div>
<div>Op afspraak na betaling via Brecht tel.:0474 86 61 19</div>
<div><p>Provinciebaan 74/6<br>8880 Ledegem<br>België</p></div>
<h3>Ophaaldag(en)</h3><div><span>13/10/2026 - 10:00 tot 12:00</span><span>14/10/2026 - 10:00 tot 12:00</span></div>
<div><p>Gentseweg 715<br>8790 Waregem<br>België</p></div>
</main></body></html>"""


def lot(lot_id, number, title, start, amount, ends, image="/sites/default/files/styles/3_2_small/public/x.jpg"):
    return {"lot_id": str(lot_id), "lot_end_time": ends, "lot_status": "1", "number": str(number),
            "start_amount": str(start), "amount": str(amount), "auction_id": "5137", "status": "1",
            "lot_image": image, "titles": {"nl": title}, "descriptions": {"nl": "incl harde schijven<br />\n gereset"}}


def loader(page, lots, pages):
    return {"lots": {l["lot_id"]: l for l in lots}, "lot_number_of_bids": {l["lot_id"]: 2 for l in lots},
            "pages": pages, "page": str(page), "lot_count": 3, "highest_bids": {}, "auctions": {}}


def test_parse_auctions_running_only():
    found = {a.auction_id: a for a in parse_auctions(AUCTIONS)}
    assert set(found) == {"5162", "5137", "5170"}  # not the upcoming and ended ones
    a = found["5137"]
    assert a.title == "Stopzetting decoratie en interieurwinkel" and a.pickup == "8790 Waregem, België"
    assert a.town == "Waregem" and a.closes_at == datetime(2026, 10, 8, 17, 0, tzinfo=timezone.utc)
    assert a.url == "https://www.openbare-verkopen.be/auction/5137"


def test_parse_pickup():
    address, when = parse_pickup(PICKUP)
    assert address == "Gentseweg 715, 8790 Waregem, België" and when == "Tue 13 Oct, 10:00–12:00"


def test_fetch_lots_only_bankruptcy_and_closures():
    t = int(datetime(2026, 10, 8, 17, 0, tzinfo=timezone.utc).timestamp())
    http = FakeHttp([
        (url_has("/lot-loader/auction/5162?page=1"), loader(1, [lot(751788, 18, "MacBook Pro model a2442", 150, 410, t)], 1)),
        (url_has("/lot-loader/auction/5137?page=1"), loader(1, [lot(749433, 1, "Kunstplant olijfboom", 20, 25, t)], 2)),
        (url_has("/lot-loader/auction/5137?page=2"), loader(2, [lot(749483, 51, "Jambo Geurstokken", 20, 20, t),
                                                                 lot(749484, 52, "Vaas", 20, 20, 1000)], 2)),
        (url_has("/auctions"), AUCTIONS),
        (url_has("/auction/5162/viewing"), PICKUP),
        (url_has("/auction/5137/viewing"), PICKUP),
        (url_has("/auction/5162"), auction_page("Verzameling van IT gerelateerde goederen uit diverse falingen.")),
        (url_has("/auction/5137"), auction_page("Alles moet weg.")),
        (url_has("/auction/5170"), auction_page("Rollend materieel van de stad.")),
    ])
    cache = {}
    ctx = SiteContext(http=http, now=NOW, is_bankruptcy=bankruptcy_matcher(["faillissement", "faling", "stopzetting"]),
                      cache=cache)
    lots = {l.lot_id: l for l in fetch_lots(ctx)}
    assert set(lots) == {"751788", "749433", "749483"}  # not "Stad Gent", not the closed lot
    mac = lots["751788"]
    assert mac.title == "MacBook Pro model a2442" and mac.current_bid == 410.0 and mac.bids == 2
    assert mac.url == "https://www.openbare-verkopen.be/lot/751788" and mac.closes_at.tzinfo is not None
    assert mac.image == "https://www.openbare-verkopen.be/sites/default/files/styles/3_2_small/public/x.jpg"
    assert mac.pickup == "Gentseweg 715, 8790 Waregem, België" and mac.pickup_when == "Tue 13 Oct, 10:00–12:00"
    assert mac.auction_title.startswith("IT en multimedia") and mac.site == "openbareverkopen"
    assert cache["bankrupt"] == {"5162": True, "5137": True, "5170": False}
    # the next day the descriptions aren't fetched again
    http.calls.clear()
    fetch_lots(SiteContext(http=http, now=NOW, is_bankruptcy=ctx.is_bankruptcy, cache=cache))
    assert not any(u.rstrip("/").endswith(("/auction/5162", "/auction/5137", "/auction/5170")) for _, u, _ in http.calls)


def test_estates_count_too():
    import yaml
    from pathlib import Path
    keywords = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yml").read_text())["auction_keywords"]
    wanted = bankruptcy_matcher(keywords)
    for title in ["Inboedel nalatenschap Dentergem", "Boedelveiling Oss", "Woningontruiming Eindhoven",
                  "Inboedel na overlijden", "Faillissementsvoertuigen Oktober", "Stopzetting decoratie en interieurwinkel"]:
        assert wanted(title), title
    for title in ["Stad Gent rollend materieel", "Snelveiling Plotter Canon + vouwmachine Estefold",
                  "Decomarket: Laminaat - PVC & Vinyl"]:
        assert not wanted(title), title
