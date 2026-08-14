"""IMGW meteorological-warning parsing and TERYT matching. No network."""

from __future__ import annotations

import pytest

from app.providers.http import cache
from app.providers.imgw import warnings as imgw_warnings

ENTRY = {
    "nazwa_zdarzenia": "Upał",
    "stopien": "2",
    "tresc": "Silne  oddziaływanie\ntermiczne.",
    "komentarz": "Ostrzeżenie może zostać zaktualizowane.",
    "biuro": "Kraków",
    "obowiazuje_od": "2026-08-13 12:00:00",
    "obowiazuje_do": "2026-08-13 20:00:00",
    "teryt": ["1261", "1262"],
}


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


def test_normalise_maps_the_documented_fields():
    warning = imgw_warnings._normalise(ENTRY)
    assert warning.event == "Upał"
    assert warning.headline == "Upał"
    # Runs of whitespace (including the embedded newline) are collapsed.
    assert warning.description == "Silne oddziaływanie termiczne."
    assert warning.instruction == "Ostrzeżenie może zostać zaktualizowane."
    assert warning.severity == "moderate"
    assert warning.level == 2
    assert warning.kind == "heat"
    assert warning.region == "Kraków"
    assert warning.color == "#fb8c00"
    # No Vorabinformation-equivalent tier exists for IMGW.
    assert warning.advance is False


def test_timestamps_are_parsed_as_warsaw_local_time():
    warning = imgw_warnings._normalise(ENTRY)
    assert warning.start.isoformat() == "2026-08-13T12:00:00+02:00"  # CEST in August
    assert warning.end.isoformat() == "2026-08-13T20:00:00+02:00"


def test_an_unparseable_timestamp_becomes_none():
    entry = dict(ENTRY, obowiazuje_od="not a timestamp")
    assert imgw_warnings._normalise(entry).start is None


@pytest.mark.parametrize(
    "level, severity, color",
    [(1, "minor", "#ffeb3b"), (2, "moderate", "#fb8c00"), (3, "severe", "#e53935")],
)
def test_every_documented_degree_maps_to_a_severity(level, severity, color):
    warning = imgw_warnings._normalise(dict(ENTRY, stopien=str(level)))
    assert (warning.severity, warning.color) == (severity, color)


def test_an_undocumented_degree_falls_back_to_minor():
    """IMGW's feed runs 1-3; a future/unexpected value must not crash the page."""
    warning = imgw_warnings._normalise(dict(ENTRY, stopien="9"))
    assert warning.severity == "minor"


def test_an_unlisted_event_name_falls_back_to_other():
    warning = imgw_warnings._normalise(dict(ENTRY, nazwa_zdarzenia="Coś nowego"))
    assert warning.kind == "other"


def test_matches_checks_teryt_intersection():
    assert imgw_warnings._matches(ENTRY, ["1261"]) is True
    assert imgw_warnings._matches(ENTRY, ["9999"]) is False
    assert imgw_warnings._matches(ENTRY, []) is False


def test_fetch_warnings_without_a_configured_teryt_returns_nothing(monkeypatch):
    monkeypatch.setattr(imgw_warnings.settings.imgw, "teryt", [])
    assert imgw_warnings.fetch_warnings() == []


def test_fetch_warnings_filters_by_the_venues_teryt(monkeypatch):
    other = dict(ENTRY, nazwa_zdarzenia="Burze", teryt=["9999"])
    monkeypatch.setattr(imgw_warnings, "fetch_json", lambda url: [ENTRY, other])
    matched = imgw_warnings.fetch_warnings(teryt=["1261"])
    assert len(matched) == 1
    assert matched[0].event == "Upał"


def test_fetch_warnings_sorts_severe_first(monkeypatch):
    minor = dict(ENTRY, nazwa_zdarzenia="Upał", stopien="1")
    severe = dict(ENTRY, nazwa_zdarzenia="Burze", stopien="3")
    monkeypatch.setattr(imgw_warnings, "fetch_json", lambda url: [minor, severe])
    matched = imgw_warnings.fetch_warnings(teryt=["1261"])
    assert [w.severity for w in matched] == ["severe", "minor"]
