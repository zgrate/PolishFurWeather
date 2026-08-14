"""POLRAD SRI (ODIM_H5) decoding and venue sampling. No network.

Fixture files are real HDF5 (via h5py) so _decode exercises the exact
group/attribute layout confirmed against a live COMPO_SRI.comp.sri file --
see the module docstring in app/providers/imgw/radar.py. Only the pixel grid
is shrunk (10x10 instead of 800x800) to keep the fixtures small.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import h5py
import numpy as np
import pytest
from pyproj import Transformer

from app.config import settings
from app.providers.http import field_cache
from app.providers.imgw import radar

PROJ4 = "+proj=aeqd +lon_0=19.0926 +lat_0=52.3469 +ellps=sphere"
XSCALE = YSCALE = 1000.0  # metres/pixel
GRID = 10  # 10x10 pixels = 10km x 10km, centred on the projection origin

_to_proj = Transformer.from_crs("EPSG:4326", PROJ4, always_xy=True)
_to_lonlat = Transformer.from_crs(PROJ4, "EPSG:4326", always_xy=True)

# UL corner 5km west, 5km north of the projection centre.
_UL_X, _UL_Y = -5000.0, 5000.0
_UL_LON, _UL_LAT = _to_lonlat.transform(_UL_X, _UL_Y)
_LR_LON, _LR_LAT = _to_lonlat.transform(_UL_X + GRID * XSCALE, _UL_Y - GRID * YSCALE)
_UR_LON, _UR_LAT = _to_lonlat.transform(_UL_X + GRID * XSCALE, _UL_Y)
_LL_LON, _LL_LAT = _to_lonlat.transform(_UL_X, _UL_Y - GRID * YSCALE)


def _pixel_lonlat(row: int, col: int) -> tuple[float, float]:
    """Lon/lat of a pixel's top-left corner -- pixel_of rounds (x-x_ul)/scale,
    and a corner (unlike a .5-offset centre) rounds back to the same index
    unambiguously."""
    x = _UL_X + col * XSCALE
    y = _UL_Y - row * YSCALE
    return _to_lonlat.transform(x, y)


def _write_h5(
    path,
    *,
    data: np.ndarray,
    gain: float = 1.0,
    offset: float = 0.0,
    nodata: float = -2.0,
    undetect: float = -1.0,
    date: str = "20260813",
    time_: str = "120000",
    product: str = "PCAPPI",
    quantity: str = "RATE",
) -> None:
    with h5py.File(path, "w") as f:
        f.attrs["Conventions"] = "ODIM_H5/V2_3"
        dataset1 = f.create_group("dataset1")
        what = dataset1.create_group("what")
        what.attrs["product"] = product
        what.attrs["quantity"] = quantity
        what.attrs["gain"] = gain
        what.attrs["offset"] = offset
        what.attrs["nodata"] = nodata
        what.attrs["undetect"] = undetect
        what.attrs["startdate"] = date
        what.attrs["starttime"] = time_
        data1 = dataset1.create_group("data1")
        data1.create_dataset("data", data=data.astype(np.float32))

        where = f.create_group("where")
        where.attrs["projdef"] = PROJ4
        where.attrs["UL_lon"], where.attrs["UL_lat"] = _UL_LON, _UL_LAT
        where.attrs["UR_lon"], where.attrs["UR_lat"] = _UR_LON, _UR_LAT
        where.attrs["LL_lon"], where.attrs["LL_lat"] = _LL_LON, _LL_LAT
        where.attrs["LR_lon"], where.attrs["LR_lat"] = _LR_LON, _LR_LAT
        where.attrs["xscale"] = XSCALE
        where.attrs["yscale"] = YSCALE
        where.attrs["xsize"] = data.shape[1]
        where.attrs["ysize"] = data.shape[0]


def _payload(tmp_path, name="frame.sri.h5", **kwargs) -> bytes:
    path = tmp_path / name
    grid_data = kwargs.pop("data", None)
    if grid_data is None:
        grid_data = np.arange(GRID * GRID, dtype=np.float64).reshape(GRID, GRID) * 0.1
    _write_h5(path, data=grid_data, **kwargs)
    return path.read_bytes()


@pytest.fixture(autouse=True)
def clear_cache():
    field_cache.clear()
    yield
    field_cache.clear()


# --------------------------------------------------------------------- decode


def test_decode_parses_the_documented_fields(tmp_path):
    payload = _payload(tmp_path, gain=0.5, offset=1.5, nodata=-2.0, undetect=-1.0)
    decoded = radar._decode(payload)
    assert decoded.gain == pytest.approx(0.5)
    assert decoded.offset == pytest.approx(1.5)
    assert decoded.nodata == pytest.approx(-2.0)
    assert decoded.undetect == pytest.approx(-1.0)
    assert decoded.proj4 == PROJ4
    assert decoded.xscale == pytest.approx(XSCALE)
    assert decoded.yscale == pytest.approx(YSCALE)
    assert (decoded.xsize, decoded.ysize) == (GRID, GRID)
    assert decoded.data.shape == (GRID, GRID)


def test_decode_extracts_the_timestamp(tmp_path):
    payload = _payload(tmp_path, date="20260813", time_="143000")
    decoded = radar._decode(payload)
    assert decoded.valid_time == datetime(2026, 8, 13, 14, 30, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------- coordinate -> grid


def test_pixel_of_converts_a_known_point_to_its_grid_cell(tmp_path):
    decoded = radar._decode(_payload(tmp_path))
    lon, lat = _pixel_lonlat(row=3, col=7)
    assert decoded.pixel_of(lat, lon) == (3, 7)


def test_pixel_of_returns_none_outside_the_grid(tmp_path):
    decoded = radar._decode(_payload(tmp_path))
    # Two full grid-widths west of the UL corner.
    far_lon, far_lat = _to_lonlat.transform(_UL_X - 2 * GRID * XSCALE, _UL_Y)
    assert decoded.pixel_of(far_lat, far_lon) is None


# ------------------------------------------------------------------- value_at


def test_value_at_applies_gain_and_offset(tmp_path):
    data = np.zeros((GRID, GRID))
    data[3, 7] = 5.0
    decoded = radar._decode(_payload(tmp_path, data=data, gain=0.5, offset=1.0))
    lon, lat = _pixel_lonlat(row=3, col=7)
    assert decoded.value_at(lat, lon) == pytest.approx(5.0 * 0.5 + 1.0)


def test_value_at_nodata_becomes_none(tmp_path):
    data = np.zeros((GRID, GRID))
    data[2, 2] = -2.0  # nodata sentinel
    decoded = radar._decode(_payload(tmp_path, data=data, nodata=-2.0))
    lon, lat = _pixel_lonlat(row=2, col=2)
    assert decoded.value_at(lat, lon) is None


def test_value_at_undetect_becomes_zero_not_none(tmp_path):
    """Covered-but-dry is a real reading (0 mm/h), unlike nodata."""
    data = np.zeros((GRID, GRID))
    data[4, 4] = -1.0  # undetect sentinel
    decoded = radar._decode(_payload(tmp_path, data=data, undetect=-1.0, gain=2.0, offset=100.0))
    lon, lat = _pixel_lonlat(row=4, col=4)
    assert decoded.value_at(lat, lon) == pytest.approx(0.0)


def test_value_at_outside_the_grid_is_none(tmp_path):
    decoded = radar._decode(_payload(tmp_path))
    far_lon, far_lat = _to_lonlat.transform(_UL_X - 2 * GRID * XSCALE, _UL_Y)
    assert decoded.value_at(far_lat, far_lon) is None


# -------------------------------------------------------------------- _fresh


def test_fresh_accepts_a_recent_frame(monkeypatch):
    monkeypatch.setattr(settings.imgw, "radar_max_age_minutes", 20)
    field = radar._decode(_payload_for_fresh(minutes_old=5))
    assert radar._fresh(field) is True


def test_fresh_rejects_a_stale_frame(monkeypatch):
    monkeypatch.setattr(settings.imgw, "radar_max_age_minutes", 20)
    field = radar._decode(_payload_for_fresh(minutes_old=45))
    assert radar._fresh(field) is False


def _payload_for_fresh(minutes_old: int) -> bytes:
    import tempfile
    from pathlib import Path

    stamp = datetime.now(timezone.utc) - timedelta(minutes=minutes_old)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fresh.sri.h5"
        _write_h5(
            path,
            data=np.zeros((GRID, GRID)),
            date=stamp.strftime("%Y%m%d"),
            time_=stamp.strftime("%H%M%S"),
        )
        return path.read_bytes()


# --------------------------------------------------------------- latest_field


def test_latest_field_returns_none_when_the_fetch_fails(monkeypatch):
    def boom():
        raise RuntimeError("IMGW unreachable")

    monkeypatch.setattr(radar, "_fetch_latest", boom)
    assert radar.latest_field() is None


def test_latest_field_returns_none_for_a_stale_frame(monkeypatch, tmp_path):
    monkeypatch.setattr(settings.imgw, "radar_max_age_minutes", 20)
    stale = radar._decode(_payload_for_fresh(minutes_old=999))
    monkeypatch.setattr(radar, "_fetch_latest", lambda: stale)
    assert radar.latest_field() is None


def test_latest_field_returns_the_frame_when_fresh(monkeypatch):
    monkeypatch.setattr(settings.imgw, "radar_max_age_minutes", 20)
    fresh = radar._decode(_payload_for_fresh(minutes_old=1))
    monkeypatch.setattr(radar, "_fetch_latest", lambda: fresh)
    assert radar.latest_field() is fresh


# ---------------------------------------------------------- precipitation_intensity


def test_precipitation_intensity_is_none_when_radar_unavailable(monkeypatch):
    monkeypatch.setattr(radar, "latest_field", lambda: None)
    value, valid_time = radar.precipitation_intensity(50.0, 19.0)
    assert (value, valid_time) == (None, None)


def test_precipitation_intensity_reads_the_venue_point(monkeypatch, tmp_path):
    data = np.zeros((GRID, GRID))
    data[5, 5] = 8.0
    decoded = radar._decode(_payload(tmp_path, data=data, gain=1.0, offset=0.0))
    monkeypatch.setattr(radar, "latest_field", lambda: decoded)
    lon, lat = _pixel_lonlat(row=5, col=5)
    value, valid_time = radar.precipitation_intensity(lat, lon)
    assert value == pytest.approx(8.0)
    assert valid_time == decoded.valid_time
