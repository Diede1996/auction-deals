"""The bid you'd have to place now, not the current bid."""
from datetime import datetime, timezone

from scanner.bidding import bid_step, fill_next_bids, parse_steps
from scanner.details import fill_start_prices, start_price_from_page
from scanner.evaluate import Fees, Settings, evaluate
from scanner.marktplaats import PriceEstimate
from scanner.models import Lot, WatchItem
from scanner.sites import onlineveilingmeester, plaatsjebod

NOW = datetime(2026, 10, 4, 13, 0, tzinfo=timezone.utc)


def lot(bid, bids, **kw):
    return Lot("proveiling", "1", "Dell U2419H", "https://x/1", bid, None, bids=bids, **kw)


def test_steps():
    steps = parse_steps(None)
    assert [bid_step(a, steps) for a in (0, 15, 20, 99, 100, 750, 2500, 9000)] == [1, 1, 5, 5, 10, 25, 50, 100]
    assert parse_steps([[0, 2], [50, 10]]) == [(0.0, 2.0), (50.0, 10.0)]


def test_next_bid():
    no_bids, bid_on, unknown, email = lot(15, 0), lot(70, 3), lot(135, None), lot(10, None, bid_from_email=True)
    fill_next_bids([no_bids, bid_on, unknown, email])
    assert no_bids.next_bid == 15 and not no_bids.step_estimated  # nobody has bid: the starting bid
    assert bid_on.next_bid == 75 and bid_on.step_estimated
    assert unknown.next_bid == 145  # number of bids unknown: counted as bid on, to be safe
    assert email.next_bid is None


def test_money_is_worked_out_for_the_next_bid():
    item = WatchItem(name="Monitor", keywords=["monitor"])
    est = PriceEstimate(median=100, low=90, high=110, count=6, query="dell u2419h")
    settings, fees = Settings(min_margin=0.30, resale_factor=0.85), Fees(premium=0.0, vat=0.0)
    # resale 85 -> max total 65.38 -> max bid 65
    at_max = lot(60, 2)
    fill_next_bids([at_max])
    v = evaluate(item, at_max, fees, settings, est)
    assert v.bid == 65 and v.max_bid == 65 and v.is_deal  # the next bid (60 + 5) is exactly the max: still ok
    over = lot(61, 2)
    fill_next_bids([over])
    assert not evaluate(item, over, fees, settings, est).is_deal  # 61 < 65, but the next bid is 66


def test_sites_that_say_the_step():
    from fixtures import OVM_AUCTIONS, OVM_LOT
    auction = onlineveilingmeester.parse_auctions(OVM_AUCTIONS)[0]
    assert onlineveilingmeester.parse_lot(OVM_LOT, auction).next_bid == 81  # hoogsteBod 76 + verhoging 5
    fresh = onlineveilingmeester.parse_lot({**OVM_LOT, "hoogsteBod": 0, "aantalBiedingen": 0}, auction)
    assert fresh.current_bid == 10 and fresh.next_bid == 10  # the opening bid
    from fixtures import pjb_lot
    from scanner.models import Auction
    a = Auction("plaatsjebod", "1", "Faillissement x", "u")
    lots = plaatsjebod.parse_lots(pjb_lot(1, "monitor", "Dell monitor", "120,00", bids=8), a)
    assert lots[0].current_bid == 120 and lots[0].next_bid == 130  # "Verhoging: € 10"


INV_PAGE = """<div class="col-main"><h2>Een Philips brilliance 24 inch monitor</h2>
<p>Een Philips brilliance 24 inch monitor model: 241B8Q</p>
<table><tr><td>Resterende tijd: 3 dagen</td></tr><tr><td>€ 15,00</td></tr>
<tr><td>Aantal biedingen:</td><td>0</td></tr><tr><td>Laatste bod:</td><td>€ 0,00</td></tr>
<tr><td>Startprijs</td><td>€ 15,00</td></tr><tr><td>Einde:</td><td>8-10-2026 13:00</td></tr></table></div>"""


def test_start_price_from_the_lot_page():
    assert start_price_from_page(INV_PAGE) == 15.0
    assert start_price_from_page(INV_PAGE.replace("<td>0</td>", "<td>2</td>")) is None  # bid on: the list shows it

    class Http:
        calls = 0

        def text(self, url):
            Http.calls += 1
            return INV_PAGE

    philips = Lot("inventarisveilingen", "3439", "Een Philips brilliance 24 inch monitor", "https://www.inventarisveilingen.nl/p/3439/", None, None)
    cache: dict = {}
    item = WatchItem(name="Monitor", keywords=["monitor"])
    assert fill_start_prices([(item, philips)], Http, cache, NOW) == 1
    assert philips.current_bid == 15 and philips.next_bid == 15 and philips.bids == 0
    again = Lot("inventarisveilingen", "3439", "Een Philips brilliance 24 inch monitor", "https://www.inventarisveilingen.nl/p/3439/", None, None)
    assert fill_start_prices([(item, again)], Http, cache, NOW) == 0 and again.current_bid == 15 and Http.calls == 1
