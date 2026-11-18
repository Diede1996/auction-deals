from datetime import datetime, timezone

from scanner import scan_health
from scanner.sites.troostwijk import find_auction_list, shape

NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
A = {"urlSlug": "x-A1-1", "name": "Faillissement X", "collectionDays": [{"countryCode": "NL"}], "minEndDate": 1}


def test_auction_list_old_layout():
    assert find_auction_list({"listData": [A], "totalSize": 9}) == ([A], 9)


def test_auction_list_found_when_the_page_moved_it():
    props = {"seo": {"title": "t"}, "auctionList": {"initialPage": {"items": [A, A, A], "totalCount": 120}}}
    assert find_auction_list(props) == ([A, A, A], 120)
    assert find_auction_list({"nothing": [{"a": 1}]}) == ([], None)


def test_shape_shows_only_structure():
    out = shape({"auctionList": {"items": [{"name": "Faillissement Geheim"}]}})
    assert "Geheim" not in out and "auctionList" in out and "1×" in out


def run(sites, state):
    return scan_health.annotate(sites, state, NOW)


def test_drop_to_zero_after_normal_scans_is_flagged():
    state = {}
    for _ in range(4):
        run([{"id": "hnvi", "ok": True, "lots": 1200, "auctions": 8, "auctionsRead": 8}], state)
    s = run([{"id": "hnvi", "ok": True, "lots": 0}], state)[0]
    assert s["usual"] == 1200 and "0 lots" in s["warn"]


def test_no_auctions_of_our_kind_is_fine():
    s = run([{"id": "nedveiling", "ok": True, "lots": 0, "auctions": 8, "auctionsRead": 0}], {})[0]
    assert s["warn"] == ""


def test_troostwijk_list_empty_is_flagged_even_with_email_lots():
    s = run([{"id": "troostwijk", "ok": True, "lots": 108, "siteAuctions": 0, "siteLots": 0, "mailLots": 108}], {})[0]
    assert "0 auctions" in s["warn"]
    s = run([{"id": "troostwijk", "ok": True, "lots": 400, "siteAuctions": 140, "siteLots": 300, "mailLots": 100}], {})[0]
    assert s["warn"] == ""


def test_history_is_capped():
    state = {}
    for _ in range(20):
        run([{"id": "hnvi", "ok": True, "lots": 5}], state)
    assert len(state["site_history"]["hnvi"]) == scan_health.KEEP
