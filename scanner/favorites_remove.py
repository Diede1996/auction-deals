"""Take favorites off the dashboard list in one go (Diede, 12 Nov 2026: Claude went through the favorites and
Diede chose to remove the ones that were too dear or hard to sell). Changes only the JSON data and the readable
lines of the favorites issue; everything else stays as the dashboard wrote it. The removed entries are printed as
JSON in the log, so they can be put back.

    python -m scanner.favorites_remove --keys "hnvi:1,hnvi:2"     (needs GITHUB_TOKEN with issues: write)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

import requests

from .favorites import MARKER

API = "https://api.github.com"
_JSON_RE = re.compile(r"(```json\s*)(\[.*?\])(\s*```)", re.S)


def remove(body: str, keys: set[str]) -> tuple[str, list[dict]]:
    m = _JSON_RE.search(body)
    if not m or MARKER not in body:
        raise ValueError("not the favorites issue")
    favs = json.loads(m.group(2))
    gone = [f for f in favs if f.get("key") in keys]
    kept = [f for f in favs if f.get("key") not in keys]
    data = json.dumps(kept, ensure_ascii=False, separators=(",", ":")).replace("`", "\\u0060")
    body = body[:m.start()] + m.group(1) + data + m.group(3) + body[m.end():]
    urls = {str(f.get("url")).replace(")", "%29") for f in gone}
    lines = [ln for ln in body.split("\n") if not (ln.startswith("- [") and any(f"]({u})" in ln for u in urls))]
    return "\n".join(lines), gone


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keys", required=True, help="lot keys separated by commas")
    ap.add_argument("--issue", type=int, default=1)
    a = ap.parse_args(argv)
    keys = {k.strip() for k in a.keys.split(",") if k.strip()}
    repo, token = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    r = requests.get(f"{API}/repos/{repo}/issues/{a.issue}", headers=h, timeout=30)
    r.raise_for_status()
    body, gone = remove(r.json()["body"], keys)
    print(f"removing {len(gone)} of {len(keys)} asked")
    print(json.dumps(gone, ensure_ascii=False))
    if gone:
        requests.patch(f"{API}/repos/{repo}/issues/{a.issue}", headers=h, json={"body": body},
                       timeout=30).raise_for_status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
