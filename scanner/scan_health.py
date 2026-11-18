"""Scan health per site (Diede, 18 Nov 2026: "how do I know all the searches on the auction sites go well?").

The bot reads thousands of lots a day and shows ~100; a site that silently stops giving lots is easy to miss
(Troostwijk's own pages gave 0 auctions from 12 to 18 Nov while its alert emails kept the count above 0). So
every scan keeps the last scans' counts per site in state.json and marks a site when it gives 0 (or far fewer)
lots or auctions than usual. The dashboard shows this in its "Scan health" block; the daily Bot check reads it too.
"""
from __future__ import annotations

from datetime import datetime
from statistics import median

KEEP = 14          # scans remembered per site
MIN_HISTORY = 3    # scans needed before "usual" means anything
DROP = 0.25        # below a quarter of the usual count counts as a sudden drop


def _usual(values: list[int]) -> int | None:
    vals = [v for v in values if isinstance(v, int)]
    return int(median(vals)) if len(vals) >= MIN_HISTORY else None


def warning(site: dict, usual_lots: int | None, usual_auctions: int | None) -> str:
    """'' when all is well, else a short reason (English, like the rest of the dashboard)."""
    if not site.get("ok"):
        return "couldn't be read: " + (site.get("error") or "no reason given")[:120]
    lots, auctions, read = site.get("lots") or 0, site.get("auctions"), site.get("auctionsRead")
    if site.get("id") == "troostwijk" and "siteAuctions" in site and not site.get("siteAuctions"):
        return "Troostwijk's own auction list gave 0 auctions (only the alert emails came in): the site may have changed"
    if auctions == 0 and usual_auctions:
        return f"0 auctions found (usually {usual_auctions}): the site may have changed"
    if lots == 0:
        if auctions and not read:
            return ""  # auctions listed, just none of the kinds read (bankruptcy, closure, ...): fine
        return f"0 lots (usually {usual_lots})" if usual_lots else "0 lots"
    if usual_lots and usual_lots >= 20 and lots < usual_lots * DROP and not (auctions and read and read < 2):
        return f"only {lots} lots (usually {usual_lots})"
    return ""


def annotate(sites: list[dict], state: dict, now: datetime, record: bool = True) -> list[dict]:
    """Adds usual/warn to each site of the dashboard's `sites` list and remembers this scan's counts."""
    hist = state.setdefault("site_history", {})
    for s in sites:
        if s.get("id") == "marktplaats":
            continue
        past = hist.get(s["id"]) or []
        usual_lots = _usual([p.get("lots") for p in past if p.get("ok", True)])
        usual_auctions = _usual([p.get("auctions") for p in past if p.get("auctions") is not None])
        if usual_lots is not None:
            s["usual"] = usual_lots
        s["warn"] = warning(s, usual_lots, usual_auctions)
        if record:
            entry = {"at": now.isoformat(), "ok": bool(s.get("ok")), "lots": s.get("lots") or 0}
            for k in ("auctions", "auctionsRead", "siteAuctions", "siteLots", "mailLots"):
                if k in s:
                    entry[k] = s[k]
            hist[s["id"]] = (past + [entry])[-KEEP:]
    return sites
