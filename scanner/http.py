"""HTTP client with browser-like TLS fingerprint, polite per-host delays and block detection.

When a site answers with a block page ("Toegang is tijdelijk beperkt", HTTP 403/429, a
Cloudflare challenge) the client raises BlockedError straight away, never retries, and refuses
every further request to that host for the rest of the run. Hammering a site that has
just blocked you only makes the block last longer.
"""
from __future__ import annotations

import logging
import random
import re
import time
from urllib.parse import urlparse

try:  # curl_cffi mimics a real Chrome browser, which avoids many simple bot blocks
    from curl_cffi import requests as _requests

    _SESSION_KW = {"impersonate": "chrome"}
except ImportError:  # pragma: no cover - fallback when curl_cffi is unavailable
    import requests as _requests  # type: ignore

    _SESSION_KW = {}

log = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
}

# Phrases that only appear on bot-block / challenge pages
BLOCK_MARKERS = (
    "toegang is tijdelijk beperkt",
    "iets in het gedrag van de browser",
    "attention required! | cloudflare",
    "cf-chl-",
    "<title>just a moment...</title>",
    "please verify you are a human",
)


class FetchError(RuntimeError):
    pass


class BlockedError(FetchError):
    """The site is refusing automated requests right now."""


def _redact(url: str) -> str:
    """Hide secrets (e.g. the Telegram bot token) from log lines."""
    return re.sub(r"/bot[^/]+", "/bot***", url)


def looks_blocked(text: str) -> bool:
    head = (text or "")[:30000].lower()
    return any(marker in head for marker in BLOCK_MARKERS)


class Http:
    def __init__(self, delay: float = 1.0, timeout: float = 30, retries: int = 2, jitter: float = 0.0):
        self.session = _requests.Session(**_SESSION_KW)
        self.session.headers.update(DEFAULT_HEADERS)
        self.delay = delay
        self.jitter = jitter  # extra random pause, so requests don't come at a machine-like rhythm
        self.timeout = timeout
        self.retries = retries
        self._last_hit: dict[str, float] = {}
        self.blocked: dict[str, str] = {}  # host -> reason, for the rest of this run
        self.request_count = 0

    def _wait(self, url: str) -> None:
        host = urlparse(url).netloc
        last = self._last_hit.get(host)
        if last is not None:
            pause = self.delay + (random.uniform(0, self.jitter) if self.jitter else 0)
            gap = time.monotonic() - last
            if gap < pause:
                time.sleep(pause - gap)
        self._last_hit[host] = time.monotonic()

    def _block(self, host: str, reason: str):
        self.blocked[host] = reason
        log.warning("%s is blocking us (%s); no more requests to it this run", host, reason)
        return BlockedError(f"{host} is blocking automated requests ({reason})")

    def request(self, method: str, url: str, **kw):
        host = urlparse(url).netloc
        if host in self.blocked:
            raise BlockedError(f"{host} is blocking automated requests ({self.blocked[host]})")
        kw.setdefault("timeout", self.timeout)
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            self._wait(url)
            self.request_count += 1
            try:
                resp = self.session.request(method, url, **kw)
            except Exception as e:  # network error
                last_err = e
                log.warning("%s %s failed (%s), attempt %d", method, _redact(url), _redact(str(e)), attempt + 1)
            else:
                if resp.status_code in (403, 429):
                    raise self._block(host, f"HTTP {resp.status_code}")
                if looks_blocked(resp.text):
                    raise self._block(host, "block page")
                if resp.status_code < 400:
                    return resp
                last_err = FetchError(f"HTTP {resp.status_code} for {_redact(url)}")
                if resp.status_code not in (408, 425, 500, 502, 503, 504):
                    break
                log.warning("%s %s -> %s, attempt %d", method, _redact(url), resp.status_code, attempt + 1)
            time.sleep(2 * (attempt + 1))
        raise FetchError(_redact(str(last_err)))

    def get(self, url: str, **kw):
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw):
        return self.request("POST", url, **kw)

    def text(self, url: str, **kw) -> str:
        return self.get(url, **kw).text

    def json(self, url: str, **kw):
        headers = {"Accept": "application/json", **kw.pop("headers", {})}
        resp = self.get(url, headers=headers, **kw)
        try:
            return resp.json()
        except ValueError:
            raise FetchError(f"expected JSON from {_redact(url)}, got something else") from None
