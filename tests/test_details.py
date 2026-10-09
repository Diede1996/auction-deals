"""Type numbers that are only in the lot description (Plaats Je Bod, 3 Oct 2026: "2 x Dell 24 inch monitor")."""
from datetime import datetime, timedelta, timezone

from conftest import FakeHttp, url_has
from scanner.details import description_from_page, fill_descriptions
from scanner.identify import mac_plan, model_code, plan_for
from scanner.models import Lot, WatchItem

NOW = datetime(2026, 10, 3, 4, 20, tzinfo=timezone.utc)
MONITOR = WatchItem(name="Monitor", keywords=["monitor", "beeldscherm"])

PJB_LOT_PAGE = """<html><body><h1>Kavel 371-002: 2 x Dell 24 inch monitor</h1>
<div class="info">Let op! Je bod wordt verhoogd met 22% opgeld en 21% BTW.</div>
<h2>Omschrijving</h2><p>2 x Dell 24 inch monitor type U2419 HC</p>
<p><b>Tijdens de veiling kunnen wij actieve bieders telefonisch of schriftelijk benaderen om biedingen te
controleren. Dit doen we om bieders en het biedproces te beschermen.</b></p>
<h3>Retourneren?</h3><p>Dit betreft een faillissements- of openbare veiling.</p></body></html>"""


def needs(lot):
    return model_code(lot.title) is None and mac_plan(lot.title) is None


def test_description_from_page():
    text = description_from_page(PJB_LOT_PAGE)
    assert text.startswith("2 x Dell 24 inch monitor type U2419 HC") and "Retourneren" not in text and len(text) <= 300
    assert description_from_page("<p>Omschrijving: Dell P2419H, 24 inch</p>") == "Dell P2419H, 24 inch"
    assert description_from_page("<p>Geen beschrijving hier</p>") == ""


def test_type_number_from_the_description():
    lot = Lot("plaatsjebod", "371-002", "2 x Dell 24 inch monitor", "u", 10, None,
              description="2 x Dell 24 inch monitor type U2419 HC Tijdens de veiling kunnen wij actieve bieders")
    p = plan_for(MONITOR, lot)
    assert p.kind == "exact" and p.searches == ["dell u2419", "u2419"] and "description" in p.note
    # a short code without the brand next to it may be anything: still a rough price
    vague = Lot("hnvi", "1", "Beeldscherm Dell", "u", 10, None, description="Kleur zwart, kabel HDMI, model P24")
    assert plan_for(MONITOR, vague).searches == ["monitor dell"]
    # the title wins when it has a type number
    titled = Lot("hnvi", "2", "Beeldscherm Dell P2419H", "u", 10, None, description="type U2719D")
    assert plan_for(MONITOR, titled).searches[0] == "dell p2419h"


def test_fill_descriptions_reads_each_lot_page_once():
    lots = [Lot("plaatsjebod", "1", "2 x Dell 24 inch monitor", "https://www.plaatsjebod.nl/nl/lots/a", 10, None),
            Lot("plaatsjebod", "2", "Dell P2419H monitor", "https://www.plaatsjebod.nl/nl/lots/b", 10, None),  # has a type
            Lot("troostwijk", "3", "Dell monitor", "https://www.troostwijkauctions.com/nl/l/x", 10, None),  # never visited
            Lot("onlineveilingmeester", "4", "Monitor, Dell", "https://www.onlineveilingmeester.nl/x", 10, None)]
    matched = [(MONITOR, lot) for lot in lots]
    http = FakeHttp([(url_has("/nl/lots/a"), PJB_LOT_PAGE)])
    cache = {}
    assert fill_descriptions(matched, lambda: http, cache, NOW, needs) == 1
    assert "U2419 HC" in lots[0].description and lots[1].description == lots[2].description == ""
    assert [u for _, u, _ in http.calls] == ["https://www.plaatsjebod.nl/nl/lots/a"]
    # next scan: from the cache, no request
    again = [(MONITOR, Lot("plaatsjebod", "1", "2 x Dell 24 inch monitor", "https://www.plaatsjebod.nl/nl/lots/a", 10, None))]
    http.calls.clear()
    assert fill_descriptions(again, lambda: http, cache, NOW + timedelta(days=1), needs) == 0
    assert http.calls == [] and "U2419 HC" in again[0][1].description
    # forgotten after 30 days
    fill_descriptions([], lambda: http, cache, NOW + timedelta(days=31), needs)
    assert cache == {}


def test_fill_descriptions_limit_and_errors():
    lots = [Lot("hnvi", str(i), "Beeldscherm Dell", f"https://www.hnvi.nl/kavel/{i}", 10, None) for i in range(5)]
    def page(m, u, kw):
        if u.endswith("/0"):
            raise RuntimeError("HTTP 500")
        return "<p>Omschrijving</p><p>Dell P2419H</p>"
    http = FakeHttp([(url_has("hnvi.nl"), page)])
    cache = {}
    assert fill_descriptions([(MONITOR, lot) for lot in lots], lambda: http, cache, NOW, needs, limit=2) == 2
    assert "hnvi:0" not in cache  # failed: tried again next scan
    assert len(cache) == 2


def test_a_site_that_fails_twice_in_a_row_is_left_alone():
    lots = [Lot("hnvi", str(i), "Beeldscherm Dell", f"https://www.hnvi.nl/kavel/{i}", 10, None) for i in range(5)]
    def page(m, u, kw):
        raise RuntimeError("timeout")
    http = FakeHttp([(url_has("hnvi.nl"), page)])
    assert fill_descriptions([(MONITOR, lot) for lot in lots], lambda: http, {}, NOW, needs) == 0
    assert len(http.calls) == 2
