"""Troostwijk lots from Troostwijk's own alert emails (the bot never visits troostwijkauctions.com)."""
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from conftest import FakeHttp, FakeResponse
from scanner.mail_alerts import Mailbox, TrackerResolver, collect, parse_email_html

NOW = datetime(2026, 9, 28, 4, 20, tzinfo=timezone.utc)
LOT1 = "https://www.troostwijkauctions.com/nl/l/makita-dhp484-accu-klopboormachine-A1-51234-17"
LOT2 = "https://www.troostwijkauctions.com/nl/l/dell-p2419h-24-inch-monitor-A1-51234-88"
LOT3 = "https://www.troostwijkauctions.com/nl/l/hugo-boss-kostuum-maat-52-A1-50999-3"

ALERT = f"""<html><body><p>Hallo, er zijn nieuwe kavels voor je zoekopdracht "makita".</p>
<table>
 <tr><td><a href="{LOT1}?utm_source=alert"><img src="https://media.tbauctions.com/x1.jpg" alt=""></a></td>
     <td><a href="{LOT1}?utm_source=alert">Makita DHP484 accu klopboormachine</a><br>
         Huidig bod: € 45<br>Sluit: 3 okt. 2026 19:00<br>Locatie: Purmerend<br>
         <a href="{LOT1}">Bekijk kavel</a></td></tr>
 <tr><td><a href="https://click.mail.example.net/ls/click?upn=abc123"><img src="https://media.tbauctions.com/x2.jpg" alt="Dell P2419H 24 inch monitor"></a></td>
     <td>Huidig bod: € 12<br><a href="https://click.mail.example.net/ls/click?upn=abc123">Bied nu</a></td></tr>
 <tr><td><a href="https://t.example.com/r?u=https%3A%2F%2Fwww.troostwijkauctions.com%2Fnl%2Fl%2Fhugo-boss-kostuum-maat-52-A1-50999-3%3Futm%3D1">Bekijk</a></td></tr>
</table><a href="https://www.troostwijkauctions.com/nl/account">Instellingen</a></body></html>"""


def tracker(m, u, kw):
    assert kw.get("allow_redirects") is False  # never follow on to the Troostwijk website
    return FakeResponse("")


class RedirectHttp(FakeHttp):
    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        assert kw.get("allow_redirects") is False and "troostwijkauctions.com" not in url
        resp = FakeResponse("")
        resp.headers = {"Location": LOT2 + "?utm_campaign=alert"} if "upn=abc123" in url else {}
        return resp


def test_parse_alert_email():
    http = RedirectHttp([])
    lots = {l["lot_id"]: l for l in parse_email_html(ALERT, TrackerResolver(http, {}))}
    assert set(lots) == {"A1-51234-17", "A1-51234-88", "A1-50999-3"}
    drill = lots["A1-51234-17"]
    assert drill["url"] == LOT1 and drill["title"] == "Makita DHP484 accu klopboormachine"  # not "Bekijk kavel"
    assert drill["bid"] == 45.0 and drill["closes"].startswith("2026-10-03T19:00") and drill["location"] == "Purmerend"
    dell = lots["A1-51234-88"]
    assert dell["url"] == LOT2 and dell["title"] == "Dell P2419H 24 inch monitor" and dell["bid"] == 12.0  # alt text
    assert lots["A1-50999-3"]["url"] == LOT3 and lots["A1-50999-3"]["title"] == "Hugo boss kostuum maat 52"  # from the link
    assert len(http.calls) == 1  # one tracker asked, once


def _mail(html, sender="Troostwijk Auctions <noreply@troostwijkauctions.com>", date="Sun, 27 Sep 2026 18:00:00 +0200",
          subject="Nieuwe kavels voor jouw zoekopdracht"):
    msg = EmailMessage()
    msg["From"], msg["Subject"], msg["Date"] = sender, subject, date
    msg.set_content("Bekijk de kavels online")
    msg.add_alternative(html, subtype="html")
    return msg


class FakeIMAP:
    mails: list = []

    def __init__(self, host):
        self.host = host

    def login(self, user, password):
        if password != "app-pass":
            raise OSError("[AUTHENTICATIONFAILED] Invalid credentials")

    def select(self, box, readonly=False):
        assert readonly  # the bot never changes the mailbox
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        return "OK", [" ".join(str(i + 1) for i in range(len(self.mails))).encode()]

    def fetch(self, num, what):
        return "OK", [(b"1 (RFC822)", self.mails[int(num) - 1].as_bytes())]

    def logout(self):
        pass


def test_collect_remembers_lots_until_they_close():
    FakeIMAP.mails = [
        _mail(ALERT),
        _mail("<p>Hoi, dit is geen veilingmail</p>", sender="Iemand <x@example.com>"),  # ignored
        _mail('<p>Nieuwe veilingen: <a href="https://www.troostwijkauctions.com/nl/a/faillissement-A1-1">bekijk</a></p>'),
    ]
    box = Mailbox("bot@gmail.com", "app-pass", imap_cls=FakeIMAP)
    state = {}
    lots, report = collect(box, RedirectHttp([]), state, NOW)
    assert report == {"emails": 3, "troostwijk_emails": 2, "unreadable": 0, "no_lots": 1, "new": 3, "lots": 3,
                      "other_auctions": 0, "unreadable_emails": []}  # an auction announcement without lots is fine
    drill = next(l for l in lots if l.lot_id == "A1-51234-17")
    assert drill.site == "troostwijk" and drill.current_bid == 45.0 and drill.location == "Purmerend"
    assert drill.auction_title == "Troostwijk alert of 27 Sep" and drill.key == "troostwijk:A1-51234-17"
    # nothing personal is stored: no subjects, senders or tracking links (the repository is public)
    assert "upn=abc123" not in str(state) and "Nieuwe kavels" not in str(state) and "noreply" not in str(state)

    # the next day there are no new emails: lots stay until they close, or 14 days without a closing time
    FakeIMAP.mails = []
    lots, report = collect(box, RedirectHttp([]), state, NOW + timedelta(days=1))
    assert report["lots"] == 3 and report["new"] == 0
    lots, _ = collect(box, RedirectHttp([]), state, NOW + timedelta(days=6))
    assert {l.lot_id for l in lots} == {"A1-51234-88", "A1-50999-3"}  # the drill closed on 3 Oct
    lots, _ = collect(box, RedirectHttp([]), state, NOW + timedelta(days=16))
    assert lots == []


def test_wrong_app_password_is_an_error():
    import pytest
    with pytest.raises(OSError):
        collect(Mailbox("bot@gmail.com", "wrong", imap_cls=FakeIMAP), FakeHttp([]), {}, NOW)


def test_mailbox_from_env(monkeypatch):
    monkeypatch.delenv("ALERTS_EMAIL", raising=False)
    assert Mailbox.from_env() is None
    monkeypatch.setenv("ALERTS_EMAIL", "bot@gmail.com")
    monkeypatch.setenv("ALERTS_APP_PASSWORD", "abcd efgh ijkl mnop")  # Google shows it with spaces
    box = Mailbox.from_env()
    assert box.password == "abcdefghijklmnop" and box.host == "imap.gmail.com"


# ---------------------------------------------------------------- a real Troostwijk auction alert (24 Sep 2026)
# Every link goes through Troostwijk's mailing service (Exponea): cdn.eu1.exponea.com/troostwijk-prod/e/.<zlib
# payload, base64>.<signature>/click. The payload holds the destination length-prefixed (protobuf), followed by
# a byte that looks like part of the URL. The tracking codes below are made up.
import base64
import zlib

from scanner.mail_alerts import decode_tracker


def exponea(url: str) -> str:
    raw = url.encode()
    size = bytes([len(raw)]) if len(raw) < 128 else bytes([(len(raw) & 0x7F) | 0x80, len(raw) >> 7])
    body = (b"\n\x0c" + b"c0ffee123456" + b"\x12\x08" + b"deadbeef" + b"\x1a" + size + raw +
            b"1\x87\xc5\x9b7:\xad\xdaA:\x1d\n\x02us\x12\x17bloomreach-auctionalert")
    token = base64.urlsafe_b64encode(zlib.compress(body)).rstrip(b"=").decode()
    return f"https://cdn.eu1.exponea.com/troostwijk-prod/e/.{token}.AbCdEfGh12345678/click"


TW = "https://www.troostwijkauctions.com"
BYLDIS = [("54", "Ahrend Duo zit-sta bureau"), ("145", "Ahrend Zit-sta bureau"), ("14", "Vergadertafel"),
          ("157", "Dossierkast"), ("124", "Ahrend Bureaustoel (2x)"), ("42", "Philips Bravia 65 inch Televisie"),
          ("195", "Ahrend Dossierkast (2x)"), ("48", "Ahrend Vergaderset"), ("117", "HP Elite E241i Monitor (2x)")]


def _card(num, title):
    link = exponea(f"{TW}/nl/l/A1-50252-{num}")
    return f"""<td><table><tr><td><a href="{link}"><img src="https://media.tbauctions.com/{num}.jpg" alt=""></a></td></tr>
      <tr><td><a href="{link}"><b>{title}</b></a></td></tr><tr><td>Veldhoven, NL</td></tr>
      <tr><td><table><tr><td>Startbod</td><td>€10</td></tr></table></td></tr>
      <tr><td><a href="{link}">Bekijk kavel</a></td></tr></table></td>"""


BYLDIS_HTML = f"""<div>---------- Forwarded message ---------<br>Van: Jasper van Leeuwen van Troostwijk Auctions
<span>&lt;<a href="mailto:no-reply@mail.troostwijkauctions.com">no-reply@mail.troostwijkauctions.com</a>&gt;</span><br>
Date: do 24 sep 2026, 11:09<br></div>
<table><tr><td><table><tr>
  <td><a href="{exponea(TW + '/?utm_content=Header_Logo')}"><img alt="Troostwijk"></a></td>
  <td><a href="{exponea(TW + '/auctions?utm_content=Header_all_auctions')}">Alle veilingen</a></td>
  <td><a href="{exponea('https://cdn.eu1.exponea.com/troostwijk-prod/e/Cgxmk0000view')}">Bekijk online</a></td>
</tr></table>
<table><tr><td><table>
  <tr><td><h2>Faillissement Byldis Prefab B.V. - Producent van modulaire gebouwen - Kantoorinventaris</h2></td></tr>
  <tr><td>A1-50252 - 
                        Veldhoven <img alt=""></td>
      <td><img alt=""> 2 okt <span>2026</span></td><td><img alt=""> 7 okt <span>2026</span></td></tr>
  <tr><td><a href="{exponea(TW + '/nl/a/A1-50252')}">Bekijk veiling nu</a></td></tr>
</table></td></tr>
<tr><td><table style="display:none"><tr><td>Veldhoven</td></tr><tr><td>A1-50252</td></tr>
  <tr><td>2 okt 2026</td><td>7 okt 2026</td></tr>
  <tr><td><a href="{exponea(TW + '/nl/a/A1-50252')}">Bekijk veiling nu</a></td></tr></table></td></tr>
<tr><td><table><tr>{''.join(_card(*c) for c in BYLDIS[:3])}</tr><tr>{''.join(_card(*c) for c in BYLDIS[3:6])}</tr>
  <tr>{''.join(_card(*c) for c in BYLDIS[6:])}</tr></table></td></tr></table>
<table><tr><td>Je ontvangt deze e-mail omdat je je hebt ingeschreven voor updates van Troostwijk Auctions.
  <a href="{exponea('https://cdn.eu1.exponea.com/troostwijk-prod/e/Cgxmk0000/consent?lang=nl')}">Uitschrijven</a>
  <a href="{exponea('https://www.facebook.com/TroostwijkAuctions/?utm_content=Footer_SM_Facebook')}"><img alt="fb"></a>
</td></tr></table></td></tr></table>"""


class NoRequests(FakeHttp):
    def request(self, method, url, **kw):
        raise AssertionError(f"the bot must not open {url}")


def test_tracking_links_are_read_without_opening_them():
    assert decode_tracker(exponea(f"{TW}/nl/l/A1-50252-54")) == (True, f"{TW}/nl/l/A1-50252-54")  # not ...-541
    assert decode_tracker(exponea(f"{TW}/nl/a/A1-50252")) == (True, f"{TW}/nl/a/A1-50252")
    assert decode_tracker(exponea("https://cdn.eu1.exponea.com/x/consent?lang=nl" + "&p=" + "q" * 150)) == (True, None)
    assert decode_tracker("https://click.mail.example.net/ls/click?upn=abc123") == (False, None)


def test_real_auction_alert_forwarded_by_hand():
    lots = {l["lot_id"]: l for l in parse_email_html(BYLDIS_HTML, TrackerResolver(NoRequests([]), {}))}
    assert set(lots) == {f"A1-50252-{n}" for n, _ in BYLDIS}
    hp = lots["A1-50252-117"]
    assert hp["url"] == f"{TW}/nl/l/A1-50252-117" and hp["title"] == "HP Elite E241i Monitor (2x)"
    assert hp["bid"] == 10.0 and hp["location"] == "Veldhoven" and hp["image"].endswith("/117.jpg")
    # the lots close on the auction's closing day; the email doesn't say at what time
    assert hp["closes"] is None and hp["closes_day"] == "2026-10-07"
    assert hp["auction"] == "Faillissement Byldis Prefab B.V. - Producent van modulaire gebouwen - Kantoorinventaris"
    assert lots["A1-50252-14"]["title"] == "Vergadertafel"


def test_collect_forwarded_auction_alert():
    FakeIMAP.mails = [_mail(BYLDIS_HTML, sender="Diede <me@gmail.com>", date="Mon, 28 Sep 2026 17:16:00 +0200")]
    box = Mailbox("bot@gmail.com", "app-pass", imap_cls=FakeIMAP)
    state = {}
    lots, report = collect(box, NoRequests([]), state, NOW + timedelta(days=1))
    assert report == {"emails": 1, "troostwijk_emails": 1, "unreadable": 0, "no_lots": 0, "new": 9, "lots": 9,
                      "other_auctions": 0, "unreadable_emails": []}
    hp = next(l for l in lots if l.lot_id == "A1-50252-117")
    assert hp.closes_at is None and hp.location == "Veldhoven" and hp.current_bid == 10.0
    assert hp.auction_title.endswith("Kantoorinventaris") and hp.closes_day == "2026-10-07"
    assert "exponea" not in str(state)  # no tracking links in the public repository
    FakeIMAP.mails = []
    assert len(collect(box, NoRequests([]), state, datetime(2026, 10, 7, 21, 0, tzinfo=timezone.utc))[0]) == 9
    assert collect(box, NoRequests([]), state, datetime(2026, 10, 7, 22, 30, tzinfo=timezone.utc))[0] == []  # 8 Oct


# An auction alert for a business sale (not a bankruptcy), 1 Oct 2026. The template has HTML comments such as
# "<!-- &#9472;&#9472; DESKTOP SUB-HEADER &#9472;&#9472; -->" right before the auction id.
COMPUTERS = [("11594", "Apple MacBook Pro 16\u201d, Apple M1 Max, 32 GB RAM, 1 TB NVMe Laptop", "50"),
             ("11596", "HP ZBook Firefly G10 14\u201d, Core(TM) i7 13th Gen, 32 GB RAM", "10")]


def _computer_card(num, title, bid):
    link = exponea(f"{TW}/nl/l/A1-38890-{num}")
    return f"""<td><a href="{link}"><img src="https://media.tbauctions.com/{num}.jpg" alt=""></a>
      <a href="{link}">{title}</a><br>Loon op zand, NL<br><span>Startbod</span> <span>€{bid}</span>
      <a href="{link}">Bekijk kavel</a></td>"""


COMPUTERS_HTML = f"""<table><tr><td><a href="{exponea(TW + '/auctions')}">Alle veilingen</a></td></tr>
<tr><td><div>Computers, Tablets, Desktops, Laptops, Smartphones &amp; Accessories</div>
<!-- &#9472;&#9472; DESKTOP SUB-HEADER &#9472;&#9472; -->
<table><tr><td>A1-38890</td><td>Loon op zand <img alt=""></td></tr>
<tr><td><img alt=""> 13 okt 2026</td><td><img alt=""> 14 okt 2026</td></tr></table>
<a href="{exponea(TW + '/nl/a/A1-38890')}">Bekijk veiling nu</a></td></tr>
<tr><td><table><tr>{''.join(_computer_card(*c) for c in COMPUTERS)}</tr></table></td></tr></table>"""


def test_auction_name_skips_html_comments():
    lots = {l["lot_id"]: l for l in parse_email_html(COMPUTERS_HTML, TrackerResolver(NoRequests([]), {}))}
    mac = lots["A1-38890-11594"]
    assert mac["auction"] == "Computers, Tablets, Desktops, Laptops, Smartphones & Accessories"
    assert mac["bid"] == 50.0 and mac["location"] == "Loon op zand" and mac["closes_day"] == "2026-10-14"


def test_only_bankruptcy_auctions_and_the_bid_is_from_the_email():
    from scanner.matching import bankruptcy_matcher
    FakeIMAP.mails = [
        _mail(BYLDIS_HTML, sender="Diede <me@gmail.com>", date="Mon, 28 Sep 2026 17:16:00 +0200",
              subject="Fwd: Faillissement Byldis Prefab B.V. - Producent van modulaire gebouwen - Kantoorinventaris"),
        _mail(COMPUTERS_HTML, date="Thu, 01 Oct 2026 09:00:00 +0200",
              subject="Computers, tablets, desktops, laptops, smartphones en accessoires"),
    ]
    box = Mailbox("bot@gmail.com", "app-pass", imap_cls=FakeIMAP)
    is_bankruptcy = bankruptcy_matcher(["faillissement", "curator"])
    state = {}
    lots, report = collect(box, NoRequests([]), state, NOW + timedelta(days=4), is_bankruptcy=is_bankruptcy,
                           only_bankruptcy=True)
    assert len(lots) == 9 and report["other_auctions"] == 2  # the computer sale is not a bankruptcy
    assert all(l.bid_from_email for l in lots)  # "Startbod €10" is not the current bid
    lots, report = collect(box, NoRequests([]), state, NOW + timedelta(days=4), is_bankruptcy=is_bankruptcy)
    assert len(lots) == 11 and report["other_auctions"] == 0
    mac = next(l for l in lots if l.lot_id == "A1-38890-11594")
    assert mac.auction_title.startswith("Computers, Tablets") and mac.current_bid == 50.0


def test_subject_names_the_auction_when_the_page_doesnt():
    html = COMPUTERS_HTML.replace("<div>Computers, Tablets, Desktops, Laptops, Smartphones &amp; Accessories</div>", "")
    FakeIMAP.mails = [_mail(html, subject="FW: Faillissement Jansen Computers")]
    lots, _ = collect(Mailbox("b", "app-pass", imap_cls=FakeIMAP), NoRequests([]), {}, NOW,
                      is_bankruptcy=lambda t: "faillissement" in t.lower(), only_bankruptcy=True)
    assert len(lots) == 2 and lots[0].auction_title == "Faillissement Jansen Computers"


def test_emails_without_lots_are_named_once():
    FakeIMAP.mails = [
        _mail("<p>Je zoekopdracht 'macbook' is opgeslagen. Beheer je zoekopdrachten in je account.</p>",
              subject="Je zoekopdracht is opgeslagen", date="Thu, 01 Oct 2026 20:15:00 +0200"),
        _mail(BYLDIS_HTML),
    ]
    box = Mailbox("bot@gmail.com", "app-pass", imap_cls=FakeIMAP)
    state = {}
    _, report = collect(box, NoRequests([]), state, NOW + timedelta(days=4))
    assert report["unreadable"] == 1 and len(report["unreadable_emails"]) == 1
    assert report["unreadable_emails"][0]["subject"] == "Je zoekopdracht is opgeslagen"
    assert "opgeslagen" not in str(state)  # only a hash is remembered (public repository)
    _, report = collect(box, NoRequests([]), state, NOW + timedelta(days=5))
    assert report["unreadable"] == 1 and report["unreadable_emails"] == []  # not again the next morning
