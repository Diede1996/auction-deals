import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from conftest import FakeHttp, FakeResponse, url_has
from fixtures import mp_listing
from scanner import scan as scan_mod
from scanner.bot import run_commands
from scanner.models import Lot
from scanner.scan import run_scan

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 23, 4, 20, tzinfo=timezone.utc)  # 06:20 in Amsterdam

MP = [mp_listing(f"Apple iPhone 13 128GB {c}", cents) for c, cents in
      [("zwart", 32000), ("blauw", 34000), ("wit", 30000), ("rood", 33000), ("groen", 35000), ("roze", 31000)]]


class FakeTelegram:
    """Stands in for scanner.telegram.Telegram; records everything that would be sent."""

    def __init__(self, updates=None):
        self.sent = []
        self._updates = updates or []

    def __call__(self, http, token, chat_id, dry_run=False):
        self.chat_id = str(chat_id) if chat_id else None
        return self

    def send(self, text, chat_id=None, preview=False):
        self.sent.append(text)

    def updates(self, offset):
        return [u for u in self._updates if offset is None or u["update_id"] >= offset]


def make_lots(now):
    return [
        # cheap iPhone closing tonight -> room to bid, closing within 24h
        Lot("hnvi", "1", "Apple iPhone 13 128GB zwart", "https://www.hnvi.nl/veiling-kavel/iphone/1", 60.0,
            now + timedelta(hours=15), "Faillissement X", image="https://assets.hnvi.nl/1.jpg"),
        # expensive iPhone -> above the max bid
        Lot("hnvi", "2", "Apple iPhone 13 blauw", "https://www.hnvi.nl/veiling-kavel/iphone/2", 260.0,
            now + timedelta(days=2), "Faillissement X"),
        # Dyson with a max_price on the watchlist, closes in 4 days; title tries to break out of the page
        Lot("proveiling", "3", "Dyson V11 </script><script>alert(1)</script>", "https://www.proveiling.nl/x/3/detail",
            80.0, now + timedelta(days=4), "Faillissementsveiling Y", location="Emmeloord"),
        # iPhone case -> excluded by the watchlist
        Lot("proveiling", "4", "iPhone 13 hoesje", "https://pv/4", 1.0, now + timedelta(days=1), "Faill. Y"),
        # already closed -> ignored
        Lot("proveiling", "5", "iPhone 13 mini", "https://pv/5", 1.0, now - timedelta(hours=1), "Faill. Y"),
    ]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    shutil.copy(ROOT / "config.yml", tmp_path / "config.yml")
    (tmp_path / "watchlist.yml").write_text(yaml.safe_dump({
        "settings": {"min_margin": 0.30, "resale_factor": 0.85},
        "items": [{"name": "iPhone 13", "keywords": ["iphone 13"], "exclude": ["hoesje"]},
                  {"name": "Dyson", "keywords": ["dyson"], "max_price": 150}],
    }))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("GITHUB_REPOSITORY", "Diede/auction-deals")
    for var in ("GITHUB_STEP_SUMMARY", "GITHUB_OUTPUT", "DASHBOARD_URL"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def scan_once(root, now, monkeypatch, fail_pjb=True):
    lots = make_lots(NOW)
    sites = {
        "hnvi": lambda ctx: [l for l in lots if l.site == "hnvi"],
        "proveiling": lambda ctx: [l for l in lots if l.site == "proveiling"],
    }
    if fail_pjb:
        sites["plaatsjebod"] = lambda ctx: (_ for _ in ()).throw(RuntimeError("HTTP 403 for plaatsjebod"))
    else:
        sites["plaatsjebod"] = lambda ctx: []
    monkeypatch.setattr(scan_mod, "SITES", sites)
    mp_http = FakeHttp([(url_has("marktplaats.nl/lrp/api/search"), {"listings": MP})])
    tg = FakeTelegram()
    assert run_scan(root, now, http_cls=lambda **kw: mp_http, telegram_cls=tg) == 0
    return tg


def page_data(root):
    html = (root / "site" / "index.html").read_text()
    start = html.index('<script id="data" type="application/json">') + len('<script id="data" type="application/json">')
    return html, json.loads(html[start:html.index("</script>", start)])


def test_daily_scan_builds_dashboard_and_digest(repo, monkeypatch):
    tg = scan_once(repo, NOW, monkeypatch)

    html, data = page_data(repo)
    assert "<script>alert(1)" not in html  # scraped text can't escape the data block
    by_key = {l["key"]: l for l in data["lots"]}
    assert set(by_key) == {"hnvi:1", "hnvi:2", "proveiling:3"}
    iphone = by_key["hnvi:1"]
    assert iphone["mp"]["count"] == 6 and iphone["market"] == iphone["mp"]["median"]
    assert iphone["isNew"] and iphone["isDeal"] and iphone["maxBid"] > iphone["bid"]
    assert iphone["premium"] == 0.19 and iphone["vat"] == 0.21
    assert not by_key["hnvi:2"]["isDeal"]
    dyson = by_key["proveiling:3"]
    assert dyson["itemMaxPrice"] == 150 and dyson["maxBid"] == 106  # floor(150 / 1.21 / 1.16): the max_price cap
    assert dyson["mp"] is None and dyson["mpSearch"].startswith("https://www.marktplaats.nl/q/dyson")
    assert data["settings"] == {"min_margin": 0.30, "resale_factor": 0.85, "selling_costs": 0}
    assert {s["id"]: s["ok"] for s in data["sites"]} == {"hnvi": True, "proveiling": True, "plaatsjebod": False,
                                                          "marktplaats": True}

    assert len(tg.sent) == 1
    digest = tg.sent[0]
    assert "Closing within 24 hours" in digest and "Apple iPhone 13 128GB zwart" in digest
    assert "selling at 85% of the Marktplaats median with at least 30% margin" in digest and "(margin €" in digest
    assert 'href="https://diede.github.io/auction-deals/"' in digest
    assert "Dyson" in digest  # new with room to bid

    state = json.loads((repo / "data" / "state.json").read_text())
    assert set(state["seen"]) == {"hnvi:1", "hnvi:2", "proveiling:3"}
    assert state["health"]["plaatsjebod"] == {**state["health"]["plaatsjebod"], "ok": False, "fails": 1}
    assert (repo / "data" / "lots.json").exists()
    assert "https://diede.github.io/auction-deals/" in (repo / "data" / "latest.md").read_text()


def test_next_day_nothing_new_and_site_warning(repo, monkeypatch):
    scan_once(repo, NOW, monkeypatch)
    tg = scan_once(repo, NOW + timedelta(days=1), monkeypatch)
    _, data = page_data(repo)
    assert not any(l["isNew"] for l in data["lots"])  # all seen yesterday
    assert "0 new" in tg.sent[0]
    # iPhone lot 1 closed overnight; Dyson closes within 24h? no (3 days left) -> nothing closing soon
    assert "hnvi:1" not in {l["key"] for l in data["lots"]}
    assert any("Plaats Je Bod" in m and "failed 2 scans" in m for m in tg.sent)
    # third day: the warning is not repeated, and a recovery message follows when the site works again
    tg3 = scan_once(repo, NOW + timedelta(days=2), monkeypatch, fail_pjb=False)
    assert not any("failed" in m for m in tg3.sent)
    assert any("Plaats Je Bod</b> works again" in m for m in tg3.sent)


def test_empty_watchlist_skips_scraping(repo, monkeypatch):
    (repo / "watchlist.yml").write_text("settings: {}\nitems: []\n")
    monkeypatch.setattr(scan_mod, "SITES", {"hnvi": lambda ctx: pytest.fail("should not scrape")})
    tg = FakeTelegram()
    assert run_scan(repo, NOW, http_cls=lambda **kw: FakeHttp([]), telegram_cls=tg) == 0
    assert tg.sent == []
    assert (repo / "site" / "index.html").exists()


def test_commands(repo, monkeypatch, tmp_path):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    tg = FakeTelegram([
        {"update_id": 10, "message": {"chat": {"id": 42}, "text": "/add ps5 -controller max=250"}},
        {"update_id": 11, "message": {"chat": {"id": 999}, "text": "/add hacked"}},  # stranger: ignored
        {"update_id": 12, "message": {"chat": {"id": 42}, "text": "/sellat 50"}},
        {"update_id": 13, "message": {"chat": {"id": 42}, "text": "/scan"}},
        {"update_id": 14, "message": {"chat": {"id": 42}, "text": "/scan"}},
        {"update_id": 15, "message": {"chat": {"id": 42}, "text": "/dashboard"}},
    ])
    assert run_commands(repo, NOW, http_cls=lambda **kw: None, telegram_cls=tg) == 0
    wl = yaml.safe_load((repo / "watchlist.yml").read_text())
    assert [i["name"] for i in wl["items"]] == ["iPhone 13", "Dyson", "ps5"]
    assert wl["settings"]["resale_factor"] == 0.5 and "target_return" not in wl["settings"]
    assert (repo / "watchlist.yml").read_text().startswith("# Your watchlist")
    tg_state = json.loads((repo / "data" / "telegram.json").read_text())
    assert tg_state["offset"] == 16 and len(tg_state["extra_scans"]) == 1 and tg_state["welcomed"]
    assert out.read_text().strip() == "scan=true"
    replies = "\n".join(tg.sent)
    assert "Added" in replies and "50% of the Marktplaats median" in replies and "Scanning now" in replies
    assert "already starting" in replies and "https://diede.github.io/auction-deals/" in replies
    assert "hacked" not in replies

    # the same messages are not handled twice
    tg.sent.clear()
    out.write_text("")
    run_commands(repo, NOW + timedelta(minutes=15), http_cls=lambda **kw: None, telegram_cls=tg)
    assert tg.sent == [] and out.read_text().strip() == "scan=false"


def test_scan_limit_per_day(repo, monkeypatch):
    (repo / "data").mkdir()
    (repo / "data" / "telegram.json").write_text(json.dumps(
        {"offset": 1, "welcomed": "x", "extra_scans": ["2026-09-23"] * 3}))
    tg = FakeTelegram([{"update_id": 1, "message": {"chat": {"id": 42}, "text": "/scan"}}])
    run_commands(repo, NOW, http_cls=lambda **kw: None, telegram_cls=tg)
    assert "used today's 3 extra scans" in tg.sent[0]


def test_chat_id_helper(repo, monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_CHAT_ID")
    tg = FakeTelegram([{"update_id": 1, "message": {"chat": {"id": 777, "first_name": "Diede"}, "text": "hi"}}])
    assert run_commands(repo, NOW, http_cls=lambda **kw: None, telegram_cls=tg) == 0
    assert "777" in capsys.readouterr().out
    assert "<code>777</code>" in tg.sent[0]


# ---------------------------------------------------------------- favorites, driving costs, exact models

def issue_list(favs, login="diede"):
    from scanner.favorites import MARKER
    return [{"number": 12, "user": {"login": login},
             "body": f"{MARKER}\nFavorites\n```json\n{json.dumps(favs)}\n```"}]


def geo_routes():
    def pdok(m, u, kw):
        if "Dorpsstraat" in u:
            return {"response": {"docs": [{"centroide_ll": "POINT(5.54 51.61)", "weergavenaam": "Dorpsstraat 1, 5461AA Veghel"}]}}
        if "Hooffstraat" in u:
            return {"response": {"docs": [{"centroide_ll": "POINT(5.47 51.44)", "weergavenaam": "Jan van Hooffstraat 3, 5611ED Eindhoven"}]}}
        return {"response": {"docs": [{"centroide_ll": "POINT(5.72 52.70)", "weergavenaam": "Produktieweg 9, 8304AV Emmeloord"}]}}

    def osrm(m, u, kw):
        n = u.split("/driving/")[1].split("?")[0].count(";")
        return {"code": "Ok", "distances": [[0] + [40000 * (i + 1) for i in range(n)]],
                "durations": [[0] + [1800 * (i + 1) for i in range(n)]]}

    energia = "<table><tr><td>Benzine 95 RON - E10</td><td>€/l 2.1080</td><td>vanaf 2026-09-22</td></tr></table>"
    return [(url_has("api.pdok.nl"), pdok), (url_has("router.project-osrm.org"), osrm),
            (url_has("energiafed.be"), energia)]


def test_scan_with_favorites_and_driving_costs(repo, monkeypatch):
    monkeypatch.setenv("HOME_ADDRESS", "Dorpsstraat 1, Veghel")
    lots = make_lots(NOW)
    lots[0].pickup, lots[0].pickup_when = "Jan van Hooffstraat 3, Eindhoven", "Mon 5 Oct, 08:00–13:00"
    lots[2].pickup = "Produktieweg 9, 8304AV Emmeloord"
    monkeypatch.setattr(scan_mod, "SITES", {"hnvi": lambda ctx: [l for l in lots if l.site == "hnvi"],
                                            "proveiling": lambda ctx: [l for l in lots if l.site == "proveiling"]})
    favs = [{"key": "hnvi:1", "title": "Apple iPhone 13 128GB zwart", "url": "https://www.hnvi.nl/veiling-kavel/iphone/1",
             "closes": (NOW + timedelta(hours=15)).isoformat()},
            {"key": "hnvi:99", "title": "Something next week", "url": "https://www.hnvi.nl/veiling-kavel/x/99",
             "closes": (NOW + timedelta(days=7)).isoformat()}]
    http = FakeHttp([(url_has("marktplaats.nl/lrp/api/search"), {"listings": MP}),
                     (url_has("api.github.com/repos/Diede/auction-deals/issues"), issue_list(favs))] + geo_routes())
    tg = FakeTelegram()
    assert run_scan(repo, NOW, http_cls=lambda **kw: http, telegram_cls=tg) == 0

    html, data = page_data(repo)
    by_key = {l["key"]: l for l in data["lots"]}
    iphone = by_key["hnvi:1"]
    assert iphone["pickup"] == "Jan van Hooffstraat 3, Eindhoven" and iphone["pickupWhen"].startswith("Mon 5 Oct")
    assert iphone["trip"]["km"] == 40.0 and iphone["trip"]["min"] == 30
    assert iphone["trip"]["cost"] == pytest.approx(80 / 16 * 2.108, abs=0.01)  # there and back, 1 op 16
    assert data["driving"]["fuel"] == 2.108 and data["driving"]["home"] is True
    assert data["favorites"]["issue"] == 12 and len(data["favorites"]["items"]) == 2
    assert data["repo"] == "Diede/auction-deals"
    assert iphone["mp"]["kind"] == "exact" and iphone["mpPlan"]["kind"] == "exact"  # phone_plan: iPhone 13, not a Pro or mini
    # the €10.54 trip lowers the max bid: floor((85% of 325 / 1.3 - 10.54) / 1.21 / 1.19) = 140 instead of 147
    assert iphone["maxBid"] == 140
    # your address is never written to the (public) repository or dashboard
    for path in ("data/state.json", "data/lots.json", "site/index.html"):
        assert "Dorpsstraat" not in (repo / path).read_text()
    digest = tg.sent[0]
    assert "Your favorites closing today" in digest and digest.index("favorites") < digest.index("Closing within 24")
    assert "Something next week" not in digest
    assert "🚗 40 km" in digest


def test_favorite_reminders_and_list(repo, monkeypatch):
    (repo / "data").mkdir(exist_ok=True)
    closes = NOW + timedelta(minutes=40)
    (repo / "data" / "lots.json").write_text(json.dumps({"lots": [
        {"key": "hnvi:1", "closes": closes.isoformat(), "bid": 60, "maxBid": 170, "pickup": "Jan van Hooffstraat 3, Eindhoven",
         "trip": {"km": 40.2}}]}))
    (repo / "data" / "telegram.json").write_text(json.dumps({"offset": 1, "welcomed": "x"}))
    favs = [{"key": "hnvi:1", "title": "Apple iPhone 13 128GB zwart", "url": "https://www.hnvi.nl/veiling-kavel/iphone/1",
             "closes": (NOW + timedelta(hours=5)).isoformat()}]  # the scan knows the newer closing time
    http = FakeHttp([(url_has("api.github.com"), issue_list(favs))])
    tg = FakeTelegram([{"update_id": 1, "message": {"chat": {"id": 42}, "text": "/favorites"}}])
    assert run_commands(repo, NOW, http_cls=lambda **kw: http, telegram_cls=tg) == 0
    listing, reminder = tg.sent
    assert "Your favorites" in listing and "Apple iPhone 13" in listing
    assert "Closes in 40 min" in reminder and "your max <b>€170</b>" in reminder and "40 km" in reminder
    # every 5 minutes the job runs again: no second reminder for the same closing time
    tg.sent.clear()
    run_commands(repo, NOW + timedelta(minutes=15), http_cls=lambda **kw: http, telegram_cls=tg)
    assert tg.sent == []


def test_bulk_lots_count_every_item():
    from scanner.evaluate import Fees, Settings, evaluate
    from scanner.marktplaats import PriceEstimate
    from scanner.models import WatchItem
    from scanner.scan import units_of
    lot = Lot("onlineveilingmeester", "1", "40x Colbert heren", "u", 50.0, None)
    assert units_of(lot) == 40 and units_of(lot, max_units=10) == 1
    est = PriceEstimate(median=20, low=15, high=25, count=9, query="colbert heren")
    v = evaluate(WatchItem(name="Suit", keywords=["colbert"]), lot, Fees(premium=0.17, vat=0.21), Settings(), est, 40)
    # 40 x €20 x 85% = €680 resale; margin and max bid are for the whole lot
    assert v.resale == pytest.approx(680) and v.profit == pytest.approx(680 - 50 * 1.17 * 1.21)
    assert v.max_bid == 369  # floor(680 / 1.3 / 1.21 / 1.17)


def test_scan_includes_troostwijk_alert_lots(repo, monkeypatch):
    from test_mail_alerts import ALERT, FakeIMAP, RedirectHttp, _mail
    from scanner.mail_alerts import Mailbox
    FakeIMAP.mails = [_mail(ALERT.replace("Makita DHP484 accu klopboormachine", "Apple iPhone 13 128GB via Troostwijk"))]
    monkeypatch.setattr(scan_mod.Mailbox, "from_env", classmethod(lambda cls: Mailbox("b@gmail.com", "app-pass", imap_cls=FakeIMAP)))
    monkeypatch.setattr(scan_mod, "SITES", {"hnvi": lambda ctx: []})

    class Http(RedirectHttp):
        def request(self, method, url, **kw):
            if "marktplaats" in url:
                self.calls.append((method, url, kw))
                return FakeResponse({"listings": MP})
            return super().request(method, url, **kw)

    tg = FakeTelegram()
    assert run_scan(repo, NOW, http_cls=lambda **kw: Http([]), telegram_cls=tg) == 0
    _, data = page_data(repo)
    tw = [l for l in data["lots"] if l["site"] == "troostwijk"]
    assert [l["title"] for l in tw] == ["Apple iPhone 13 128GB via Troostwijk"]  # only watchlist matches
    assert tw[0]["premium"] == 0.18 and tw[0]["mp"]["count"] == 6 and tw[0]["maxBid"] > 0
    assert {"id": "troostwijk", "name": "Troostwijk", "ok": True, "lots": 3, "error": ""} in data["sites"]
    assert any(f["id"] == "troostwijk" for f in data["fees"])


def test_scan_with_forwarded_troostwijk_auction_alert(repo, monkeypatch):
    """The real email of 28 Sep: every link behind a tracker, closing day without a time."""
    from test_mail_alerts import BYLDIS_HTML, FakeIMAP, NoRequests, _mail
    from scanner.mail_alerts import Mailbox
    (repo / "watchlist.yml").write_text(yaml.safe_dump({"items": [{"name": "Monitor", "keywords": ["monitor"]}]}))
    FakeIMAP.mails = [_mail(BYLDIS_HTML, sender="Diede <me@gmail.com>", date="Mon, 28 Sep 2026 17:16:00 +0200")]
    monkeypatch.setattr(scan_mod.Mailbox, "from_env", classmethod(lambda cls: Mailbox("b@gmail.com", "app-pass", imap_cls=FakeIMAP)))
    monkeypatch.setattr(scan_mod, "SITES", {"hnvi": lambda ctx: []})

    hp_listings = [mp_listing(f"HP EliteDisplay E241i 24 inch monitor {i}", c) for i, c in
                   enumerate([4500, 5000, 5500, 6000, 4000])]

    class Http(NoRequests):
        def request(self, method, url, **kw):
            assert "exponea" not in url and "troostwijkauctions" not in url
            return FakeResponse({"listings": hp_listings})

    tg = FakeTelegram()
    assert run_scan(repo, NOW, http_cls=lambda **kw: Http([]), telegram_cls=tg) == 0
    _, data = page_data(repo)
    tw = [l for l in data["lots"] if l["site"] == "troostwijk"]
    assert [l["title"] for l in tw] == ["HP Elite E241i Monitor (2x)"]
    assert tw[0]["closes"] is None and tw[0]["closesDay"] == "2026-10-07" and tw[0]["location"] == "Veldhoven"
    # the €10 in the email is the starting bid: not "room to bid", but listed to check on the lot page
    assert tw[0]["bidFromEmail"] is True and tw[0]["maxBid"] > 10
    digest = tg.sent[-1]
    assert "0 with room to bid" in digest and "New from Troostwijk emails" in digest
    assert "HP Elite E241i Monitor (2x)</a>\n   bid up to <b>€" in digest and "closes Wed 7 Oct" in digest
    assert tw[0]["units"] == 2 and tw[0]["auction"].startswith("Faillissement Byldis")
    assert {"id": "troostwijk", "name": "Troostwijk", "ok": True, "lots": 9, "error": ""} in data["sites"]


def test_digest_names_troostwijk_emails_without_lots(repo, monkeypatch):
    from test_mail_alerts import FakeIMAP, _mail
    from scanner.mail_alerts import Mailbox
    FakeIMAP.mails = [_mail("<p>Welkom bij Troostwijk <b>& co</b></p>", subject="Welkom bij Troostwijk Auctions",
                            date="Thu, 24 Sep 2026 20:15:00 +0200")]
    monkeypatch.setattr(scan_mod.Mailbox, "from_env", classmethod(lambda cls: Mailbox("b@gmail.com", "app-pass", imap_cls=FakeIMAP)))
    monkeypatch.setattr(scan_mod, "SITES", {"hnvi": lambda ctx: []})
    tg = FakeTelegram()
    assert run_scan(repo, NOW, http_cls=lambda **kw: FakeHttp([]), telegram_cls=tg) == 0
    assert "• Welkom bij Troostwijk Auctions (Thu 24 Sep)" in tg.sent[-1]
    assert "Welkom" not in (repo / "data" / "state.json").read_text()
    tg = FakeTelegram()
    assert run_scan(repo, NOW + timedelta(days=1), http_cls=lambda **kw: FakeHttp([]), telegram_cls=tg) == 0
    assert "Welkom" not in tg.sent[-1]  # reported once


def test_digest_links_troostwijk_saved_searches(repo, monkeypatch):
    from test_mail_alerts import SAVED_SEARCHES_HTML, FakeIMAP, NoRequests, _mail, search_page
    from scanner.mail_alerts import Mailbox
    FakeIMAP.mails = [_mail(SAVED_SEARCHES_HTML, subject="Je opgeslagen zoekopdrachten",
                            date="Tue, 22 Sep 2026 03:25:40 +0200")]
    monkeypatch.setattr(scan_mod.Mailbox, "from_env", classmethod(lambda cls: Mailbox("b@gmail.com", "app-pass", imap_cls=FakeIMAP)))
    monkeypatch.setattr(scan_mod, "SITES", {"hnvi": lambda ctx: []})
    tg = FakeTelegram()
    assert run_scan(repo, NOW, http_cls=lambda **kw: NoRequests([]), telegram_cls=tg) == 0
    digest = tg.sent[-1]
    assert "🔎 Troostwijk has new lots for your saved searches: " in digest and "couldn't find any lots" not in digest
    assert f'<a href="{search_page("festool").replace("&", "&amp;")}">festool</a> · <a href=' in digest
    tg = FakeTelegram()
    assert run_scan(repo, NOW + timedelta(days=1), http_cls=lambda **kw: NoRequests([]), telegram_cls=tg) == 0
    assert "saved searches" not in tg.sent[-1]  # each email once
