"""Read all lots of one Troostwijk auction into the private repository (workflow "Auction lots").
Only counts are printed: the public log must not show Troostwijk's lots (see private_repo.py)."""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

from . import private_repo
from .http import BlockedError
from .sites.troostwijk import Reader, read_auction

_TAGS = re.compile(r"<[^>]+>")


def lot_details(props: dict) -> dict:
    lot = props.get("lot") or {}
    desc = lot.get("description") or {}
    if isinstance(desc, dict):
        desc = desc.get("description") or desc.get("text") or ""
    attrs = [{"name": a.get("name"), "value": a.get("value")} for a in lot.get("attributes") or [] if isinstance(a, dict)]
    return {"description": _TAGS.sub(" ", str(desc or "")).strip(), "attributes": attrs,
            "startBid": ((lot.get("startingBid") or lot.get("initialAmount") or {}) or {}).get("cents")}


def main() -> int:
    slug = (os.environ.get("SLUG") or "").strip().split("?")[0].rstrip("/").split("/")[-1]
    if not re.fullmatch(r"[a-z0-9-]+", slug or ""):
        print("not an auction slug")
        return 1
    reader = Reader(SimpleNamespace(http=None), 8.0)
    try:
        lots, complete = read_auction(reader, slug, 40)
        if os.environ.get("DESCRIPTIONS", "true").lower() == "true":
            for i, rec in enumerate(lots):
                try:
                    props = reader.props(f"/nl/l/{rec['slug']}")
                    rec.update(lot_details(props))
                    rec["raw"] = props.get("lot")  # the page's own fields, in case the description sits elsewhere
                except BlockedError:
                    raise
                except Exception as e:  # one odd lot page shouldn't stop the rest
                    rec["error"] = type(e).__name__
                if (i + 1) % 25 == 0:
                    print(f"{i + 1} of {len(lots)} lot pages read")
    except BlockedError as e:
        print(f"stopped: {e}")
        complete = False
    data = {"slug": slug, "read": datetime.now(timezone.utc).isoformat(), "complete": complete, "lots": lots}
    ok = private_repo.put_json(f"lookups/{slug}.json", data, f"Lots of auction {slug}")
    print(f"{len(lots)} lots read (complete: {complete}); saved to the private repository: {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
