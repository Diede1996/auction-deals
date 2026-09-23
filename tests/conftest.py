import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FakeResponse:
    def __init__(self, body, status=200):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return json.loads(self.text)


class FakeHttp:
    """Routes: list of (predicate(method, url, kwargs) -> bool, body or callable)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self.request_count = 0

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        self.request_count += 1
        for match, body in self.routes:
            if match(method, url, kw):
                body = body(method, url, kw) if callable(body) else body
                return FakeResponse(body)
        raise AssertionError(f"unexpected request {method} {url}")

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def text(self, url, **kw):
        return self.get(url, **kw).text

    def json(self, url, **kw):
        return self.get(url, **kw).json()


def url_has(*parts, method=None):
    return lambda m, u, kw: all(p in u for p in parts) and (method is None or m == method)


@pytest.fixture
def fake_http():
    return FakeHttp
