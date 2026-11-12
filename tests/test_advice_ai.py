import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS

from scanner import advice, advice_ai
from tests.test_advice import SETTINGS, lot

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


class Client:
    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []
        self.messages = self

    def create(self, **kw):
        self.prompts.append(kw["messages"][0]["content"])
        r = self.replies.pop(0)
        return NS(stop_reason="end_turn", content=[NS(type="text", text=json.dumps(r))])


def J(keep, verdict="kopen"):
    return {"modelKnown": True, "keep": keep, "verdict": verdict, "model": "m", "confidence": "hoog", "reason": "r", "risks": "",
            "dropUnseen": False}


class Http:
    def __init__(self, listings):
        self.listings, self.urls = listings, []

    def json(self, url):
        self.urls.append(url)
        return {"listings": self.listings}


def li(cents, title, item="m1"):
    return {"itemId": item, "title": title, "priceInfo": {"priceType": "FIXED", "priceCents": cents},
            "vipUrl": f"/v/x/{item}"}


def setup(tmp_path, lots):
    root, priv = tmp_path / "pub", tmp_path / "priv"
    (root / "data").mkdir(parents=True)
    (root / "data" / "lots.json").write_text(json.dumps({"settings": SETTINGS, "lots": lots, "favorites": {}}))
    return root, priv


def test_marktplaats_lots_and_identical_ones(tmp_path):
    a = lot(maxBid=80.0)
    b = lot(key="s:2", maxBid=80.0)  # same listings: judged once
    root, priv = setup(tmp_path, [a, b])
    client = Client([J([1, 2])])
    j = advice_ai.judge(root, priv, NOW, client, http=Http([]))
    assert j["s:1"]["keep"] == [1, 2] and j["s:1"]["source"] == "marktplaats" and j["s:2"] == {"sameAs": "s:1"}
    assert len(client.prompts) == 1 and "0. €100 accu" in client.prompts[0]
    res = advice.apply(root, priv, j, NOW)
    assert res["lots"]["s:1"]["price"] == 210.0 and res["lots"]["s:1"]["source"] == "marktplaats"


def test_2dehands_for_a_lot_without_marktplaats_price(tmp_path):
    c = lot(key="s:3", mp=None, mpPlan={"kind": "exact", "brand": "makita", "model": "4350T",
                                        "search": "https://www.marktplaats.nl/q/makita+4350t/"})
    root, priv = setup(tmp_path, [c])
    http = Http([li(9000, "Makita 4350T decoupeerzaag", "m1"), li(11000, "Makita 4350T", "m2"),
                 li(3000, "zaagbladen voor makita", "m3"), li(20000, "Makita 4350T winkel", "a9")])  # a9: business
    client = Client([J([1, 2])])
    j = advice_ai.judge(root, priv, NOW, client, http=http)
    assert "query=makita+4350t" in http.urls[0] and "2dehands.be" in http.urls[0]
    assert j["s:3"]["source"] == "2dehands" and [x["price"] for x in j["s:3"]["listings"]] == [90.0, 110.0]
    res = advice.apply(root, priv, j, NOW)
    assert res["lots"]["s:3"]["price"] == 100.0 and res["lots"]["s:3"]["source"] == "2dehands"


def test_query_of():
    assert advice_ai.query_of({"mpPlan": {"kind": "general"}}) is None
    assert advice_ai.query_of({"mpPlan": {"kind": "exact", "model": "X1", "search": "https://www.marktplaats.nl/q/bosch+x1/"}}) == "bosch x1"


def test_unknown_model_keeps_nothing():
    j = advice_ai.ask(Client([dict(J([0, 1]), modelKnown=False)]), "x")
    assert j["keep"] == [] and j["verdict"] == "twijfel"
