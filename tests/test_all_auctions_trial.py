from datetime import datetime, timezone

from scanner.matching import all_auctions_trial
from scanner.models import Auction, Lot, WatchItem
from scanner.evaluate import Verdict
from scanner.scan import record_trial, trial_counts
from scanner.sites.base import SiteContext

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def test_trial_is_on_until_its_last_day():
    cfg = {"all_auctions": {"enabled": True, "until": "2026-11-12", "max_auctions_per_site": 40}}
    assert all_auctions_trial(cfg, NOW) == {"until": datetime(2026, 11, 12).date(), "max_per_site": 40}
    assert all_auctions_trial(cfg, datetime(2026, 11, 12, 22, 30, tzinfo=timezone.utc)) is not None  # 23:30 in NL
    assert all_auctions_trial(cfg, datetime(2026, 11, 12, 23, 30, tzinfo=timezone.utc)) is None  # 13 Nov in NL
    assert all_auctions_trial({"all_auctions": {"enabled": False}}, NOW) is None
    assert all_auctions_trial({}, NOW) is None


def test_context_reads_other_auctions_only_during_the_trial():
    a = Auction(site="x", auction_id="1", title="Overstock fietsen", url="u")
    b = Auction(site="x", auction_id="2", title="Partijen gereedschap", url="u")
    off = SiteContext(http=None, now=NOW, is_bankruptcy=lambda *_: False)
    assert off.wants(a, True) and not off.wants(a, False) and not off.regular
    on = SiteContext(http=None, now=NOW, is_bankruptcy=lambda *_: False, all_auctions=True, max_regular=1)
    assert on.wants(a, False) and on.regular == {"Overstock fietsen"}
    assert not on.wants(b, False)  # at most one other auction per site
    assert on.wants(b, True) and on.regular == {"Overstock fietsen"}  # a bankruptcy auction is no "regular" one


def _row(regular: bool, deal: bool, priced: bool):
    lot = Lot(site="x", lot_id=str(id(object())), title="t", url="u", current_bid=10, closes_at=None, regular=regular)
    v = Verdict(is_deal=deal, reason="", bid=10, total_cost=12, resale=50 if priced else None)
    return (WatchItem(name="i", keywords=["t"]), lot, v, object() if priced else None)


def test_counts_per_kind_of_auction():
    rows = [_row(False, True, True), _row(False, False, False), _row(True, False, True)]
    assert trial_counts(rows) == {"special": {"lots": 2, "priced": 1, "deals": 1},
                                  "regular": {"lots": 1, "priced": 1, "deals": 0}}
    state = {}
    record_trial(state, rows, NOW, True, False)
    assert state["trial_counts"]["2026-10-29"]["regular"]["lots"] == 1
    record_trial(state, rows, datetime(2027, 1, 30, tzinfo=timezone.utc), False, False)
    assert "trial_counts" not in state  # forgotten after 60 days
