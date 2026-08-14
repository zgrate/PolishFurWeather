"""IMGW product-catalogue lookups. No network: fetch_json is stubbed."""

from __future__ import annotations

import pytest

from app.providers.http import cache
from app.providers.imgw import client


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


def test_product_files_returns_the_listing(monkeypatch):
    monkeypatch.setattr(
        client, "fetch_json", lambda url: [{"file": "a.h5", "url": "https://x/a.h5"}]
    )
    assert client.product_files("COMPO_SRI.comp.sri") == [
        {"file": "a.h5", "url": "https://x/a.h5"}
    ]


def test_product_files_raises_on_imgws_own_not_found_shape(monkeypatch):
    """A ``*_h5``-suffixed id answers 200 with ``{"status": false}`` -- an HTTP
    miss and this body must be indistinguishable to callers."""
    monkeypatch.setattr(
        client, "fetch_json", lambda url: {"status": False, "message": "Product could not be found"}
    )
    with pytest.raises(client.ProductNotFound, match="Product could not be found"):
        client.product_files("COMPO_SRI.comp.sri_h5")


def test_product_files_raises_on_an_unexpected_shape(monkeypatch):
    monkeypatch.setattr(client, "fetch_json", lambda url: {"unexpected": True})
    with pytest.raises(client.ProductNotFound):
        client.product_files("whatever")


def test_latest_file_picks_the_lexicographically_greatest_name(monkeypatch):
    """File names start with a sortable timestamp, so string-max is newest."""
    monkeypatch.setattr(
        client,
        "fetch_json",
        lambda url: [
            {"file": "20260813090000.sri.h5", "url": "https://x/1"},
            {"file": "20260813093000.sri.h5", "url": "https://x/2"},
            {"file": "20260813084500.sri.h5", "url": "https://x/3"},
        ],
    )
    assert client.latest_file("COMPO_SRI.comp.sri")["url"] == "https://x/2"


def test_latest_file_filters_by_suffix(monkeypatch):
    monkeypatch.setattr(
        client,
        "fetch_json",
        lambda url: [
            {"file": "readme.txt", "url": "https://x/readme"},
            {"file": "20260813090000.sri.h5", "url": "https://x/1"},
        ],
    )
    assert client.latest_file("COMPO_SRI.comp.sri", suffix=".sri.h5")["file"] == "20260813090000.sri.h5"


def test_latest_file_raises_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(client, "fetch_json", lambda url: [{"file": "readme.txt", "url": "x"}])
    with pytest.raises(client.ProductNotFound):
        client.latest_file("COMPO_SRI.comp.sri", suffix=".sri.h5")


def test_catalogue_is_cached_across_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(client, "fetch_json", lambda url: calls.append(url) or [{"id": "x"}])
    client.catalogue()
    client.catalogue()
    assert len(calls) == 1
