"""Open-Meteo forecast and pollen parsing. No network: get() is stubbed."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.providers.http import cache
from app.providers.open_meteo import forecast, pollen


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


# ------------------------------------------------------------------ forecast

FORECAST_PAYLOAD = {
    "hourly": {
        "time": ["2026-08-13T12:00", "2026-08-13T13:00"],
        "temperature_2m": [23.4, 24.1],
        "dew_point_2m": [12.0, 12.5],
        "relative_humidity_2m": [55.0, 52.0],
        "wind_speed_10m": [3.0, 3.5],
        "wind_gusts_10m": [6.0, 7.0],
        "wind_direction_10m": [270, 275],
        "pressure_msl": [1013.5, 1013.0],
        "cloud_cover": [40, 60],
        "precipitation": [0.0, 0.2],
        "precipitation_probability": [10, 30],
        "shortwave_radiation": [500.0, 480.0],
        "visibility": [20000, 18000],
        "weather_code": [1, 61],
    }
}


def test_fetch_maps_every_documented_field(monkeypatch):
    monkeypatch.setattr(forecast, "get", lambda url, params: FORECAST_PAYLOAD)
    points = forecast.fetch_forecast(lat=50.06, lon=19.94, hours=48)
    assert len(points) == 2
    first = points[0]
    assert first.time == datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
    assert first.temperature == pytest.approx(23.4)
    assert first.dewpoint == pytest.approx(12.0)
    assert first.humidity == pytest.approx(55.0)
    assert first.wind_speed == pytest.approx(3.0)
    assert first.wind_gust == pytest.approx(6.0)
    assert first.wind_direction == pytest.approx(270)
    assert first.pressure == pytest.approx(1013.5)
    assert first.cloud_cover == pytest.approx(40)
    assert first.precipitation == pytest.approx(0.0)
    assert first.precipitation_prob == pytest.approx(10)
    assert first.solar_radiation == pytest.approx(500.0)
    assert first.visibility == pytest.approx(20000)
    assert first.weather_code == 1
    assert first.source == "open-meteo"


def test_fetch_handles_missing_columns_as_none(monkeypatch):
    payload = {"hourly": {"time": ["2026-08-13T12:00"], "temperature_2m": [23.4]}}
    monkeypatch.setattr(forecast, "get", lambda url, params: payload)
    points = forecast.fetch_forecast(lat=50.06, lon=19.94, hours=48)
    assert points[0].temperature == pytest.approx(23.4)
    assert points[0].wind_speed is None
    assert points[0].weather_code is None


def test_fetch_handles_an_empty_hourly_block(monkeypatch):
    monkeypatch.setattr(forecast, "get", lambda url, params: {})
    assert forecast.fetch_forecast(lat=50.06, lon=19.94, hours=48) == []


@pytest.mark.parametrize(
    "hours, expected_days",
    [(1, 2), (24, 2), (25, 3), (16 * 24, 16), (99 * 24, 16)],  # forecast_days caps at 16
)
def test_days_for_rounds_up_and_caps_at_sixteen(hours, expected_days):
    assert forecast._days_for(hours) == expected_days


def test_cache_key_is_stable_for_the_same_request():
    key_a = forecast.cache_key(lat=50.06, lon=19.94, hours=48)
    key_b = forecast.cache_key(lat=50.06, lon=19.94, hours=48)
    assert key_a == key_b
    assert "50.060" in key_a and "19.940" in key_a


# --------------------------------------------------------------------- pollen


def _pollen_payload(**overrides):
    hourly = {
        "time": ["2026-08-13T11:00", "2026-08-13T12:00", "2026-08-13T13:00"],
        "alder_pollen": [5.0, 5.0, 5.0],
        "birch_pollen": [80.0, 80.0, 80.0],
        "grass_pollen": [15.0, 15.0, 15.0],
        "ragweed_pollen": [1.0, 1.0, 1.0],
    }
    hourly.update(overrides)
    return {"hourly": hourly}


def test_hazel_is_never_reported_because_cams_has_no_such_field(monkeypatch):
    """The species table itself excludes hazel -- see module docstring and
    POLRAD_SRI_Implementation_Summary.md's "never report unavailable as zero" rule."""
    monkeypatch.setattr(pollen, "_fetch_hourly", lambda lat, lon: _pollen_payload())
    readings = pollen.at_point(lat=50.06, lon=19.94)
    assert "hazel" not in {r["key"] for r in readings}
    assert "hazel" not in pollen.SPECIES


def test_a_null_reading_is_omitted_rather_than_shown_as_zero(monkeypatch):
    payload = _pollen_payload(birch_pollen=[None, None, None])
    monkeypatch.setattr(pollen, "_fetch_hourly", lambda lat, lon: payload)
    readings = pollen.at_point(lat=50.06, lon=19.94)
    assert "birch" not in {r["key"] for r in readings}


def test_at_point_picks_the_most_recent_past_hour(monkeypatch):
    """index tracks the latest timestamp <= now; here that's the 12:00 slot."""
    payload = _pollen_payload(birch_pollen=[999.0, 80.0, 999.0])
    monkeypatch.setattr(pollen, "_fetch_hourly", lambda lat, lon: payload)
    monkeypatch.setattr(
        pollen, "datetime",
        type("_dt", (), {"now": staticmethod(lambda tz=None: datetime(2026, 8, 13, 12, 30, tzinfo=timezone.utc)),
                          "fromisoformat": staticmethod(datetime.fromisoformat)}),
    )
    readings = pollen.at_point(lat=50.06, lon=19.94)
    birch = next(r for r in readings if r["key"] == "birch")
    assert birch["value"] == pytest.approx(80.0)


def test_readings_are_sorted_heaviest_species_first(monkeypatch):
    monkeypatch.setattr(pollen, "_fetch_hourly", lambda lat, lon: _pollen_payload())
    readings = pollen.at_point(lat=50.06, lon=19.94)
    levels = [r["level_index"] for r in readings]
    assert levels == sorted(levels, reverse=True)


def test_level_thresholds_use_configured_values_when_present(monkeypatch):
    monkeypatch.setattr(pollen.settings.pollen, "thresholds", {"birch": [1.0, 2.0, 3.0]})
    assert pollen._level_index("birch", 0.5) == 0
    assert pollen._level_index("birch", 1.5) == 1
    assert pollen._level_index("birch", 2.5) == 2
    assert pollen._level_index("birch", 10.0) == 3


def test_level_thresholds_fall_back_to_defaults_when_unconfigured(monkeypatch):
    monkeypatch.setattr(pollen.settings.pollen, "thresholds", {})
    assert pollen._level_index("ragweed", 0.0) == 0
    assert pollen._level_index("ragweed", 50.0) == 3  # above the top default edge (25.0)


def test_at_point_returns_empty_list_when_the_fetch_fails(monkeypatch):
    def boom(lat, lon):
        raise RuntimeError("Open-Meteo unreachable")

    monkeypatch.setattr(pollen, "_fetch_hourly", boom)
    assert pollen.at_point(lat=50.06, lon=19.94) == []
