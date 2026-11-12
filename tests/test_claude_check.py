import json
from types import SimpleNamespace as NS

from scanner import claude_check
from scanner.marktplaats import PriceEstimate


def est():
    listings = [{"price": p, "title": t} for p, t in
                [(150, "Kärcher K5 compact"), (180, "Kärcher K5 premium"), (400, "Kärcher HD 5/15 professional"),
                 (450, "Kärcher HD 6/13")]]
    return PriceEstimate(median=290, low=170, high=420, count=5, query="k5", prices=[150, 180, 400, 450, 500],
                         listings=listings)


class Client:
    def __init__(self, reply=None, fail=False):
        self.reply, self.fail, self.calls = reply, fail, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("down")
        return NS(stop_reason="end_turn", content=[NS(type="text", text=json.dumps(self.reply))])


LOT = NS(title="Kärcher K5", current_bid=60, description="werkt prima", key="mp:1")


def test_checked_estimate_uses_only_kept_and_drops_unseen_when_shown_all():
    e = claude_check.checked_estimate(est(), [0, 1])
    assert e.prices == [150, 180, 500] and e.median == 180 and e.count == 3


def test_too_few_kept_gives_none():
    one = PriceEstimate(median=1, low=1, high=1, count=1, query="", prices=[150], listings=[{"price": 150}])
    assert claude_check.checked_estimate(one, [0]) is None
    assert claude_check.checked_estimate(est(), []) is None
    assert claude_check.checked_estimate(est(), [99]) is None


def test_check_reprices_and_notes():
    seen = []
    def reeval(item, lot, e):
        seen.append(e.median)
        return NS(is_deal=True)
    out, notes = claude_check.check([("i", LOT, NS(is_deal=True), est())], reeval,
                                    Client({"keep": [0, 1], "verdict": "kopen", "reason": "Klopt."}))
    assert len(out) == 1 and seen == [180] and notes == {"mp:1": "kopen: Klopt."}


def test_check_drops_niet_and_no_longer_deal():
    out, _ = claude_check.check([("i", LOT, NS(), est())], lambda *a: NS(is_deal=True),
                                Client({"keep": [0, 1], "verdict": "niet", "reason": "Defect."}))
    assert out == []
    out, _ = claude_check.check([("i", LOT, NS(), est())], lambda *a: NS(is_deal=False),
                                Client({"keep": [0, 1], "verdict": "kopen", "reason": ""}))
    assert out == []


def test_api_failure_or_no_key_sends_unchecked(monkeypatch):
    found = [("i", LOT, NS(), est())]
    out, notes = claude_check.check(found, lambda *a: None, Client(fail=True))
    assert out == found and notes == {}
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert claude_check.check(found, lambda *a: None) == (found, {})
