"""digest schedule: weekly — one Telegram overview on Sunday evening of what closes in the coming week."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from conftest import FakeHttp, url_has
from scanner import scan as scan_mod
from scanner.bot import run_commands
from scanner.scan import run_scan, weekly_summary_due
from test_main import MP, FakeTelegram, make_lots, repo  # noqa: F401  (repo is a fixture)

WED = datetime(2026, 9, 23, 4, 20, tzinfo=timezone.utc)      # Wed 06:20 in Amsterdam
SUN = datetime(2026, 9, 27, 17, 30, tzinfo=timezone.utc)     # Sun 19:30 in Amsterdam
SUN_AFTERNOON = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
MON = datetime(2026, 9, 28, 11, 0, tzinfo=timezone.utc)      # Mon 13:00: GitHub started Sunday's scan late


@pytest.fixture
def weekly(repo):  # noqa: F811
    cfg = repo / "config.yml"
    cfg.write_text(cfg.read_text().replace("  schedule: daily\n", "  schedule: weekly\n"))
    return repo


def scan_at(root, now, monkeypatch):
    lots = make_lots(now)
    monkeypatch.setattr(scan_mod, "SITES", {"hnvi": lambda ctx: [l for l in lots if l.site == "hnvi"],
                                            "proveiling": lambda ctx: [l for l in lots if l.site == "proveiling"]})
    mp_http = FakeHttp([(url_has("marktplaats.nl/lrp/api/search"), {"listings": MP})])
    tg = FakeTelegram()
    assert run_scan(root, now, http_cls=lambda **kw: mp_http, telegram_cls=tg) == 0
    return [m for m in tg.sent if "Auction" in m]


def test_due_only_from_sunday_evening_once_a_week():
    cfg = {"weekly_from_hour": 18}
    assert weekly_summary_due(cfg, {}, WED) is None
    assert weekly_summary_due(cfg, {}, SUN_AFTERNOON) is None
    assert weekly_summary_due(cfg, {}, SUN) == "2026-09-27"
    assert weekly_summary_due(cfg, {}, MON) == "2026-09-27"  # late start: still this week's summary
    assert weekly_summary_due(cfg, {"weekly_summary": "2026-09-27"}, MON) is None
    assert weekly_summary_due(cfg, {}, MON + timedelta(days=1)) is None


def test_no_summary_on_weekdays_but_the_dashboard_updates(weekly, monkeypatch):
    assert scan_at(weekly, WED, monkeypatch) == []
    assert (weekly / "site" / "index.html").exists()


def test_sunday_evening_week_overview_sent_once(weekly, monkeypatch):
    assert scan_at(weekly, SUN_AFTERNOON, monkeypatch) == []
    sent = scan_at(weekly, SUN, monkeypatch)
    assert len(sent) == 1
    week = sent[0]
    assert "Auction week ahead" in week and "Closing in the coming week" in week
    assert "Apple iPhone 13 128GB zwart" in week and "Mon 28 Sep" in week  # grouped by closing day
    assert "Closing within 24 hours" not in week
    assert json.loads((weekly / "data" / "state.json").read_text())["weekly_summary"] == "2026-09-27"
    assert scan_at(weekly, SUN + timedelta(hours=1), monkeypatch) == []   # not twice
    assert scan_at(weekly, MON, monkeypatch) == []


def test_late_scan_on_monday_still_sends_it(weekly, monkeypatch):
    assert len(scan_at(weekly, MON, monkeypatch)) == 1


def test_scan_command_always_sends_the_overview(weekly, monkeypatch):
    monkeypatch.setenv("SEND_SUMMARY", "true")
    sent = scan_at(weekly, WED, monkeypatch)
    assert len(sent) == 1 and "Auction week ahead" in sent[0]
    assert "weekly_summary" not in json.loads((weekly / "data" / "state.json").read_text())


def test_telegram_job_starts_the_sunday_scan(weekly, monkeypatch, tmp_path):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    tg = FakeTelegram()
    run_commands(weekly, SUN_AFTERNOON, http_cls=lambda **kw: None, telegram_cls=tg)
    assert out.read_text().strip().endswith("scan=false")
    out.write_text("")
    run_commands(weekly, SUN, http_cls=lambda **kw: None, telegram_cls=tg)
    assert out.read_text().strip() == "scan=true"
    out.write_text("")
    run_commands(weekly, SUN + timedelta(minutes=30), http_cls=lambda **kw: None, telegram_cls=tg)
    assert out.read_text().strip() == "scan=false"  # that scan is still running
    # once the summary went out, no more scans
    (weekly / "data" / "state.json").write_text(json.dumps({"weekly_summary": "2026-09-27"}))
    out.write_text("")
    run_commands(weekly, SUN + timedelta(hours=4), http_cls=lambda **kw: None, telegram_cls=tg)
    assert out.read_text().strip() == "scan=false"
