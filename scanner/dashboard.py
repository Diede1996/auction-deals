"""Build the dashboard: one self-contained HTML page with the scan results embedded as JSON.

All maths (suggested max bid for your target return) runs in the page, so you can change the
target return, minimum profit and expected sale price and see the max bids update right away.
"""
from __future__ import annotations

import json
from pathlib import Path

TEMPLATE_PATH = Path(__file__).with_name("dashboard_template.html")


def render(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c")  # scraped text can never open or close a tag in the page
    return TEMPLATE_PATH.read_text(encoding="utf-8").replace("__DATA__", payload)


def write(site_dir: Path, data: dict) -> Path:
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "index.html").write_text(render(data), encoding="utf-8")
    (site_dir / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (site_dir / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    return site_dir / "index.html"
