"""IMGW SYNOP parsing and the SQLite history store. No network: fetch_json is stubbed."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.providers.http import cache
from app.providers.imgw import observations as synop

SYNOP_PAYLOAD = {
    "id_stacji": "12375",
    "stacja": "KRAKÓW-BALICE",
    "data_pomiaru": "2026-08-13",
    "godzina_pomiaru": "12",
    "temperatura": "23.4",
    "predkosc_wiatru": "3",
    "kierunek_wiatru": "270",
    "wilgotnosc_wzgledna": "55.0",
    "suma_opadu": "1.2",
    "cisnienie": "1013.5",
}


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """A scratch SQLite file per test -- the module-level connection is
    process-global, so each test needs its own path and a fresh connection."""
    monkeypatch.setenv("EFW_IMGW_DB_PATH", str(tmp_path / "obs.sqlite3"))
    monkeypatch.setattr(synop, "_connection", None)
    cache.clear()
    yield
    cache.clear()
    monkeypatch.setattr(synop, "_connection", None)


def test_parses_the_documented_fields():
    point, precip_6h = synop._parse_synop(SYNOP_PAYLOAD)
    assert point.time == datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
    assert point.temperature == pytest.approx(23.4)
    assert point.humidity == pytest.approx(55.0)
    assert point.wind_speed == pytest.approx(3.0)
    assert point.wind_direction == pytest.approx(270.0)
    assert point.pressure == pytest.approx(1013.5)
    assert point.source == "imgw-synop"
    assert precip_6h == pytest.approx(1.2)


def test_dewpoint_is_derived_when_temperature_and_humidity_are_both_present():
    point, _ = synop._parse_synop(SYNOP_PAYLOAD)
    assert point.dewpoint is not None
    assert point.dewpoint < point.temperature


def test_wo6g_never_populates_hourly_precipitation():
    """The 6h gauge total from SYNOP is quality-control data, not this hour's
    rain -- see the module docstring and POLRAD_SRI_Implementation_Summary.md."""
    point, _ = synop._parse_synop(SYNOP_PAYLOAD)
    assert point.precipitation is None


def test_fields_imgw_does_not_publish_stay_none():
    point, _ = synop._parse_synop(SYNOP_PAYLOAD)
    assert point.wind_gust is None
    assert point.cloud_cover is None
    assert point.visibility is None
    assert point.weather_code is None


def test_missing_timestamp_is_rejected():
    with pytest.raises(ValueError):
        synop._parse_synop({"temperatura": "1.0"})


def test_non_numeric_fields_become_none_rather_than_raising():
    payload = dict(SYNOP_PAYLOAD, temperatura="brak", suma_opadu=None)
    point, precip_6h = synop._parse_synop(payload)
    assert point.temperature is None
    assert point.dewpoint is None  # cannot derive without a temperature
    assert precip_6h is None


def test_fetch_current_requires_a_configured_station(monkeypatch):
    monkeypatch.setattr(synop.settings.imgw, "station_id", "")
    with pytest.raises(ValueError, match="not configured"):
        synop.fetch_current()


def test_fetch_current_stores_and_fetch_recent_reads_it_back(monkeypatch):
    monkeypatch.setattr(synop, "fetch_json", lambda url: dict(SYNOP_PAYLOAD))
    point = synop.fetch_current(station_id="12375")
    assert point.temperature == pytest.approx(23.4)

    cache.clear()  # force fetch_recent's own refresh to hit the stub again
    recent = synop.fetch_recent(station_id="12375")
    assert len(recent) == 1
    assert recent[0].time == point.time
    assert recent[0].temperature == pytest.approx(23.4)
    # The store round-trips through ISO text; the WO6G figure is not part of
    # what comes back as a WeatherPoint either way.
    assert recent[0].precipitation is None


def test_fetch_recent_survives_the_refresh_failing(monkeypatch):
    """A transient SYNOP failure must not hide history already on disk."""
    monkeypatch.setattr(synop, "fetch_json", lambda url: dict(SYNOP_PAYLOAD))
    synop.fetch_current(station_id="12375")
    cache.clear()

    def boom(url):
        raise RuntimeError("SYNOP unreachable")

    monkeypatch.setattr(synop, "fetch_json", boom)
    recent = synop.fetch_recent(station_id="12375")
    assert len(recent) == 1
