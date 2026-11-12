"""Claude checks a Marktplaats bargain before it goes to Telegram (Diede, 12 Nov 2026).

The deals finder (deals.py) compares a new listing with other Marktplaats listings using fixed rules, and those
sometimes compare different things (a professional Kärcher with a home K5, a set with a single machine). Before a
bargain is sent, Claude (Haiku, cheap) reads the listing and the listings it was compared with, and says which of
those really are the same thing. The code then takes the median of those, as the bot does: Claude never makes up
a price. A bargain that is no bargain any more is left out; the others get Claude's short note.

Needs the secret ANTHROPIC_API_KEY; without it, or when the API fails, bargains go out unchecked as before.
"""
from __future__ import annotations

import json
import logging
import os
import statistics
from dataclasses import replace

log = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
MAX_CHECKS = 10  # bargains checked per run, the rest go out unchecked (a run rarely finds more than a few)
MIN_LISTINGS = 2

SYSTEM = """You check bargains for a Dutch reseller who buys second-hand things on Marktplaats to sell them again.
You get one new listing (the possible bargain) and numbered listings it was compared with. Say which of the
numbered listings are really the same thing as the new listing: same model and version, same storage/memory for
laptops and phones, same year or chip for Apple, the whole device (not only a battery, case or part, not a set
with other machines). When the new listing doesn't say enough to know (no model, unclear what's included), say so.
Never guess prices. Write the reason in Dutch, one or two short sentences."""

SCHEMA = {
    "type": "object",
    "properties": {
        "keep": {"type": "array", "items": {"type": "integer"}},
        "verdict": {"type": "string", "enum": ["kopen", "twijfel", "niet"]},
        "reason": {"type": "string"},
    },
    "required": ["keep", "verdict", "reason"],
    "additionalProperties": False,
}


def _prompt(lot, est) -> str:
    lines = [f"NEW LISTING: {lot.title}", f"Asking price: €{lot.current_bid or 0:g}",
             f"Description: {(lot.description or '')[:600]}", "", "COMPARED WITH:"]
    for i, li in enumerate(est.listings or []):
        each = f" (per piece, set of {li['of']})" if li.get("of") else ""
        lines.append(f"{i}. €{li['price']:g}{each} {li.get('title') or ''}")
    unseen = max(0, len(est.prices or []) - len(est.listings or []))
    if unseen:
        lines.append(f"(+ {unseen} dearer listings not shown)")
    return "\n".join(lines)


def ask(client, lot, est) -> dict | None:
    """Claude's judgment {keep, verdict, reason}, or None when it can't be had."""
    try:
        response = client.messages.create(
            model=MODEL, max_tokens=2000, system=SYSTEM,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": _prompt(lot, est)}])
    except Exception as e:  # anthropic.APIError and network trouble: send the bargain unchecked
        log.warning("claude check failed for %s: %s", lot.key, e)
        return None
    if response.stop_reason != "end_turn":
        log.warning("claude check for %s stopped: %s", lot.key, response.stop_reason)
        return None
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def checked_estimate(est, keep: list[int]):
    """The estimate again, from only the listings Claude kept (plus the unseen dearer ones); None if too few."""
    shown = est.listings or []
    keep = {i for i in keep if isinstance(i, int) and 0 <= i < len(shown)}
    if not keep:
        return None
    prices = [float(shown[i]["price"]) for i in sorted(keep)]
    prices += sorted(est.prices or [])[len(shown):]
    if len(prices) < MIN_LISTINGS:
        return None
    prices.sort()
    q = statistics.quantiles(prices, n=4) if len(prices) >= 2 else [prices[0]] * 3
    return replace(est, median=round(statistics.median(prices), 2), low=round(q[0], 2), high=round(q[2], 2),
                   count=len(prices),
                   listings=[shown[i] for i in sorted(keep)], prices=prices)


def client_from_env():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    try:
        import anthropic
    except ImportError:
        log.warning("ANTHROPIC_API_KEY is set but the anthropic package isn't installed")
        return None
    return anthropic.Anthropic(api_key=key, max_retries=2, timeout=60)


def check(found: list, reevaluate, client=None) -> tuple[list, dict]:
    """found: [(item, lot, verdict, estimate)]. reevaluate(item, lot, estimate) -> Verdict.
    Returns (bargains to send, {lot key: note}). Unchecked bargains keep going out."""
    client = client if client is not None else client_from_env()
    if client is None or not found:
        return found, {}
    out, notes, dropped = [], {}, 0
    for n, (item, lot, v, est) in enumerate(found):
        if n >= MAX_CHECKS or est is None or not est.listings:
            out.append((item, lot, v, est))
            continue
        j = ask(client, lot, est)
        if j is None:
            out.append((item, lot, v, est))
            continue
        new = checked_estimate(est, j.get("keep") or [])
        v2 = reevaluate(item, lot, new) if new is not None else None
        if j.get("verdict") == "niet" or new is None or not v2.is_deal:
            dropped += 1
            log.info("claude check: %s is no bargain (%s)", lot.title[:60], j.get("reason"))
            continue
        notes[lot.key] = f"{j.get('verdict')}: {j.get('reason') or ''}".strip()
        out.append((item, lot, v2, new))
    log.info("claude check: %d bargains checked, %d left out", min(len(found), MAX_CHECKS), dropped)
    return out, notes
