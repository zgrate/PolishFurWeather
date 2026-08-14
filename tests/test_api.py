"""API tests. IMGW/Open-Meteo are stubbed out so the suite runs offline and deterministically."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import service
from app.config import settings
from app.main import app
from app.models import WeatherPoint, Warning
from app.providers import poland
from app.providers.http import cache, field_cache
from app.providers.imgw import cosmo as imgw_cosmo
from app.providers.imgw import radar as imgw_radar

#: A fixed run so /api/model never reaches the network for its "latest run"
#: probe -- describe_parameters() swallows a failure there into "unavailable"
#: rather than raising, which would hide a live call behind a green suite.
FIXED_RUN = datetime(2026, 8, 13, 0, tzinfo=timezone.utc)

RADAR_INFO = {
    "provider": "imgw-polrad",
    "product": "COMPO_SRI.comp.sri",
    "available": True,
    "valid_time": "2026-08-13T12:00:00+00:00",
    "bbox": {"min_lat": 52.0, "min_lon": 18.0, "max_lat": 54.0, "max_lon": 20.0},
    "image_url": "/api/radar.png",
    "attribution": (
        "Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej "
        "– Państwowy Instytut Badawczy."
    ),
    "refresh_seconds": 240,
}


@pytest.fixture(autouse=True)
def clear_cache(monkeypatch):
    cache.clear()
    field_cache.clear()
    # A real venue is left blank in config.json on purpose (see IMGWSettings'
    # docstring) -- tests stand in a fixture station rather than guessing one.
    monkeypatch.setattr(settings.imgw, "station_id", "12375")
    monkeypatch.setattr(settings.imgw, "station_name", "Kraków-Balice")
    monkeypatch.setattr(settings.imgw, "teryt", ["1261"])
    # Every summary now reads the pollen over the venue, which means a CAMS
    # file per species in season. No test may go and get one: the blanket stub
    # is here rather than in each fixture so a test added later cannot quietly
    # put the suite back on the network. Fixtures that want a reading set their
    # own on top of this.
    monkeypatch.setattr(poland, "pollen_at_point", lambda *a, **k: [])
    monkeypatch.setattr(poland, "radar_info", lambda *a, **k: dict(RADAR_INFO))
    monkeypatch.setattr(imgw_cosmo, "latest_run", lambda: FIXED_RUN)
    yield
    cache.clear()
    field_cache.clear()


#: A real pollen reading means downloading a CAMS NetCDF file per species in
#: season, so every fixture below stubs it: the suite is offline. Moderate
#: rather than high, so the default board has nothing to shout about and a
#: test that wants a warning has to ask for one.
GRASSES_MODERATE = {
    "key": "grasses",
    "value": 12.0,
    "level": "moderate",
    "level_index": 1,
    "color": "#ffd633",
    "warn": False,
    "valid": "2026-08-12",
}


def _series(hours: int = 30) -> list[WeatherPoint]:
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return [
        WeatherPoint(
            time=base + timedelta(hours=offset),
            temperature=18.0 + (offset % 8),
            dewpoint=11.0,
            humidity=62.0,
            wind_speed=2.5,
            wind_gust=5.0,
            wind_direction=270.0,
            pressure=1015.0,
            cloud_cover=40.0,
            precipitation=0.0,
            precipitation_prob=10.0,
            sunshine_minutes=30.0,
            weather_code=1,
        )
        for offset in range(hours)
    ]


@pytest.fixture
def stub_sources(monkeypatch):
    points = _series()
    monkeypatch.setattr(poland, "fetch_current", lambda *a, **k: points[0])
    # Nothing to repair in a stubbed day; see test_elapsed_gaps_are_filled.
    monkeypatch.setattr(poland, "fetch_recent", lambda *a, **k: [])
    monkeypatch.setattr(
        poland,
        "fetch_forecast",
        lambda *a, **k: {
            "points": points,
            "issued": datetime.now(timezone.utc),
            "station_name": "KRAKOW-BALICE",
            "extremes": {},
        },
    )
    monkeypatch.setattr(poland, "fetch_warnings", lambda *a, **k: [])
    monkeypatch.setattr(poland, "pollen_at_point", lambda *a, **k: [dict(GRASSES_MODERATE)])
    return points


@pytest.fixture
def client(stub_sources):
    return TestClient(app)


@pytest.fixture
def elapsed_client(monkeypatch):
    """A forecast shaped like the real one: today's elapsed hours come first.

    Open-Meteo steps that have gone by are kept so the charts can grey them
    out behind a "now" line, which means everything answering "what next" has
    to skip past them explicitly.
    """
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    points = _series(hours=36)
    for offset, point in enumerate(points):
        point.time = now - timedelta(hours=5) + timedelta(hours=offset)

    monkeypatch.setattr(poland, "fetch_current", lambda *a, **k: points[5])
    monkeypatch.setattr(poland, "fetch_recent", lambda *a, **k: [])
    monkeypatch.setattr(
        poland,
        "fetch_forecast",
        lambda *a, **k: {
            "points": points,
            "issued": now,
            "station_name": "KRAKOW-BALICE",
            "extremes": {},
        },
    )
    monkeypatch.setattr(poland, "fetch_warnings", lambda *a, **k: [])
    monkeypatch.setattr(poland, "pollen_at_point", lambda *a, **k: [dict(GRASSES_MODERATE)])
    return TestClient(app)


def _hour_now() -> datetime:
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)


def test_health_reports_ok(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["station"]


def test_summary_has_every_section(client):
    body = client.get("/api/summary").json()

    assert body["current"]["temperature"] == 18.0
    assert body["fsi"]["score"] > 0
    assert set(body["fsi"]["subscores"]) == {
        "thermal_humidity",
        "precipitation",
        "wind",
        "stickiness",
    }
    assert body["fsi_series"]
    assert body["daily"]
    assert body["radar"]["provider"]
    assert body["degraded"] == []


def test_summary_localises_times(client):
    body = client.get("/api/summary").json()
    # Europe/Berlin is UTC+1 or +2, never UTC.
    assert body["current"]["time_local"].endswith(("+01:00", "+02:00"))
    assert ":" in body["current"]["hour"]


def test_daily_cards_carry_fsi_and_weather(client):
    day = client.get("/api/summary").json()["daily"][0]
    assert day["weekday"]
    assert day["fsi_max"] is not None
    assert day["weather"]["text"]


def test_index_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Fursuiting Index" in response.text


def test_static_assets_are_served(client):
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200


def test_summary_degrades_when_observations_fail(monkeypatch, stub_sources):
    def boom(*args, **kwargs):
        raise RuntimeError("IMGW unreachable")

    monkeypatch.setattr(poland, "fetch_current", boom)
    body = TestClient(app).get("/api/summary").json()

    # The forecast still carries the page; the failure is reported, not fatal.
    assert body["degraded"] == ["observations unavailable"]
    assert body["current"] is not None
    assert body["daily"]


def test_summary_fails_loudly_when_every_source_is_down(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("IMGW unreachable")

    monkeypatch.setattr(poland, "fetch_current", boom)
    monkeypatch.setattr(poland, "fetch_forecast", boom)
    monkeypatch.setattr(poland, "fetch_warnings", boom)

    assert TestClient(app).get("/api/summary").status_code == 503


def test_radar_image_is_served_when_a_frame_is_available(client, monkeypatch):
    monkeypatch.setattr(
        imgw_radar,
        "render",
        lambda bbox, width, height: {
            "png": b"\x89PNG\r\n\x1a\n" + b"0" * 16,
            "valid": datetime.now(timezone.utc),
            "min": 0.0,
            "max": 3.5,
        },
    )
    response = client.get("/api/radar.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_radar_image_404s_without_a_fresh_frame(client, monkeypatch):
    """A missing/stale POLRAD frame is an answer, not a fabricated picture."""
    monkeypatch.setattr(imgw_radar, "render", lambda bbox, width, height: None)
    assert client.get("/api/radar.png").status_code == 404


def test_summary_carries_the_pollen_reading(client):
    """The board raises its own warning from this, so it has to be in the payload."""
    reading = client.get("/api/summary").json()["pollen"][0]
    assert reading["key"] == "grasses"
    assert reading["level"] == "moderate"
    assert reading["warn"] is False


def test_pollen_never_takes_the_page_down_with_it(monkeypatch, stub_sources):
    """A research forecast off a once-daily file is the least important thing
    on the page, and must never be the reason the weather does not load."""

    def boom(*args, **kwargs):
        raise RuntimeError("air-quality-api.open-meteo.com unreachable")

    monkeypatch.setattr(poland, "pollen_at_point", boom)
    body = TestClient(app).get("/api/summary").json()

    assert body["pollen"] == []
    assert body["current"] is not None
    assert body["daily"]


def test_radar_block_names_the_provider(client):
    """The map needs an image URL and the area, and nothing else."""
    radar = client.get("/api/summary").json()["radar"]
    assert radar["provider"]
    assert radar["image_url"]
    assert {"min_lat", "min_lon", "max_lat", "max_lon"} <= set(radar["bbox"])
    assert "age_seconds" not in radar


def test_best_window_is_reported(client):
    body = client.get("/api/summary").json()
    window = body["best_window"]
    assert window is None or {"start", "end", "hours", "peak_score"} <= set(window)


def test_best_and_worst_windows_do_not_overlap(client):
    """One is the stretch worth going out in, the other the stretch to avoid."""
    body = client.get("/api/summary").json()
    best, worst = body["best_window"], body["worst_window"]
    if best and worst:
        assert best["start"] != worst["start"]


def test_each_day_carries_an_hourly_series(client):
    """The per-day bar charts need one entry per forecast hour of that day."""
    for day in client.get("/api/summary").json()["daily"]:
        assert day["series"], f"{day['date']} has no hourly series"
        assert len(day["series"]) == day["hour_count"]
        for entry in day["series"]:
            assert 0.0 <= entry["score"] <= 10.0
            assert entry["color"].startswith("#")
            assert len(entry["hour"]) == 2


def test_days_report_best_and_worst_hours(client):
    for day in client.get("/api/summary").json()["daily"]:
        assert day["fsi_min"] <= day["fsi_max"]
        assert day["fsi_best_hour"] and day["fsi_worst_hour"]


def _scored(scores: list[float], skip: set[int] = frozenset()) -> list:
    """(point, score) pairs an hour apart, with `skip` leaving a gap."""
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    pairs, hour = [], 0
    for score in scores:
        while hour in skip:
            hour += 1
        pairs.append((WeatherPoint(time=base + timedelta(hours=hour)), score))
        hour += 1
    return pairs


def _span(window) -> tuple:
    return (window["hours"], window["peak_score"]) if window else None


def test_day_best_window_grows_out_from_the_peak():
    """The hours either side of the best one are usually just as good, and a
    single hour is a poor answer to "when should I suit up"."""
    tz = service._local_tz()
    assert _span(service._peak_window(_scored([5.0, 9.8, 9.9, 9.6, 4.0]), True, tz)) == (3, 9.9)


def test_day_worst_window_grows_the_same_way():
    tz = service._local_tz()
    assert _span(service._peak_window(_scored([9.0, 3.2, 3.0, 8.0]), False, tz)) == (2, 3.0)


def test_day_window_stops_at_the_tolerance():
    """A hour a full point below the peak is a different kind of hour."""
    tz = service._local_tz()
    assert _span(service._peak_window(_scored([9.9, 9.0]), True, tz)) == (1, 9.9)


def test_day_window_does_not_bridge_a_gap_in_the_forecast():
    tz = service._local_tz()
    assert _span(service._peak_window(_scored([9.9, 9.9], skip={1}), True, tz)) == (1, 9.9)


def test_day_window_of_nothing_is_nothing():
    assert service._peak_window([], True, service._local_tz()) is None


def test_days_report_best_and_worst_windows(client):
    """The day row names a stretch, on whichever clock the reader picked, so the
    payload carries timestamps rather than a formatted "07:00"."""
    for day in client.get("/api/summary").json()["daily"]:
        window = day["fsi_best_window"]
        assert {"start", "end", "hours", "peak_score"} <= set(window)
        assert window["end"] > window["start"]
        # Identical windows would name one hour as both the best and the worst.
        assert day["fsi_worst_window"] != window


def test_days_report_a_wind_direction(client):
    """The day rows name it beside the wind speed, as "12-30 km/h W"."""
    for day in client.get("/api/summary").json()["daily"]:
        assert day["wind_direction"] == pytest.approx(270.0)
        assert day["wind_direction_name"] == "W"


def _day_wind(bearings: list[float]) -> float | None:
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return service._mean_wind_direction(
        [
            WeatherPoint(time=base + timedelta(hours=offset), wind_speed=3.0, wind_direction=value)
            for offset, value in enumerate(bearings)
        ]
    )


def test_day_wind_direction_is_a_vector_mean():
    """Averaging the bearings would put a day blowing from 350 deg and 10 deg on
    a due-south wind -- the exact opposite of the truth."""
    assert _day_wind([350.0, 10.0]) == pytest.approx(0.0, abs=0.1)
    assert _day_wind([90.0, 90.0]) == pytest.approx(90.0, abs=0.1)
    assert _day_wind([0.0, 180.0]) is None  # cancels out exactly
    assert _day_wind([]) is None


def test_day_series_colours_match_the_score_bands(client):
    from app.service import _score_color

    for day in client.get("/api/summary").json()["daily"]:
        for entry in day["series"]:
            assert entry["color"] == _score_color(entry["score"])


def test_display_page_is_served(client):
    response = client.get("/display")
    assert response.status_code == 200
    assert "ConOps" in response.text


def test_display_assets_are_served(client):
    for path in ("/display.js", "/display.css", "/chart.js"):
        assert client.get(path).status_code == 200, path


def test_pages_and_scripts_must_revalidate(client):
    """A cached app.js against fresh HTML looks up ids that no longer exist
    and kills the page, so markup and its scripts may not be held blindly."""
    for path in ("/", "/display", "/app.js", "/display.js", "/chart.js", "/style.css"):
        cache_control = client.get(path).headers.get("cache-control", "")
        assert "no-cache" in cache_control, f"{path} -> {cache_control!r}"


def test_vendor_assets_may_be_cached(client):
    """Leaflet is version-pinned under a stable name, so it can be held."""
    cache_control = client.get("/vendor/leaflet.js").headers.get("cache-control", "")
    assert "max-age" in cache_control and "no-cache" not in cache_control


def test_static_assets_still_revalidate_cheaply(client):
    """Revalidation must return 304, not the whole file again."""
    first = client.get("/app.js")
    etag = first.headers.get("etag")
    assert etag
    again = client.get("/app.js", headers={"If-None-Match": etag})
    assert again.status_code == 304


def test_summary_publishes_the_event_name(client):
    event = client.get("/api/summary").json()["event"]
    assert event["name"] and event["short_name"]


def test_summary_publishes_the_band_scale(client):
    """The frontend colours bars from this, so it must not drift from the engine."""
    from app import fsi

    bands = client.get("/api/summary").json()["bands"]
    assert [b["color"] for b in bands] == [color for _, _, color in fsi.BANDS]
    assert [b["min"] for b in bands] == [minimum for minimum, _, _ in fsi.BANDS]


def test_series_entries_carry_a_weather_icon(client):
    """Icons above the bars show how the weather builds through the day."""
    body = client.get("/api/summary").json()
    assert any(entry.get("icon") for entry in body["fsi_series"])
    for day in body["daily"]:
        assert all("icon" in entry for entry in day["series"])


def test_every_bar_carries_the_conditions_behind_it(client):
    """Clicking a bar opens the weather for that hour, on the day cards too --
    which reach further out than the old 48-hour "hourly" list did."""
    for entry in client.get("/api/summary").json()["fsi_series"]:
        assert entry["weather"]["text"]
        assert entry["temperature"] is not None
        assert entry["wind_speed_kmh"] is not None
        assert "precipitation_prob" in entry
        assert entry["wetbulb"] is not None


def test_elapsed_hours_stay_on_the_chart(elapsed_client):
    body = elapsed_client.get("/api/summary").json()
    elapsed = [e for e in body["fsi_series"] if datetime.fromisoformat(e["time"]) < _hour_now()]

    assert elapsed, "the charts need the hours that have gone by to grey them out"


def test_advice_never_points_at_an_hour_that_has_gone(elapsed_client):
    """The series carries elapsed hours; the best and worst stretches must not."""
    body = elapsed_client.get("/api/summary").json()

    for key in ("best_window", "worst_window"):
        window = body[key]
        if window:
            assert datetime.fromisoformat(window["start"]) >= _hour_now(), key


def test_todays_best_hour_is_still_ahead(elapsed_client):
    body = elapsed_client.get("/api/summary").json()
    today = body["daily"][0]

    # Only meaningful while some of today's suiting hours are left; after that
    # the day falls back to reporting the whole day it had.
    remaining = [
        entry
        for entry in today["series"]
        if datetime.fromisoformat(entry["time"]) >= _hour_now() and 7 <= int(entry["hour"]) <= 22
    ]
    if remaining:
        assert today["fsi_best_hour"] >= min(entry["hour"] + ":00" for entry in remaining)


def test_current_falls_back_to_the_hour_we_are_in(monkeypatch, elapsed_client):
    """Without an observation the fallback used to be points[0], which is now
    the first hour of *today*, not the hour we are in."""
    monkeypatch.setattr(poland, "fetch_current", lambda *a, **k: None)
    cache.clear()
    field_cache.clear()

    body = elapsed_client.get("/api/summary").json()
    assert datetime.fromisoformat(body["current"]["time"]) == _hour_now()


def _stub_sources(monkeypatch, now, forecast, observed):
    """Both IMGW/Open-Meteo sources under a clock pinned to `now`.

    The repair only has work to do part-way through a local day, so the hour has
    to be chosen rather than inherited from whenever the suite happens to run --
    at 00:xx local nothing has elapsed yet and the test would prove nothing.
    """
    monkeypatch.setattr(service, "hour_now", lambda: now)
    monkeypatch.setattr(poland, "fetch_current", lambda *a, **k: observed[-1])
    monkeypatch.setattr(poland, "fetch_recent", lambda *a, **k: observed)
    monkeypatch.setattr(
        poland,
        "fetch_forecast",
        lambda *a, **k: {
            "points": forecast,
            "issued": now,
            "station_name": "KRAKOW-BALICE",
            "extremes": {},
        },
    )
    monkeypatch.setattr(poland, "fetch_warnings", lambda *a, **k: [])
    monkeypatch.setattr(poland, "pollen_at_point", lambda *a, **k: [dict(GRASSES_MODERATE)])
    return TestClient(app)


def _local_midday() -> datetime:
    """12:00 local today, in UTC -- a point with a morning behind it."""
    local = datetime.now(service._local_tz()).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    return local.astimezone(timezone.utc)


def test_elapsed_gaps_are_filled_from_observations(monkeypatch):
    """A restart used to lose today's greyed-out hours until midnight.

    Open-Meteo's own history only remembers the hours a newer run dropped for
    as long as the process lives, so a deploy at midday left the chart
    starting at whenever the current run begins. IMGW's SYNOP history carries
    the same hours as measurements, so they come back from there instead.
    """
    now = _local_midday()
    # A run that begins in an hour, exactly as a fresh Open-Meteo fetch does.
    forecast = _series(hours=12)
    for offset, point in enumerate(forecast):
        point.time = now + timedelta(hours=offset + 1)

    observed = _series(hours=6)
    for offset, point in enumerate(observed):
        point.time = now - timedelta(hours=5 - offset)
        point.source = "synop"

    client = _stub_sources(monkeypatch, now, forecast, observed)
    series = client.get("/api/summary").json()["fsi_series"]
    times = [datetime.fromisoformat(entry["time"]) for entry in series]

    # Every observed hour is back, including the one we are in -- that is the
    # hour the now line is drawn in, and it falls between the two sources.
    for point in observed:
        assert point.time in times
    assert times == sorted(times)
    assert len(set(times)) == len(times)  # never both an observation and a forecast


def test_observed_hours_never_overwrite_the_forecast(monkeypatch):
    """Only gaps are filled: an hour the forecast still covers stays as it is."""
    now = _local_midday()
    forecast = _series(hours=12)
    for offset, point in enumerate(forecast):
        point.time = now - timedelta(hours=2) + timedelta(hours=offset)

    observed = _series(hours=5)
    for offset, point in enumerate(observed):
        point.time = now - timedelta(hours=4 - offset)
        point.temperature = -40.0  # unmistakable if it leaks through
        point.source = "synop"

    client = _stub_sources(monkeypatch, now, forecast, observed)
    series = {
        entry["time"]: entry for entry in client.get("/api/summary").json()["fsi_series"]
    }

    # The two hours the forecast does not reach come from the observations...
    for offset in (4, 3):
        assert series[(now - timedelta(hours=offset)).isoformat()]["temperature"] == -40.0
    # ...and the ones it does reach are untouched.
    for offset in (2, 1, 0):
        assert series[(now - timedelta(hours=offset)).isoformat()]["temperature"] != -40.0


def test_series_colours_come_from_the_engine(client):
    from app import fsi

    body = client.get("/api/summary").json()
    for entry in body["fsi_series"]:
        assert entry["color"] == fsi.band_color(entry["score"])


def test_german_bands_are_translated(client):
    labels = [b["label"] for b in client.get("/api/summary?lang=de").json()["bands"]]
    assert "Ausgezeichnet" in labels


def test_model_info_describes_every_layer(client):
    body = client.get("/api/model").json()
    assert set(body["parameters"]) == {"clouds", "temperature", "wind"}
    # Clouds carry rain on top; wind carries arrows and no isolines any more.
    assert body["parameters"]["clouds"]["overlay"]["unit"] == "mm/h"
    assert body["parameters"]["wind"]["arrows"] is True
    assert "contour_step" not in body["parameters"]["wind"]


def test_model_layers_publish_the_steps_they_are_drawn_in(client):
    """The key is drawn from these, so a band the legend prints is a band the
    renderer painted -- two hand-kept lists would drift."""
    layers = client.get("/api/model").json()["parameters"]

    for key, expected in (("clouds", 8), ("temperature", 21), ("wind", 12)):
        bands = layers[key]["bands"]
        assert len(bands) == expected
        assert bands[0]["from"] == layers[key]["min"]
        assert bands[-1]["to"] == layers[key]["max"]
        # Contiguous, in order, and every one of them coloured.
        for lower, upper in zip(bands, bands[1:]):
            assert lower["to"] == upper["from"]
        assert all(band["color"].startswith("rgba(") for band in bands)


def test_cloud_bands_are_eighths_from_one_to_full():
    """Sky cover is reported in oktas; 0/8 has no swatch because a clear sky is
    drawn as nothing at all."""
    bands = imgw_cosmo._bands("clouds")
    assert [band["label"] for band in bands] == [f"{n}/8" for n in range(1, 9)]


def test_cloud_field_rounds_up_to_the_next_okta():
    """Any cloud at all is at least 1/8, and only a truly clear sky is 0."""
    values = np.array([0.0, 0.1, 12.5, 12.6, 99.9, 100.0])
    assert list(imgw_cosmo._oktas(values)) == [0, 1, 1, 2, 8, 8]


def test_temperature_bands_are_two_degrees_on_even_numbers():
    bands = imgw_cosmo._bands("temperature")
    assert all(band["to"] - band["from"] == 2.0 for band in bands)
    assert all(band["from"] % 2 == 0 for band in bands)


def test_model_bbox_is_wider_than_tall(client):
    """The model card's map is wide, so the field is cut to match or it would
    sit in the frame with empty margins."""
    box = client.get("/api/model").json()["bbox"]
    lat_span = box["max_lat"] - box["min_lat"]
    lon_span = (box["max_lon"] - box["min_lon"]) * 0.6  # degrees -> ground scale
    assert lon_span / lat_span > 1.8


def test_easter_egg_is_exposed_on_the_api(client):
    body = client.get("/api/summary").json()
    assert "easter_egg" in body["fsi"]


def test_media_is_served(client):
    response = client.get("/media/ravi_67.mp4")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("video/")
