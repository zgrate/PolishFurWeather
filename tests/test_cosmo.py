"""COSMO_HVD grid math and rendering. No network: _fetch_step/latest_run are stubbed."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pytest

from app.providers.http import cache, field_cache
from app.providers.imgw import cosmo


def _rot_to_true(rlat: float, rlon: float, polphi: float, pollam: float) -> tuple[float, float]:
    """Published COSMO/int2lm ``phirot2phi``/``rlarot2rla`` inverse of _true_to_rot.

    Used only to check _true_to_rot round-trips; not part of the app itself.
    """
    sinpol = math.sin(math.radians(polphi))
    cospol = math.cos(math.radians(polphi))
    lampol = math.radians(pollam)
    zphis = math.radians(rlat)
    zrlas = math.radians(rlon)
    arg = cospol * math.cos(zphis) * math.cos(zrlas) + sinpol * math.sin(zphis)
    lat = math.degrees(math.asin(max(-1.0, min(1.0, arg))))
    arg1 = math.sin(lampol) * (-sinpol * math.cos(zrlas) * math.cos(zphis) + cospol * math.sin(zphis)) \
        - math.cos(lampol) * math.sin(zrlas) * math.cos(zphis)
    arg2 = math.cos(lampol) * (-sinpol * math.cos(zrlas) * math.cos(zphis) + cospol * math.sin(zphis)) \
        + math.sin(lampol) * math.sin(zrlas) * math.cos(zphis)
    lon = math.degrees(math.atan2(arg1, arg2))
    return lat, lon


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    field_cache.clear()
    yield
    cache.clear()
    field_cache.clear()


# ------------------------------------------------------------- rotated pole


@pytest.mark.parametrize("lat, lon", [(52.0, 21.0), (50.0, 19.0), (-10.0, 170.0), (54.5, 18.6)])
def test_true_to_rot_round_trips_through_the_published_inverse(lat, lon):
    """COSMO_HVD's north pole (see module docstring): lat 40, lon -170."""
    rot_lat, rot_lon = cosmo._true_to_rot(lat, lon, 40.0, -170.0)
    back_lat, back_lon = _rot_to_true(rot_lat, rot_lon, 40.0, -170.0)
    assert back_lat == pytest.approx(lat, abs=1e-6)
    assert back_lon == pytest.approx(lon, abs=1e-6)


def test_true_to_rot_is_identity_at_the_true_north_pole():
    """A rotated grid whose pole IS the true pole changes nothing but the lon origin."""
    rot_lat, rot_lon = cosmo._true_to_rot(52.0, 21.0, 90.0, 0.0)
    assert rot_lat == pytest.approx(52.0)
    assert rot_lon == pytest.approx(-159.0)  # lon - 180, wrapped to [-180, 180]


# ------------------------------------------------------------------ CosmoStep


def _step(fields=None) -> cosmo.CosmoStep:
    # COSMO_HVD's real north pole (see module docstring); geometry chosen so
    # the rotated grid stays well clear of the antimeridian, unlike an
    # identity (90, 0) pole where lon = true_lon - 180 can wrap there.
    ni, nj = 4, 3
    grid_fields = fields or {"t_2m": np.arange(ni * nj, dtype=float).reshape(nj, ni)}
    return cosmo.CosmoStep(
        run=datetime(2026, 8, 13, 0, tzinfo=timezone.utc),
        step=0,
        valid_time=datetime(2026, 8, 13, 0, tzinfo=timezone.utc),
        ni=ni,
        nj=nj,
        la1=-4.0,
        lo1=-5.0,
        dlat=2.0,
        dlon=2.0,
        pole_lat=40.0,
        pole_lon=-170.0,
        fields=grid_fields,
    )


def test_value_at_reads_back_the_grid_point_nearest_a_true_coordinate():
    step = _step()
    values = step.fields["t_2m"]
    # With an unrotated (true-pole) grid, rot == (lat, lon - 180).
    lat, lon = _rot_to_true(step.la1, step.lo1, step.pole_lat, step.pole_lon)
    assert step.value_at(values, lat, lon) == pytest.approx(values[0, 0])


def test_value_at_returns_none_outside_the_grid():
    step = _step()
    assert step.value_at(step.fields["t_2m"], 0.0, 0.0) is None


def test_sample_grid_places_values_in_true_lat_lon_orientation():
    step = _step()
    values = step.fields["t_2m"]
    lat0, lon0 = _rot_to_true(step.la1, step.lo1, step.pole_lat, step.pole_lon)
    lat1, lon1 = _rot_to_true(step.la1 + step.dlat * (step.nj - 1), step.lo1 + step.dlon * (step.ni - 1), step.pole_lat, step.pole_lon)
    min_lat, max_lat = sorted((lat0, lat1))
    min_lon, max_lon = sorted((lon0, lon1))
    resampled = step.sample_grid(values, width=step.ni, height=step.nj, bbox=(min_lat, min_lon, max_lat, max_lon))
    assert resampled.shape == (step.nj, step.ni)
    assert np.isfinite(resampled).all()


def test_sample_grid_marks_out_of_bounds_pixels_as_nan():
    step = _step()
    far_away = (60.0, 60.0, 61.0, 61.0)
    resampled = step.sample_grid(step.fields["t_2m"], width=3, height=3, bbox=far_away)
    assert np.isnan(resampled).all()


# --------------------------------------------------------------- rain rate


def test_rain_rate_is_zero_at_step_zero(monkeypatch):
    run = datetime(2026, 8, 13, 0, tzinfo=timezone.utc)
    step0 = _step({"tot_prec": np.array([[5.0, 5.0]])})
    monkeypatch.setattr(cosmo, "_fetch_step", lambda r, s: step0)
    rate = cosmo._rain_rate_mm_h(run, 0)
    np.testing.assert_allclose(rate, [[0.0, 0.0]])


def test_rain_rate_is_the_clipped_delta_between_consecutive_steps(monkeypatch):
    run = datetime(2026, 8, 13, 0, tzinfo=timezone.utc)
    steps = {
        0: _step({"tot_prec": np.array([[10.0, 10.0]])}),
        1: _step({"tot_prec": np.array([[13.0, 8.0]])}),  # second cell dips
    }
    monkeypatch.setattr(cosmo, "_fetch_step", lambda r, s: steps[s])
    rate = cosmo._rain_rate_mm_h(run, 1)
    # 13-10=3mm; 8-10=-2mm floored to 0 rather than shown negative.
    np.testing.assert_allclose(rate, [[3.0, 0.0]])


# ------------------------------------------------------------------ render


def _fake_step_for_render() -> cosmo.CosmoStep:
    # Rotated geometry chosen (see test_true_to_rot_round_trips_...) so this
    # grid's true-coordinate footprint actually covers the bbox used below
    # (roughly 48-53N, 17-22E) under COSMO_HVD's real north pole.
    ni, nj = 6, 8
    fields = {
        "clct": np.full((nj, ni), 50.0),
        "t_2m": np.full((nj, ni), 293.15),  # 20C
        "u_10m": np.full((nj, ni), 2.0),
        "v_10m": np.full((nj, ni), 0.0),
        "tot_prec": np.zeros((nj, ni)),
    }
    return cosmo.CosmoStep(
        run=datetime(2026, 8, 13, 0, tzinfo=timezone.utc),
        step=3,
        valid_time=datetime(2026, 8, 13, 3, tzinfo=timezone.utc),
        ni=ni,
        nj=nj,
        la1=-3.0,
        lo1=3.0,
        dlat=1.0,
        dlon=1.0,
        pole_lat=40.0,
        pole_lon=-170.0,
        fields=fields,
    )


@pytest.fixture
def stub_render(monkeypatch):
    run = datetime(2026, 8, 13, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(cosmo, "latest_run", lambda: run)
    monkeypatch.setattr(cosmo, "_fetch_step", lambda r, s: _fake_step_for_render())
    return run


@pytest.mark.parametrize("param_key", ["clouds", "temperature", "wind"])
def test_render_produces_a_png_with_a_min_max_scale(stub_render, param_key):
    bbox = (48.0, 17.0, 53.0, 22.0)
    result = cosmo.render(param_key, 3, bbox, width=40)
    assert result["png"].startswith(b"\x89PNG")
    assert result["run"] == stub_render
    assert result["valid"] == datetime(2026, 8, 13, 3, tzinfo=timezone.utc)
    assert result["min"] is not None and result["max"] is not None


def test_render_rejects_an_unknown_parameter(stub_render):
    with pytest.raises(ValueError):
        cosmo.render("humidity", 3, (48.0, 17.0, 53.0, 22.0))


# ------------------------------------------------------------- describe / bands


def test_oktas_maps_cloud_percent_to_eighths():
    clct = np.array([0.0, 12.5, 50.0, 100.0])
    assert cosmo._oktas(clct).tolist() == [0.0, 1.0, 4.0, 8.0]


def test_bands_cover_the_full_parameter_range_with_no_gaps():
    bands = cosmo._bands("temperature")
    parameter = cosmo.PARAMETERS["temperature"]
    assert bands[0]["from"] == pytest.approx(parameter.vmin)
    assert bands[-1]["to"] == pytest.approx(parameter.vmax)
    for a, b in zip(bands, bands[1:]):
        assert a["to"] == pytest.approx(b["from"])


def test_cloud_bands_have_eight_okta_steps():
    bands = cosmo._bands("clouds")
    assert len(bands) == 8
    assert [b["label"] for b in bands] == [f"{n}/8" for n in range(1, 9)]


def test_describe_parameters_reports_the_current_run(stub_render):
    described = cosmo.describe_parameters((48.0, 17.0, 53.0, 22.0))
    assert described["run"] == stub_render.isoformat()
    assert described["max_step"] == cosmo.MAX_STEP
    assert set(described["parameters"]) == {"clouds", "temperature", "wind"}
    assert described["parameters"]["wind"]["arrows"] is True


def test_describe_parameters_degrades_gracefully_when_no_run_is_published(monkeypatch):
    def boom():
        raise RuntimeError("No COSMO_HVD run currently published")

    monkeypatch.setattr(cosmo, "latest_run", boom)
    described = cosmo.describe_parameters((48.0, 17.0, 53.0, 22.0))
    assert described["run"] is None
