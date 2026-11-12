from datetime import datetime, timedelta, timezone

from scanner import health

NOW = datetime(2026, 11, 12, 14, 0, tzinfo=timezone.utc)  # 15:00 in NL


def ago(h):
    return (NOW - timedelta(hours=h)).isoformat()


def test_lots_fresh_and_sites_ok():
    lots = {"generated": ago(5), "sites": [{"name": "HNVI", "ok": True, "lots": 10}]}
    assert health.check_lots(lots, NOW) == []


def test_lots_old_site_failed_or_empty():
    lots = {"generated": ago(50), "sites": [{"name": "HNVI", "ok": False, "error": "403"},
                                            {"name": "Vlavem", "ok": True, "lots": 0},
                                            {"name": "Troostwijk", "ok": True, "lots": 5, "private": True, "privateSaved": False}]}
    p = health.check_lots(lots, NOW)
    assert len(p) == 4 and "2 days" in p[0] and "HNVI" in p[1] and "0 lots" in p[2] and "private" in p[3]


def run(conclusion, h):
    return {"status": "completed", "conclusion": conclusion, "updated_at": ago(h)}


def test_runs():
    ok = {"scan.yml": [run("success", 10)], "deals.yml": [run("success", 1)], "commands.yml": [run("success", 1)]}
    assert health.check_runs(ok, NOW) == []
    bad = dict(ok, **{"deals.yml": [run("failure", 0.5), run("failure", 1), run("success", 2)],
                      "scan.yml": [run("success", 40)]})
    p = health.check_runs(bad, NOW)
    assert any("Daily scan: no successful run for 40 h" in x for x in p)
    assert any("Marktplaats bargains: the last 2 runs failed" in x for x in p)
    assert any("Telegram commands" in x for x in health.check_runs(dict(ok, **{"commands.yml": []}), NOW))


def test_private_and_browser():
    files = {"data/troostwijk-lots.json": {"generated": ago(3)}, "data/claude-advice.json": None}
    p = health.check_private(files, NOW)
    assert p == ["Claude advice (Wednesday and Sunday): not updated for a long time."]
    assert health.check_private({}, NOW) == []
    assert health.check_browser({"problems": []}) == []
    assert health.check_browser(None) and health.check_browser({"problems": ["x"]}) == ["Dashboard: x"]


def test_message_escapes():
    assert "&lt;b&gt;" in health.message(["<b>"])
