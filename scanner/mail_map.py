"""A small map of the pickup addresses for the Sunday mail (Diede, 2026-11-20).

Numbered pins on OpenStreetMap tiles, drawn with Pillow. The tiles are fetched once a week (a handful per mail,
well within https://operations.osmfoundation.org/policies/tiles/), with an identifying User-Agent and the
required "© OpenStreetMap" credit on the image. The coordinates come from the scan's address cache
(data/state.json "geo"), so nothing is looked up here. Any failure gives no map; the mail goes out without it.
"""
from __future__ import annotations

import io
import logging
import math

log = logging.getLogger(__name__)

TILE = 256
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
USER_AGENT = "auction-deals-bot/1.0 (weekly pickup map, github.com/Diede1996/auction-deals)"
PIN = (200, 40, 40)


def _world_px(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = TILE * 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat = max(min(lat, 85.0), -85.0)
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def _zoom(points: list[tuple[float, float]], width: int, height: int, pad: int) -> int:
    for z in range(12, 4, -1):
        xs, ys = zip(*(_world_px(lat, lon, z) for lat, lon in points))
        if max(xs) - min(xs) <= width - 2 * pad and max(ys) - min(ys) <= height - 2 * pad:
            return z
    return 5


def render(points: list[tuple[float, float]], get_tile, width: int = 640, height: int = 480,
           pad: int = 40) -> bytes | None:
    """points: (lat, lon) in the order of their numbers 1, 2, 3, ... get_tile(z, x, y) -> PNG bytes."""
    if not points:
        return None
    from PIL import Image, ImageDraw, ImageFont
    z = _zoom(points, width, height, pad)
    if len(points) == 1:
        z = min(z, 10)
    xs, ys = zip(*(_world_px(lat, lon, z) for lat, lon in points))
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    left, top = cx - width / 2, cy - height / 2
    img = Image.new("RGB", (width, height), (235, 235, 230))
    n = 2 ** z
    for tx in range(int(left // TILE), int((left + width) // TILE) + 1):
        for ty in range(int(top // TILE), int((top + height) // TILE) + 1):
            if not 0 <= ty < n:
                continue
            tile = Image.open(io.BytesIO(get_tile(z, tx % n, ty))).convert("RGB")
            img.paste(tile, (int(tx * TILE - left), int(ty * TILE - top)))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 14)
        small = ImageFont.truetype("DejaVuSans.ttf", 10)
    except OSError:
        font = small = ImageFont.load_default()
    # the highest numbers first, so pin 1 ends up on top where pins overlap
    for i in range(len(points), 0, -1):
        x, y = xs[i - 1] - left, ys[i - 1] - top
        r = 13
        draw.ellipse((x - r, y - r, x + r, y + r), fill=PIN, outline="white", width=2)
        draw.text((x, y), str(i), fill="white", font=font, anchor="mm")
    credit = "© OpenStreetMap"
    w = draw.textlength(credit, font=small)
    draw.rectangle((width - w - 8, height - 15, width, height), fill=(255, 255, 255))
    draw.text((width - w - 4, height - 13), credit, fill=(60, 60, 60), font=small)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def osm_tiles(session=None):
    import requests
    session = session or requests.Session()

    def get(z: int, x: int, y: int) -> bytes:
        r = session.get(TILE_URL.format(z=z, x=x, y=y), headers={"User-Agent": USER_AGENT}, timeout=20)
        r.raise_for_status()
        return r.content
    return get


def pickup_map(points: list[tuple[float, float]], get_tile=None) -> bytes | None:
    try:
        return render(points, get_tile or osm_tiles())
    except Exception as e:  # no map is better than no mail
        log.warning("pickup map: %s", e)
        return None
