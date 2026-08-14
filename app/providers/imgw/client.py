"""IMGW's product catalogue -- the only sanctioned way to find a download URL.

IMGW's datastore files live at URLs that change with every run/timestamp, and
some catalogue entries that look like the product wanted (``*_h5`` suffixed
ids) answer ``{"status": false, "message": "Product could not be found"}``
while a sibling id without the suffix works and lists the ``.h5`` file inside
it. Both of these are things you can only find out by asking the catalogue
live, never by hardcoding a URL pattern -- so every fetch in this package goes
through ``product_files`` rather than building a datastore path itself.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.providers.http import cache, fetch_json

logger = logging.getLogger(__name__)

CATALOGUE_URL = "https://danepubliczne.imgw.pl/api/data/product"
PRODUCT_URL = "https://danepubliczne.imgw.pl/api/data/product/id/{id}"

#: The catalogue barely changes; the file listing inside a product does, on
#: whatever cadence that product publishes (5 min for radar, hourly-ish for
#: COSMO runs). Cache the listing briefly rather than per-request.
LISTING_TTL = 120


class ProductNotFound(RuntimeError):
    """The catalogue has no entry for this product id right now."""


def catalogue() -> List[Dict[str, str]]:
    """The full list of ``{id, url, opis}`` products IMGW currently publishes."""
    return cache.get_or_fetch("imgw:catalogue", 3600, lambda: fetch_json(CATALOGUE_URL))


def product_files(product_id: str) -> List[Dict[str, str]]:
    """Every file currently published under a product id, as ``{file, url}``.

    Raises :class:`ProductNotFound` for both an HTTP-level miss and IMGW's own
    ``{"status": false}`` body -- callers should not have to know which shape
    "not found" comes back as.
    """

    def _fetch() -> List[Dict[str, str]]:
        data: Any = fetch_json(PRODUCT_URL.format(id=product_id))
        if isinstance(data, dict) and data.get("status") is False:
            raise ProductNotFound(f"{product_id}: {data.get('message', 'not found')}")
        if not isinstance(data, list):
            raise ProductNotFound(f"{product_id}: unexpected catalogue response shape")
        return data

    return cache.get_or_fetch(f"imgw:product:{product_id}", LISTING_TTL, _fetch)


def latest_file(product_id: str, suffix: Optional[str] = None) -> Dict[str, str]:
    """The most recently published file for a product, optionally filtered by suffix.

    File names in every product used here start with a sortable timestamp
    (``COMPO_SRI``: ``YYYYMMDDHHMMSS...``; COSMO: the valid-time stamp), so the
    lexicographically greatest name is the newest file -- no need to parse
    every name just to pick one.
    """
    files = product_files(product_id)
    if suffix:
        files = [f for f in files if f.get("file", "").endswith(suffix)]
    if not files:
        raise ProductNotFound(f"{product_id}: no files matching {suffix!r}")
    return max(files, key=lambda f: f["file"])
