import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from conftest import FakeHttp, url_has
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
        "settings": {"target_return": 0.30, "min_profit": 25, "resale_factor": 0.85},
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
    assert data["settings"] == {"min_profit": 25, "resale_factor": 0.85, "selling_costs": 0}
    assert {s["id"]: s["ok"] for s in data["sites"]} == {"hnvi": True, "proveiling": True, "plaatsjebod": False,
                                                          "marktplaats": True}

    assert len(tg.sent) == 1
    digest = tg.sent[0]
    assert "Closing within 24 hours" in digest and "Apple iPhone 13 128GB zwart" in digest
    assert "selling at 85% of the Marktplaats median" in digest and "(margin €" in digest
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
