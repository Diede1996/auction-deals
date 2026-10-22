"""Your stock in Telegram: /koop, /verkocht, /prijs, /voorraad, /winst, price reminders, encrypted storage."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from scanner import inventory
from scanner.bot import run_commands
from scanner.marktplaats import PriceEstimate
from scanner.models import WatchItem

from test_main import FakeTelegram, repo  # noqa: F401  (pytest fixture)

NOW = datetime(2026, 10, 14, 6, 0, tzinfo=timezone.utc)  # 08:00 in Amsterdam
ITEMS = [WatchItem(name="Monitor", keywords=["monitor"]), WatchItem(name="Smoking", keywords=["smoking"])]


def est(median):
    return PriceEstimate(median=median, low=median * 0.8, high=median * 1.2, count=7, query="dell u2719d")


def ctx(now=NOW, prices=None):
    prices = prices or {}
    return inventory.Context(items=ITEMS, resale_factor=0.75, now=now,
                             price_of=lambda name, item: est(prices[name]) if name in prices else None)


def test_nice_price_and_suggestion():
    assert [inventory.nice_price(x) for x in (38, 123, 487, 1234)] == [40, 125, 490, 1250]
    assert inventory.suggest(45, 120, 0.75) == (120, 90)  # ask the median, accept 75% of it
    assert inventory.suggest(100, 120, 0.75) == (120, 100)  # never below what you paid
    assert inventory.suggest(45, None, 0.75) == (None, None)


def test_buy_sell_and_profit():
    inv = {"items": [], "next": 1}
    reply, changed = inventory.handle("/koop Dell U2719D monitor | 45 tijd=1,5u", inv, ctx(prices={"Dell U2719D monitor": 120}))
    assert changed and "#1" in reply and "soort: Monitor" in reply and "Vraag <b>€120</b>" in reply
    assert "niet onder <b>€90</b>" in reply and "Vraagprijs € 120" in reply and "<code>" in reply
    assert inv["items"][0]["minutes"] == 90 and inv["items"][0]["median"] == 120

    reply, _ = inventory.handle("/koop Smoking Hugo Boss maat 52 | 20", inv, ctx())
    assert "Geen vergelijkbare advertenties" in reply and "/prijs 2 80" in reply
    reply, _ = inventory.handle("/prijs 2 75", inv, ctx())
    assert "€75" in reply and inv["items"][1]["ask"] == 75

    later = NOW + timedelta(days=6)
    reply, _ = inventory.handle("/verkocht 1 95 tijd=30", inv, ctx(later))
    assert "winst <b>€50</b> in 6 dagen" in reply and "€25 per uur" in reply  # €50 in 2 hours
    reply, _ = inventory.handle("/verkocht #2 €70", inv, ctx(later))
    assert "€50" in reply and "tijd=30" in reply  # asks for the time next time

    report = inventory.profit_report(inv, 0.75, later)
    assert "<b>Monitor</b>: 1 verkocht · winst €50 (111%) · 6 dagen tot verkoop · <b>€25/uur</b>" in report
    assert "<b>Smoking</b>: 1 verkocht" in report and "<b>Alles</b>: 2 verkocht · winst €100" in report
    assert "79% van de Marktplaats-mediaan" in report and "Na 5 verkopen" in report

    reply, _ = inventory.handle("/voorraad", inv, ctx(later))
    assert "niets te koop" in reply
    reply, changed = inventory.handle("/weg 2", inv, ctx(later))
    assert changed and len(inv["items"]) == 1


def test_sellat_advice_after_five_sales():
    inv = {"items": [], "next": 1}
    for n in range(5):
        inventory.handle(f"/koop Monitor {n} | 40", inv, ctx(prices={f"Monitor {n}": 100}))
        inventory.handle(f"/verkocht {n + 1} 60", inv, ctx())
    assert "/sellat 60" in inventory.profit_report(inv, 0.75, NOW)


def test_bad_input_changes_nothing():
    inv = {"items": [], "next": 1}
    for text in ("/koop monitor", "/koop | 45", "/verkocht 9 50", "/verkocht", "/prijs 1", "/weg 4"):
        reply, changed = inventory.handle(text, inv, ctx())
        assert not changed and "Zo werkt het" in reply or "Welke" in reply
    assert inv == {"items": [], "next": 1}
    assert inventory._money("1.250") == 1250 and inventory._money("€ 45,50") == 45.5


def test_reminder_to_lower_the_price():
    inv = {"items": [], "next": 1}
    inventory.handle("/koop Dell monitor | 45", inv, ctx(prices={"Dell monitor": 120}))
    assert inventory.due_nudges(inv, NOW + timedelta(days=9)) == []
    due = inventory.due_nudges(inv, NOW + timedelta(days=10))
    assert [d["next_ask"] for d in due] == [110]
    assert "/prijs 1 110" in inventory.nudge_text(due, NOW + timedelta(days=10))
    inv["items"][0]["nudged"] = (NOW + timedelta(days=10)).isoformat()
    assert inventory.due_nudges(inv, NOW + timedelta(days=12)) == []  # once a week
    inv["items"][0]["ask"] = 90  # already at the floor
    due = inventory.due_nudges(inv, NOW + timedelta(days=17))
    assert due[0]["next_ask"] is None and "bodemprijs" in inventory.nudge_text(due, NOW)


def test_encrypted_and_key_checked(tmp_path):
    path = tmp_path / "inv.enc"
    inventory.save(path, {"items": [{"id": 1, "name": "Geheime monitor", "paid": 45}], "next": 2}, "secret")
    assert b"Geheime" not in path.read_bytes()
    assert inventory.load(path, "secret")["items"][0]["name"] == "Geheime monitor"
    with pytest.raises(inventory.InventoryKeyError):
        inventory.load(path, "other")


def test_bot_keeps_stock_private(repo):  # noqa: F811
    tg = FakeTelegram([{"update_id": 1, "message": {"chat": {"id": 42}, "text": "/koop Samsung monitor 27 | 35"}},
                       {"update_id": 2, "message": {"chat": {"id": 42}, "text": "/voorraad"}},
                       {"update_id": 3, "message": {"chat": {"id": 42}, "text": "/help"}}])
    # http_cls returns None: the Marktplaats lookup fails quietly and the item is still saved
    assert run_commands(repo, NOW, http_cls=lambda **kw: None, telegram_cls=tg) == 0
    text = "\n".join(tg.sent)
    assert "#1" in text and "Samsung monitor 27" in text and "/voorraadhelp" in text
    enc = (repo / "data" / "inventory.enc").read_bytes()
    assert b"Samsung" not in enc and "Samsung" not in (repo / "data" / "telegram.json").read_text()
    assert inventory.load(repo / "data" / "inventory.enc", "123:abc")["items"][0]["paid"] == 35


def test_bot_sends_price_reminder_once_a_day(repo):  # noqa: F811
    inventory.save(repo / "data" / "inventory.enc", {"next": 2, "items": [
        {"id": 1, "name": "Dell monitor", "paid": 45, "bought": (NOW - timedelta(days=12)).isoformat(),
         "listed": (NOW - timedelta(days=12)).isoformat(), "ask": 120, "floor": 90, "category": "Monitor"}]},
        "123:abc")
    (repo / "data" / "telegram.json").write_text(json.dumps({"offset": 1, "welcomed": "x"}))
    tg = FakeTelegram()
    morning = NOW + timedelta(hours=1)  # 09:00 in Amsterdam
    run_commands(repo, NOW, http_cls=lambda **kw: None, telegram_cls=tg)  # 08:00: not yet
    assert tg.sent == []
    run_commands(repo, morning, http_cls=lambda **kw: None, telegram_cls=tg)
    assert len(tg.sent) == 1 and "/prijs 1 110" in tg.sent[0]
    run_commands(repo, morning + timedelta(minutes=5), http_cls=lambda **kw: None, telegram_cls=tg)
    assert len(tg.sent) == 1


def test_wrong_key_never_overwrites(repo, monkeypatch):  # noqa: F811
    inventory.save(repo / "data" / "inventory.enc", {"items": [], "next": 7}, "old-token")
    before = (repo / "data" / "inventory.enc").read_bytes()
    tg = FakeTelegram([{"update_id": 1, "message": {"chat": {"id": 42}, "text": "/koop x | 5"}}])
    run_commands(repo, NOW + timedelta(hours=3), http_cls=lambda **kw: None, telegram_cls=tg)
    assert "sleutel is veranderd" in tg.sent[0]
    assert (repo / "data" / "inventory.enc").read_bytes() == before
    monkeypatch.setenv("INVENTORY_KEY", "old-token")
    tg = FakeTelegram([{"update_id": 2, "message": {"chat": {"id": 42}, "text": "/koop x | 5"}}])
    run_commands(repo, NOW + timedelta(hours=3), http_cls=lambda **kw: None, telegram_cls=tg)
    assert "#7" in tg.sent[0]


def test_buy_a_whole_auction_in_one_message():
    inv = {"items": [], "next": 1}
    text = "/koop\nSmoking HUGO maat 52 | 130,60\n/koop Colbert HUGO Arti | 65\nzonder prijs\n\n"
    reply, changed = inventory.handle(text, inv, ctx(prices={"Smoking HUGO maat 52": 200}))
    assert changed and [i["name"] for i in inv["items"]] == ["Smoking HUGO maat 52", "Colbert HUGO Arti"]
    assert inv["items"][0]["paid"] == 130.6 and inv["items"][0]["category"] == "Smoking"
    assert "<b>2 aankopen</b>" in reply and "vraag €200" in reply and "/prijs 2 80" in reply
    assert "Niet gelezen" in reply and "zonder prijs" in reply
    reply, changed = inventory.handle("/koop\nniets\nleesbaars", inv, ctx())
    assert not changed and "geen regel" in reply
