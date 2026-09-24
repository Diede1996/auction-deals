from scanner.commands import handle, parse_add


def test_parse_add_full():
    item = parse_add('playstation 5 | ps5 -controller -game max=250 profit=60 mp="ps5 console"')
    assert item.name == "playstation 5"
    assert item.keywords == ["playstation 5", "ps5"]
    assert item.exclude == ["controller", "game"]
    assert item.max_price == 250 and item.min_profit == 60
    assert item.marktplaats_query == "ps5 console"


def test_parse_add_simple():
    item = parse_add("iphone 15 pro")
    assert item.keywords == ["iphone 15 pro"] and item.exclude == [] and item.max_price is None
    assert parse_add("   ") is None
    assert parse_add("dyson price=€350").market_price == 350


def test_add_list_remove_flow():
    wl = {"settings": {"min_profit": 50}, "items": [{"name": "MacBook", "keywords": ["macbook"]}]}
    reply, changed = handle("/add iphone 15 -hoesje max=400", wl, "")
    assert changed and "Added" in reply and len(wl["items"]) == 2
    assert wl["items"][1] == {"name": "iphone 15", "keywords": ["iphone 15"], "exclude": ["hoesje"], "max_price": 400.0}

    reply, changed = handle("/add iphone 15 max=350", wl, "")
    assert changed and "Updated" in reply and len(wl["items"]) == 2 and wl["items"][1]["max_price"] == 350

    reply, _ = handle("/list", wl, "")
    assert "1. MacBook" in reply and "2. iphone 15" in reply

    reply, changed = handle("/remove 1", wl, "")
    assert changed and "MacBook" in reply and [i["name"] for i in wl["items"]] == ["iphone 15"]

    reply, changed = handle("/remove iPhone 15", wl, "")
    assert changed and wl["items"] == []

    reply, changed = handle("/remove 7", wl, "")
    assert not changed and "couldn't find" in reply


def test_sellat_and_old_return_command():
    wl = {"settings": {"target_return": 0.3, "min_profit": 25}, "items": []}
    reply, changed = handle("/sellat 50%", wl, "")
    assert changed and wl["settings"] == {"min_profit": 25, "resale_factor": 0.5}
    reply, changed = handle("/return 30", wl, "")
    assert not changed and "/sellat" in reply
    assert not handle("/sellat abc", wl, "")[1]


def test_minprofit_and_misc():
    wl = {"items": []}
    reply, changed = handle("/minprofit 75", wl, "")
    assert changed and wl["settings"]["min_profit"] == 75
    assert handle("/status", wl, "STATUS")[0] == "STATUS"
    assert "/add" in handle("/help", wl, "")[0]
    assert handle("/list@MyBot", wl, "")[0].startswith("Your watchlist is empty")
    assert not handle("hello", wl, "")[1]
    assert "don't know" in handle("/foo", wl, "")[0]
