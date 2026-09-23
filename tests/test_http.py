import pytest
from scanner.http import BlockedError, FetchError, Http

BLOCK_PAGE = """<html><body><h1>Toegang is tijdelijk beperkt</h1>
<p>Waarom deze blokkade? Iets in het gedrag van de browser trok onze aandacht.</p></body></html>"""


class Resp:
    def __init__(self, status, text):
        self.status_code, self.text = status, text

    def json(self):
        import json
        return json.loads(self.text)


def client(responses):
    http = Http(delay=0, retries=2)
    sent = []

    def request(method, url, **kw):
        sent.append(url)
        return responses.pop(0)

    http.session.request = request
    return http, sent


@pytest.mark.parametrize("resp", [Resp(403, "Forbidden"), Resp(429, "Too many"), Resp(200, BLOCK_PAGE)])
def test_block_is_never_retried_and_sticks_for_the_run(resp):
    http, sent = client([resp, Resp(200, "{}")])
    with pytest.raises(BlockedError):
        http.get("https://www.marktplaats.nl/lrp/api/search?query=x")
    assert len(sent) == 1  # no retry
    with pytest.raises(BlockedError):
        http.get("https://www.marktplaats.nl/lrp/api/search?query=y")
    assert len(sent) == 1  # no further requests to that host
    assert http.get("https://www.hnvi.nl/").status_code == 200  # other sites are unaffected


def test_non_json_answer_is_an_error():
    http, _ = client([Resp(200, "<html>not json</html>")])
    with pytest.raises(FetchError):
        http.json("https://onlineveilingmeester.nl/rest/nl/veilingen")


def test_server_errors_are_retried(monkeypatch):
    monkeypatch.setattr("scanner.http.time.sleep", lambda s: None)
    http, sent = client([Resp(503, "busy"), Resp(200, "ok")])
    assert http.text("https://www.proveiling.nl/") == "ok" and len(sent) == 2
