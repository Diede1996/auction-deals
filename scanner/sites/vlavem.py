"""Vlavem (vlavem.com, De Vlaamse Veilingmaatschappij, Belgium): business closures ("stopzettingsveiling"),
estates ("afkomstig uit nalatenschap") and household contents, plus a lot of new overstock.

Same auction software as ProVeiling, so proveiling.fetch_from() reads it. Only auctions with a word from
auction_keywords in the name are read; the name says why the goods are sold.

Buyer's costs: 17% + 21% VAT on that 17% (on used goods there's no VAT on the bid itself);
config.yml counts 21% VAT on the bid too, to be safe.
"""
from __future__ import annotations

from . import proveiling
from .base import SiteContext

SITE = "vlavem"
BASE = "https://www.vlavem.com"


def fetch_lots(ctx: SiteContext):
    return proveiling.fetch_from(ctx, SITE, BASE, country="België")
