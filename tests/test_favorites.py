import json
from datetime import datetime, timedelta, timezone

from conftest import FakeHttp, url_has
from scanner.favorites import MARKER, FavoritesStore, due_reminders, parse_body

NOW = datetime(2026, 9, 28, 17, 30, tzinfo=timezone.utc)  # 19:30 in Amsterdam


def body(favs):
    return f"{MARKER}\n⭐ Favorites\n\n```json\n{json.dumps(favs)}\n```\n"


FAVS = [
    {"key": "hnvi:196601", "title": "Beeldscherm 27 inch ACER RG270", "url": "https://www.hnvi.nl/veiling-kavel/x/196601",
     "closes": "2026-09-28T20:05:00+02:00", "maxBid": 45},
    {"key": "proveiling:3285252", "title": "Philips HD7695/90", "url": "https://www.proveiling.nl/x/3285252/detail",
     "closes": "2026-09-29T20:35:00+02:00"},
]


def test_parse_body_keeps_only_safe_entries():
    evil = FAVS + [
        {"key": "x:1", "title": "phishing", "url": "https://evil.example.com/login"},  # not an auction site
        {"key": "x:2", "title": "js", "url": "javascript:alert(1)"},
        {"key": "bad key!", "title": "t", "url": "https://www.hnvi.nl/a"},
        FAVS[0],  # duplicate
        "nonsense",
    ]
    favs = parse_body(body(evil))
    assert [f["key"] for f in favs] == ["hnvi:196601", "proveiling:3285252"]
    assert favs[0]["maxBid"] == 45
    assert parse_body("no json here") == [] and parse_body(body([])) == []


def test_store_reads_the_owners_issue_only():
    issues = [
        {"number": 7, "body": body([{"key": "hnvi:1", "title": "stranger", "url": "https://www.hnvi.nl/1"}]),
         "user": {"login": "someone-else"}},  # anyone can open an issue on a public repository
        {"number": 3, "body": body(FAVS), "user": {"login": "Diede1996"}},
        {"number": 1, "body": "unrelated issue", "user": {"login": "diede1996"}},
    ]
    http = FakeHttp([(url_has("api.github.com/repos/diede1996/auction-deals/issues"), issues)])
    store = FavoritesStore(http, "diede1996/auction-deals", "tok")
    favs = store.load()
    assert store.issue == 3 and [f["key"] for f in favs] == ["hnvi:196601", "proveiling:3285252"]
    assert http.calls[0][2]["headers"]["Authorization"] == "Bearer tok"


def test_store_without_repository_or_access():
    assert FavoritesStore(FakeHttp([]), "", "").load() == []

    def denied(m, u, kw):
        raise RuntimeError("HTTP 404")

    store = FavoritesStore(FakeHttp([(url_has("api.github.com"), denied)]), "a/b", "")
    assert store.load() == [] and store.error


def test_reminders_once_per_closing_time():
    favs = parse_body(body(FAVS))
    lots = {"hnvi:196601": {"closes": "2026-09-28T20:05:00+02:00"}}  # latest closing time from the scan
    due = due_reminders(favs, lots, NOW, 60, {})
    assert [f["key"] for f, _ in due] == ["hnvi:196601"]  # closes in 35 minutes; the other one tomorrow
    reminded = {"hnvi:196601": due[0][1].isoformat()}
    assert due_reminders(favs, lots, NOW + timedelta(minutes=15), 60, reminded) == []
    # extended closing time (someone bid at the last minute) -> remind again when it's close
    lots["hnvi:196601"]["closes"] = "2026-09-28T20:25:00+02:00"
    assert len(due_reminders(favs, lots, NOW + timedelta(minutes=15), 60, reminded)) == 1
    # already closed: no reminder
    assert due_reminders(favs, lots, NOW + timedelta(hours=2), 60, {}) == []
