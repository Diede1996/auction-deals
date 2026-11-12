"""Claude's buy advice every day, through the API (Diede, 12 Nov 2026).

The same steps as the scheduled Claude session (scanner/advice.py, docs/claude-advice.md), but run by the bot itself
after each daily scan with a small Claude model (Haiku), so every promising lot is checked before Diede sees it, not
only 40 twice a week. Lots without a Marktplaats price get a search on 2dehands.be (the Belgian sister site of
Marktplaats); Claude picks the listings that really compare there too. Prices always come from real listings: the
median of the ones Claude kept, never from Claude's memory.

    python -m scanner.advice_ai --private ../auction-private     (needs ANTHROPIC_API_KEY)
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus, urlencode, urlparse

from . import advice, claude_check, private_repo

log = logging.getLogger(__name__)

MODEL = claude_check.MODEL
MAX_JUDGE = 150  # lots per day; ones judged before on the same listings are skipped, so a normal day has far fewer
MAX_2DEHANDS = 30  # 2dehands searches per day for lots Marktplaats couldn't price
SEARCH_2DEHANDS = "https://www.2dehands.be/lrp/api/search"

SYSTEM = """You check auction lots for a Dutch reseller who buys at auctions and resells on Marktplaats/2dehands.
You get one lot and numbered second-hand listings a bot found for it. Decide which listings are REALLY the same
thing as the lot, so the reseller's price is right. Be strict, when in doubt leave a listing out:
- exactly the same model and version: the type number must match (Kärcher BR 30/1 is not BR 35/12; K5 home model is
  not an HD professional machine; Makita DHR243 is not DHR242), same storage/memory for laptops and phones, same
  year or chip for Apple;
- the whole device in working order: not only a battery, charger, case, attachment or part; not a set with other
  machines; not "defect" or "for parts";
- private sellers only: leave out shops and traders. Signs: several listings with the same title where only a year,
  number or size differs, "Pack 2015", "BTW", "garantie", "factuur", "inruil", "leverbaar", a company name.
modelKnown = false when the lot itself doesn't say which model it is (only a brand and a kind of machine, or
several different models in one lot): then keep nothing.
verdict: "kopen" = the model is clear and at least 2 listings really compare (the bot works out the price and max
bid from those; you don't know the bid, so never doubt because of the price); "twijfel" = model unknown, fewer
than 2 real listings, or a mixed lot; "niet" = defect, for parts, poor resale, or a trap.
dropUnseen = true when the search clearly mixes in other models (the dearer listings beyond the ones shown then
don't count either). model = what the lot is, as precisely as it says. confidence: hoog / middel / laag.
reason: one or two short sentences, ALWAYS in Dutch: what compares, and what the bot got wrong if it did. risks: optional,
short, Dutch. Never guess a price."""

SCHEMA = {
    "type": "object",
    "properties": {
        "modelKnown": {"type": "boolean"},
        "keep": {"type": "array", "items": {"type": "integer"}},
        "verdict": {"type": "string", "enum": ["kopen", "twijfel", "niet"]},
        "model": {"type": "string"},
        "confidence": {"type": "string", "enum": ["hoog", "middel", "laag"]},
        "reason": {"type": "string"},
        "risks": {"type": "string"},
        "dropUnseen": {"type": "boolean"},
    },
    "required": ["modelKnown", "keep", "verdict", "model", "confidence", "reason", "risks", "dropUnseen"],
    "additionalProperties": False,
}


def _lot_text(lot: dict) -> list[str]:
    out = [f"LOT: {lot.get('title')}", f"Category: {lot.get('item')}"]
    if lot.get("units") and int(lot["units"]) > 1:
        out.append(f"Items in the lot: {lot['units']} (listing prices are per item)")
    if lot.get("description"):
        out.append(f"Description: {str(lot['description'])[:600]}")
    if lot.get("auction"):
        out.append(f"Auction: {lot['auction']}")
    return out


def prompt(lot: dict, listings: list[dict], unseen: int = 0, where: str = "Marktplaats") -> str:
    lines = _lot_text(lot) + ["", f"LISTINGS ON {where.upper()} (cheapest first):"]
    for i, li in enumerate(listings):
        each = f" (per piece, set of {li['of']})" if li.get("of") else ""
        bid = " (bieden vanaf)" if li.get("bid") else ""
        lines.append(f"{i}. €{li.get('price'):g}{each}{bid} {li.get('title') or ''}"
                     + (f" | {li['desc']}" if li.get("desc") else ""))
    if unseen:
        lines.append(f"(+ {unseen} dearer listings not shown)")
    return "\n".join(lines)


def ask(client, text: str) -> dict | None:
    try:
        r = client.messages.create(model=MODEL, max_tokens=2000, system=SYSTEM,
                                   output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                                   messages=[{"role": "user", "content": text}])
    except Exception as e:
        log.warning("advice: Claude call failed: %s (%r)", e, getattr(e, "__cause__", None))
        return None
    if r.stop_reason != "end_turn":
        return None
    try:
        j = json.loads(next((b.text for b in r.content if b.type == "text"), ""))
    except json.JSONDecodeError:
        return None
    if j.get("modelKnown") is False:  # no type number in the lot: no listing can be "the same", whatever was kept
        j["keep"], j["verdict"] = [], "niet" if j.get("verdict") == "niet" else "twijfel"
    return j


# ---------------------------------------------------------------- 2dehands for lots Marktplaats couldn't price

def query_of(lot: dict) -> str | None:
    """The bot's own search (brand + type number) for a lot with a known model."""
    plan = lot.get("mpPlan") or {}
    if plan.get("kind") != "exact" or not plan.get("model"):
        return None
    url = plan.get("search") or lot.get("mpSearch") or ""
    path = urlparse(url).path
    if "/q/" in path:
        return unquote_plus(path.split("/q/", 1)[1].strip("/")).strip() or None
    q = parse_qs(urlparse(url).query).get("query")
    return q[0] if q else " ".join(x for x in (plan.get("brand"), plan.get("model")) if x)


def lots_for_2dehands(lots: list[dict], old: dict, now: datetime, limit: int = MAX_2DEHANDS) -> list[dict]:
    out = []
    for lot in lots:
        if lot.get("mp") or lot.get("item") in advice.SKIP_ITEMS or lot.get("key") in old:
            continue
        closes = advice._closes(lot)
        if not closes or closes < now or closes > now + timedelta(hours=advice.WINDOW_HOURS):
            continue
        if query_of(lot):
            out.append((closes, lot))
    out.sort(key=lambda t: t[0])
    return [lot for _, lot in out[:limit]]


def search_2dehands(http, query: str) -> list[dict]:
    from .marktplaats import PRICED_TYPES, condition_of, is_business
    params = {"query": query, "limit": 100, "offset": 0, "searchInTitleAndDescription": "false",
              "viewOptions": "list-view"}
    rows = []
    for li in http.json(f"{SEARCH_2DEHANDS}?{urlencode(params)}").get("listings") or []:
        info = li.get("priceInfo") or {}
        if info.get("priceType") not in PRICED_TYPES or not info.get("priceCents") or is_business(li):
            continue
        if condition_of(li) in ("niet werkend", "defect"):
            continue
        vip = li.get("vipUrl") or ""
        rows.append({"price": round(info["priceCents"] / 100, 2),
                     "title": html.unescape(li.get("title") or "").strip()[:90],
                     "desc": html.unescape(li.get("description") or "").replace("\n", " ").strip()[:100],
                     **({"bid": True} if info.get("priceType") == "MIN_BID" else {}),
                     "url": ("https://www.2dehands.be" + vip) if vip.startswith("/") else vip})
    rows.sort(key=lambda r: r["price"])
    return rows[:30]


# ---------------------------------------------------------------- the daily run

def judge(root: Path, private: Path, now: datetime, client, http=None) -> dict:
    """Claude's judgments for today's lots, in the format advice.apply() takes."""
    data, lots = advice.all_lots(root, private)
    by_key = {lot.get("key"): lot for lot in lots}
    old = (advice._load(private / advice.ADVICE_PATH) or {}).get("lots") or {}
    todo = advice.prepare(root, private, now, MAX_JUDGE)
    judgments: dict = {}
    for entry in todo["lots"]:
        mp = entry.get("marktplaats") or {}
        if mp.get("sameListingsAs"):
            judgments[entry["key"]] = {"sameAs": mp["sameListingsAs"]}
            continue
        lot = by_key.get(entry["key"]) or entry
        j = ask(client, prompt(lot, mp.get("listings") or [], mp.get("notShown") or 0))
        if j:
            j["source"] = "marktplaats"
            judgments[entry["key"]] = j
    judgments = {k: j for k, j in judgments.items() if not j.get("sameAs") or j["sameAs"] in judgments}
    if http is None:
        from .http import Http
        http = Http(delay=4.0, jitter=3.0)
    done: dict = {}  # same search and title (ten identical lots): one search and one judgment
    for lot in lots_for_2dehands(lots, old, now):
        q = query_of(lot)
        same = (q, lot.get("title"))
        if same in done:
            judgments[lot["key"]] = dict(judgments[done[same]])
            continue
        try:
            found = search_2dehands(http, q)
        except Exception as e:  # blocked or down: the rest waits for tomorrow
            log.warning("advice: 2dehands search failed: %s", e)
            break
        if len(found) < advice.MIN_LISTINGS:
            continue
        j = ask(client, prompt(lot, found, where="2dehands.be"))
        if not j:
            continue
        keep = {i for i in j.get("keep") or [] if isinstance(i, int) and 0 <= i < len(found)}
        j["listings"] = [found[i] for i in sorted(keep)]
        j["keep"] = []
        j["source"] = "2dehands"  # no listing kept: apply() gives no price (it needs 2 real listings)
        judgments[lot["key"]] = j
        done[same] = lot["key"]
    return judgments


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--private", required=True, help="a checkout of auction-private")
    a = ap.parse_args(argv)
    import os
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:  # the shape only, never the key itself: a pasted space or line break inside breaks every call
        k = key.strip()
        print(f"API key: {len(k)} characters, starts with sk-ant-: {k.startswith('sk-ant-')}, "
              f"plain ASCII without spaces: {k.isascii() and not any(c.isspace() for c in k)}")
    client = claude_check.client_from_env()
    if client is None:
        print("no ANTHROPIC_API_KEY: no Claude advice today")
        return 0
    root, private, now = Path(a.root), Path(a.private), datetime.now(timezone.utc)
    started = time.time()
    judgments = judge(root, private, now, client)
    res = advice.apply(root, private, judgments, now, daily=True)
    counts: dict[str, int] = {}
    for x in res["lots"].values():
        counts[x["label"]] = counts.get(x["label"], 0) + 1
    print(f"Claude judged {len(judgments)} lots in {time.time() - started:.0f} s; advice now {counts}")
    if not private_repo.put_json(advice.ADVICE_PATH, res, "Claude advice (daily)"):
        print("could not save the advice to the private repository", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
