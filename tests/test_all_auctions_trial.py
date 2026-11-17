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


def test_auction_with_a_favorite_is_always_read():
    # BellAuction, Nov 2026: Bosch tools starred during the trial dropped out of the scan when the trial ended
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from scanner.scan import favorite_auctions
    from scanner.sites.base import SiteContext
    now = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
    favs = [{"key": "bellauction:1", "site": "bellauction", "auction": "Elektrisch gereedschap en machines",
             "closes": "2026-11-19T18:48:00+00:00"},
            {"key": "bellauction:2", "site": "bellauction", "auction": "Oude veiling", "closes": "2026-11-01T18:00:00+00:00"}]
    fav = favorite_auctions(favs, now)
    assert fav == {"bellauction": {"Elektrisch gereedschap en machines"}}
    ctx = SiteContext(http=None, now=now, is_bankruptcy=lambda *a: False, favorite_auctions=fav["bellauction"])
    assert ctx.wants(SimpleNamespace(title="Elektrisch gereedschap en machines"), False)
    assert not ctx.wants(SimpleNamespace(title="Oude veiling"), False)
    assert (ctx.auctions_seen, ctx.auctions_read) == (2, 1)
