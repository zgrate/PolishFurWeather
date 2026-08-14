"""Local integration of POLRAD SRI rate samples into an accumulated total.

No network: this module only ever touches its own SQLite store. See
POLRAD_SRI_Implementation_Summary.md's "integration of 5-minute samples"
checklist item and the module docstring.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.providers.imgw import radar_store

LAT, LON = 50.0614, 19.9366  # Krakow, arbitrary for these tests


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("EFW_IMGW_DB_PATH", str(tmp_path / "radar.sqlite3"))
    monkeypatch.setattr(radar_store, "_connection", None)
    yield
    monkeypatch.setattr(radar_store, "_connection", None)


def _record_at(minutes_ago: float, intensity: float) -> None:
    product_time = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    radar_store.record(LAT, LON, intensity, product_time)


def test_accumulated_is_none_when_nothing_recorded():
    assert radar_store.accumulated(LAT, LON, hours=1) is None


def test_accumulated_integrates_a_steady_rate_over_five_minute_samples():
    # 4 mm/h held for three consecutive 5-minute samples -> 4 * (10/60) = 0.667mm
    # across the two gaps between them (the last sample has no following gap).
    _record_at(10, 4.0)
    _record_at(5, 4.0)
    _record_at(0, 4.0)
    total = radar_store.accumulated(LAT, LON, hours=1)
    assert total == pytest.approx(4.0 * (10 / 60), abs=0.01)


def test_accumulated_excludes_a_gap_larger_than_the_integration_ceiling():
    """A hole bigger than MAX_INTEGRATION_GAP means the radar was down, not
    that it rained at the last known rate for the whole gap."""
    _record_at(50, 10.0)  # 50 minutes ago: a 40-minute gap to the next sample
    _record_at(10, 10.0)
    _record_at(5, 10.0)
    total = radar_store.accumulated(LAT, LON, hours=1)
    # Only the 10min->5min gap (5 minutes) counts; the 50->10 gap (40min) is excluded.
    assert total == pytest.approx(10.0 * (5 / 60), abs=0.01)


def test_accumulated_only_counts_samples_inside_the_requested_window():
    _record_at(180, 20.0)  # outside a 1h window
    _record_at(5, 2.0)
    _record_at(0, 2.0)
    total = radar_store.accumulated(LAT, LON, hours=1)
    assert total == pytest.approx(2.0 * (5 / 60), abs=0.01)


def test_record_upserts_rather_than_duplicating_the_same_product_timestamp():
    product_time = datetime.now(timezone.utc)
    radar_store.record(LAT, LON, 5.0, product_time)
    radar_store.record(LAT, LON, 9.0, product_time)  # same product_time, revised value

    conn = radar_store._connect()
    rows = conn.execute(
        "SELECT intensity_mm_h FROM radar_precipitation WHERE latitude = ? AND longitude = ?",
        (round(LAT, 4), round(LON, 4)),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == pytest.approx(9.0)


def test_different_venues_are_tracked_independently():
    _record_at(5, 3.0)
    radar_store.record(52.0, 21.0, 100.0, datetime.now(timezone.utc) - timedelta(minutes=5))
    radar_store.record(52.0, 21.0, 100.0, datetime.now(timezone.utc))

    krakow = radar_store.accumulated(LAT, LON, hours=1)
    warsaw = radar_store.accumulated(52.0, 21.0, hours=1)
    assert krakow != warsaw
