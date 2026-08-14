"""Tests for the public v1 API -- the contract third parties depend on.

IMGW/Open-Meteo are stubbed so the suite stays offline. These assertions are
deliberately about *field names and shapes*: if one changes, a consumer
breaks, and the test should be the thing that notices.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import i18n as app_i18n
from app import main
from app.api_v1 import WIND_FACTORS
from app.config import settings
from app.main import app
from app.models import WeatherPoint, Warning
from app.providers import poland

ENDPOINTS = (
    "/api/v1/fsi",
    "/api/v1/current",
    "/api/v1/forecast",
    "/api/v1/daily",
    "/api/v1/warnings",
    "/api/v1/scale",
    "/api/v1/overview",
)

@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    from app.providers.http import cache, field_cache

    cache.clear()
    field_cache.clear()
    main.limiter.reset()
    # A real venue is left blank in config.json on purpose (see IMGWSettings'
    # docstring) -- tests stand in a fixture station rather than guessing one.
    monkeypatch.setattr(settings.imgw, "station_id", "12375")
    monkeypatch.setattr(settings.imgw, "station_name", "Kraków-Balice")
    monkeypatch.setattr(settings.imgw, "teryt", ["1261"])
    # The summary behind every v1 endpoint reads pollen, which costs a live
    # fetch. Not from here it is not: the suite stays offline.
    monkeypatch.setattr(poland, "pollen_at_point", lambda *a, **k: [])
    yield
    cache.clear()
    field_cache.clear()
    main.limiter.reset()


def _points(hours: int = 30) -> list[WeatherPoint]:
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return [
        WeatherPoint(
            time=base + timedelta(hours=offset),
            temperature=18.0 + (offset % 6),
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


def _warning() -> Warning:
    now = datetime.now(timezone.utc)
    return Warning(
        event="Upał",
        event_en="Heat warning (moderate)",
        headline="Ostrzeżenie meteorologiczne: Upał",
        description="Silne oddziaływanie termiczne.",
        instruction="Pij odpowiednią ilość wody.",
        severity="moderate",
        level=2,
        kind="heat",
        region="Kraków",
        # IMGW has no "Vorabinformation"-equivalent tier -- advance is always
        # False for its warnings; see models.Warning's docstring.
        advance=False,
        start=now,
        end=now + timedelta(hours=6),
        color="#fb8c00",
    )


@pytest.fixture
def client(monkeypatch):
    points = _points()
    monkeypatch.setattr(poland, "fetch_current", lambda *a, **k: points[0])
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
    monkeypatch.setattr(poland, "fetch_warnings", lambda *a, **k: [_warning(), _warning()])
    return TestClient(app)


# ------------------------------------------------------------ availability


@pytest.mark.parametrize("path", ENDPOINTS)
def test_every_endpoint_answers(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"].startswith("public")


@pytest.mark.parametrize("path", ENDPOINTS)
def test_every_endpoint_carries_provenance(client, path):
    """A consumer must be able to tell where the numbers came from."""
    meta = client.get(path).json()["meta"]
    assert meta["event"] and meta["location"] and meta["station_id"]
    assert "IMGW" in meta["attribution"]
    assert meta["language"] == "en"
    assert meta["timezone"] == "Europe/Berlin"


# ------------------------------------------------------------------ shapes


def test_fsi_shape_is_the_documented_contract(client):
    body = client.get("/api/v1/fsi").json()
    assert 0.0 <= body["score"] <= 10.0
    assert body["band"] and body["band_key"] and body["color"].startswith("#")
    assert body["advice"]
    assert {s["key"] for s in body["subscores"]} == {
        "thermal_humidity",
        "precipitation",
        "wind",
        "stickiness",
    }
    for sub in body["subscores"]:
        assert 0.0 <= sub["score"] <= 10.0
        assert 0.0 <= sub["weight"] <= 1.0


def test_fsi_is_small_enough_for_a_bot(client):
    """The point of the focused endpoints: the internal aggregate is ~60 kB."""
    focused = len(client.get("/api/v1/fsi").content)
    aggregate = len(client.get("/api/summary").content)
    assert focused < 4000
    assert focused < aggregate / 5


def test_units_are_named_in_the_field(client):
    body = client.get("/api/v1/current").json()
    for field in ("temperature_c", "wind_speed_kmh", "pressure_hpa", "humidity_percent"):
        assert field in body


def test_forecast_honours_the_hours_parameter(client):
    assert len(client.get("/api/v1/forecast?hours=6").json()["hours"]) == 6
    assert len(client.get("/api/v1/forecast?hours=12").json()["hours"]) == 12


def test_forecast_hours_join_score_and_weather(client):
    hour = client.get("/api/v1/forecast?hours=3").json()["hours"][0]
    assert 0.0 <= hour["score"] <= 10.0
    assert hour["temperature_c"] is not None
    assert hour["weather"]["text"]


def test_forecast_starts_at_the_hour_we_are_in(client):
    """The internal series keeps today's elapsed hours so the site can grey them
    out. "hours from now" must not be spent on them."""
    from datetime import datetime, timezone

    first = client.get("/api/v1/forecast?hours=3").json()["hours"][0]
    assert datetime.fromisoformat(first["time"]).astimezone(timezone.utc) >= datetime.now(
        timezone.utc
    ).replace(minute=0, second=0, microsecond=0)


def test_forecast_carries_weather_past_the_first_48_hours(client):
    """It used to join against a 48-entry list, so anything beyond that came
    back with a score and nothing else."""
    hours = client.get("/api/v1/forecast?hours=120").json()["hours"]
    assert hours[-1]["temperature_c"] is not None
    assert hours[-1]["weather"]["text"]


def test_forecast_rejects_an_out_of_range_horizon(client):
    assert client.get("/api/v1/forecast?hours=0").status_code == 422
    assert client.get("/api/v1/forecast?hours=999").status_code == 422


def test_daily_reports_best_and_worst(client):
    for day in client.get("/api/v1/daily").json()["days"]:
        assert day["date"] and day["weekday"]
        if day["fsi_max"] is not None:
            assert day["fsi_min"] <= day["fsi_max"]


def test_a_warning_in_force_does_not_move_the_score(client):
    """Warnings are published beside the index, not folded into it.

    The fixture has a heat warning valid right now; under the old cap that alone
    forced the score down to 2.0 regardless of the actual weather.
    """
    from app import fsi

    body = client.get("/api/v1/fsi").json()
    assert client.get("/api/v1/warnings").json()["count"] == 2
    assert body["score"] == fsi.compute(_points()[0]).score
    assert body["score"] > 2.0


def test_warnings_carry_the_expected_fields(client):
    body = client.get("/api/v1/warnings").json()
    assert body["count"] == 2
    # IMGW has no "advance notice" tier the way DWD's Vorabinformation is --
    # every IMGW-sourced warning is always False here, not fabricated.
    assert {w["advance"] for w in body["warnings"]} == {False}
    for warning in body["warnings"]:
        assert warning["headline"] and warning["label"]
        assert warning["kind"] and warning["severity"]


def test_scale_matches_the_engine(client):
    from app import fsi

    body = client.get("/api/v1/scale").json()
    assert [b["color"] for b in body["bands"]] == [c for _, _, c in fsi.BANDS]
    assert body["suitable_from"] == 5.0


def test_overview_bundles_the_same_shapes(client):
    body = client.get("/api/v1/overview").json()
    assert body["fsi"]["score"] == client.get("/api/v1/fsi").json()["score"]
    assert body["warnings"]["count"] == client.get("/api/v1/warnings").json()["count"]
    assert body["forecast"]["hours"]
    assert body["scale"]["bands"]


# --------------------------------------------------------------- languages


def test_german_translates_generated_text(client):
    body = client.get("/api/v1/fsi?lang=de").json()
    assert body["meta"]["language"] == "de"
    # Read off app.i18n rather than copied out of it: the hard-coded list here
    # had drifted to the *frontend's* German wording ("Mäßig", "Vorsicht"),
    # which the API never returns, so the assertion only held for as long as
    # the fixture never landed in those two bands.
    keys = ("excellent", "good", "fair", "poor", "bad")
    assert body["band"] in {app_i18n.t("de", f"band.{key}") for key in keys}
    # The key stays stable across languages -- that is what clients switch on.
    assert body["band_key"] in set(keys)


def test_polish_translates_generated_text(client):
    body = client.get("/api/v1/fsi?lang=pl").json()
    assert body["meta"]["language"] == "pl"
    keys = ("excellent", "good", "fair", "poor", "bad")
    assert body["band"] in {app_i18n.t("pl", f"band.{key}") for key in keys}
    assert body["band_key"] in set(keys)


def test_an_unknown_language_is_rejected(client):
    assert client.get("/api/v1/fsi?lang=fr").status_code == 422


# -------------------------------------------------------------- rate limit


def test_rate_limit_eventually_returns_429(client, monkeypatch):
    monkeypatch.setattr(main.limiter, "limit", 5)
    main.limiter.reset()

    codes = [client.get("/api/v1/scale").status_code for _ in range(7)]
    assert codes[:5] == [200] * 5
    assert codes[-1] == 429

    blocked = client.get("/api/v1/scale")
    assert blocked.headers["retry-after"]
    assert blocked.json()["retry_after_seconds"] >= 1


def test_rate_limit_headers_count_down(client, monkeypatch):
    monkeypatch.setattr(main.limiter, "limit", 10)
    main.limiter.reset()

    first = int(client.get("/api/v1/scale").headers["x-ratelimit-remaining"])
    second = int(client.get("/api/v1/scale").headers["x-ratelimit-remaining"])
    assert second == first - 1


def test_pages_are_not_rate_limited(client, monkeypatch):
    """A board left open must never be locked out of its own HTML."""
    monkeypatch.setattr(main.limiter, "limit", 2)
    main.limiter.reset()

    for _ in range(6):
        assert client.get("/").status_code == 200


# ------------------------------------------------------------------- docs


def test_openapi_documents_the_public_surface(client):
    schema = client.get("/openapi.json").json()
    for path in ENDPOINTS:
        assert path in schema["paths"], f"{path} missing from OpenAPI"
    assert "api/v1" in schema["info"]["description"]


def test_the_schema_publishes_nothing_but_the_contract(client):
    """What is documented is what someone may build on.

    The aggregate the frontend reads is still reachable -- the site needs it --
    but it is nobody's contract, and a path in the schema is an invitation to
    depend on it.
    """
    schema = client.get("/openapi.json").json()
    for path in ("/api/summary", "/api/pollen.png"):
        assert path not in schema["paths"], f"{path} should not be published"
    assert client.get("/api/summary").status_code == 200


def test_wind_can_be_asked_for_in_mph_or_knots(client):
    for unit, speed, gust in (("mph", "wind_speed_mph", "wind_gust_mph"),
                              ("kn", "wind_speed_kn", "wind_gust_kn")):
        body = client.get(f"/api/v1/current?wind_unit={unit}").json()
        assert body["wind_speed_kmh"] is not None, "km/h stays whatever else is asked for"
        assert body[speed] == pytest.approx(body["wind_speed_kmh"] * WIND_FACTORS[unit], abs=0.1)
        assert body[gust] is not None


def test_the_unit_nobody_asked_for_is_absent_not_empty(client):
    """A 120-hour forecast is no place for four empty fields per hour."""
    plain = client.get("/api/v1/forecast?hours=3").json()
    for hour in plain["hours"]:
        assert "wind_speed_mph" not in hour
        assert "wind_speed_kn" not in hour
        assert "wind_speed_kmh" in hour

    mph = client.get("/api/v1/forecast?hours=3&wind_unit=mph").json()
    for hour in mph["hours"]:
        assert "wind_speed_mph" in hour
        assert "wind_speed_kn" not in hour


def test_an_unknown_wind_unit_is_rejected(client):
    assert client.get("/api/v1/current?wind_unit=mps").status_code == 422
