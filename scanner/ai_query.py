"""Claude names the product when the bot's own Marktplaats search found too little (Diede, 24 Nov 2026).

The bot reads the type number from the lot title with fixed rules. In a test on 25 lots without a price it often
picked the wrong one: the chain of a chainsaw ("572 XP ... C85" -> C85), the Makita kit code ("DGA504RTJ", while
private sellers write "DGA504"), a different number from the description ("ZBook Fury 16 G9" -> "Fury 15 G9").
With the right type number and the product word ("makita dga504 haakse slijper") 5 of those 25 lots got a price,
against 0 with the bot's searches.

So, after the normal pricing, lots that still have no price go to Claude (Haiku, cheap) in batches: it names the
brand, the base type number as sellers write it, the Dutch product word, and whether the lot is only a part or
battery. The bot then searches Marktplaats again with that and compares only listings that name the same type
number (same filters as always: private sellers, used, no parts). Claude never gives a price.

The answers are saved per lot text (a hash, not the lot) for 30 days, so a lot is asked about once.
Needs the secret ANTHROPIC_API_KEY; without it this step is skipped.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta

from . import claude_check
from .identify import Rule, SearchPlan
from .util import normalize

log = logging.getLogger(__name__)

MODEL = claude_check.MODEL
BATCH = 30
KEEP_DAYS = 30

SYSTEM = """You help a Dutch reseller find second-hand prices on Marktplaats for auction lots.
For each numbered lot, name the main product so it can be searched on Marktplaats:
- brand: the manufacturer, lower case ("makita", "husqvarna", "hp").
- model: the manufacturer's base type number as private sellers write it in ad titles. Leave out kit and packaging
  suffixes that don't change the machine (Makita Z, ZJ, ZK, RTJ, RFJ, RMJ, J; DeWalt NT, P2, M2, -XJ, -QW).
  Keep what makes it a different product (DHR242 vs DHR243, "M18 FUEL" vs "M18", "ZBook Fury 16 G9").
  Use the number of the lot's main product, never that of a chain, blade, battery or accessory that comes with it.
  Use null when the lot gives no type number you are sure of, or holds several different machines.
- type: the Dutch product word sellers use, 1-2 words ("boorhamer", "haakse slijper", "robotmaaier", "laptop").
- part: true when the lot itself is only a battery, charger, accessory, spare part or clothing, not the machine.
Answer for every lot, with its number."""

SCHEMA = {
    "type": "object",
    "properties": {
        "lots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "brand": {"type": ["string", "null"]},
                    "model": {"type": ["string", "null"]},
                    "type": {"type": ["string", "null"]},
                    "part": {"type": "boolean"},
                },
                "required": ["i", "brand", "model", "type", "part"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["lots"],
    "additionalProperties": False,
}


def text_key(lot) -> str:
    """Saved under a hash of the lot's text: the public state file never lists which lots were asked about."""
    raw = f"{lot.title}|{(lot.description or '')[:300]}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _prompt(lots: list) -> str:
    lines = []
    for i, lot in enumerate(lots):
        desc = re.sub(r"\s+", " ", lot.description or "").strip()[:300]
        lines.append(f"{i}. {lot.title}" + (f" | {desc}" if desc else ""))
    return "\n".join(lines)


def ask(client, lots: list) -> dict[int, dict]:
    """{number: answer} for one batch; empty when the API can't be reached."""
    try:
        response = client.messages.create(
            model=MODEL, max_tokens=4000, system=SYSTEM,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": _prompt(lots)}])
    except Exception as e:  # anthropic.APIError and network trouble: these lots stay without a price
        log.warning("claude search help failed: %s", e)
        return {}
    if response.stop_reason != "end_turn":
        log.warning("claude search help stopped: %s", response.stop_reason)
        return {}
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        found = json.loads(text).get("lots") or []
    except (json.JSONDecodeError, AttributeError):
        return {}
    return {a["i"]: a for a in found if isinstance(a, dict) and isinstance(a.get("i"), int)}


def plan_from(answer: dict | None) -> SearchPlan | None:
    """The Marktplaats searches and the comparison rule for Claude's answer, or None without a usable type number."""
    if not answer:
        return None
    brand = normalize(answer.get("brand") or "")
    model = normalize(answer.get("model") or "")
    kind = normalize(answer.get("type") or "")
    squashed = model.replace(" ", "")
    if not brand or not squashed or len(squashed) < 2 or not re.search(r"\d", squashed):
        return None  # a type number has a digit in it ("J15", "305e"); "Airwrap" alone isn't one
    if model.startswith(brand + " "):
        model = model[len(brand) + 1:]
    label = f"{brand} {model}"
    searches = [f"{label} {kind}".strip(), label] if kind and kind not in model else [label]
    rule = Rule([(brand,)], model=squashed, label=label, parts_ok=bool(answer.get("part")))
    return SearchPlan("exact", searches, [rule], model=(answer.get("model") or "").strip(), brand=brand,
                      note=f"type number {answer.get('model')} named by Claude from the lot text")


def answers_for(lots: list, cache: dict, now: datetime, client, max_ask: int) -> dict[str, dict]:
    """Claude's answer per lot key: saved ones, then new questions in batches (at most max_ask lots)."""
    out, todo = {}, []
    for lot in lots:
        entry = cache.get(text_key(lot))
        if entry and now - datetime.fromisoformat(entry["at"]) <= timedelta(days=KEEP_DAYS):
            out[lot.key] = entry
        elif len(todo) < max_ask:
            todo.append(lot)
    if client:
        for start in range(0, len(todo), BATCH):
            batch = todo[start:start + BATCH]
            got = ask(client, batch)
            for i, lot in enumerate(batch):
                if i in got:
                    a = got[i]
                    entry = {"at": now.isoformat(), "brand": a.get("brand"), "model": a.get("model"),
                             "type": a.get("type"), "part": bool(a.get("part"))}
                    cache[text_key(lot)] = entry
                    out[lot.key] = entry
    return out


def prune(cache: dict, now: datetime) -> None:
    for k in [k for k, v in cache.items() if now - datetime.fromisoformat(v["at"]) > timedelta(days=KEEP_DAYS)]:
        del cache[k]


def reprice(rows: list, finder, cache: dict, now: datetime, client, reevaluate, searchable,
            max_ask: int = 120) -> tuple[list, dict]:
    """rows: (item, lot, verdict, estimate). Lots without a price that may be searched get Claude's type number and
    a new search. Returns the rows (repriced ones with their new estimate and verdict) and {lot key: plan} of the
    lots that got a price this way."""
    todo = [lot for item, lot, v, est in rows
            if est is None and item.price_check and item.market_price is None and not item.always_show
            and searchable(lot)]
    if not todo:
        return rows, {}
    answers = answers_for(todo, cache, now, client, max_ask)
    plans, out, tried = {}, [], 0
    for item, lot, v, est in rows:
        plan = plan_from(answers.get(lot.key)) if est is None and lot.key in answers else None
        if plan:
            tried += 1
            new = finder.for_plan(item, plan, prefix="ai")
            if new:
                plans[lot.key] = plan
                out.append((item, lot, reevaluate(item, lot, new), new))
                continue
        out.append((item, lot, v, est))
    log.info("claude named %d of %d unpriced lots, %d searched again, %d got a price",
             sum(1 for a in answers.values() if plan_from(a)), len(todo), tried, len(plans))
    return out, plans
