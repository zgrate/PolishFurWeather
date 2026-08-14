"""Public, versioned API.

Author: laffiesphere. Human-readable notes for consumers live at ``/api-docs``;
this module is the contract those notes describe.

Third parties (the EF app, bots, info screens) should build against ``/api/v1``
and nothing else. It is the whole published surface: the aggregate the frontend
reads and the map images it draws are deliberately absent from the schema,
because their shape and their cost are the site's own business. Everything here
is reshaped into the models in ``schemas.py`` and is safe to depend on.

Endpoints are deliberately narrow: ``/api/v1/fsi`` is a few hundred bytes,
where the internal aggregate is ~60 kB.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Response

from app import fsi as fsi_engine
from app import schemas, service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Public API v1"])

#: Browser and CDN caching. IMGW SYNOP observations are hourly and the
#: forecast changes on a similar cadence, so a couple of minutes costs a
#: consumer nothing.
CACHE_SECONDS = 120

LanguageQuery = Query("en", pattern="^(en|de)$", description="Language for generated text")

#: From the km/h the payload speaks in. The frontend keeps its own copy of these
#: two numbers in i18n.js: it already holds every hour of the forecast, so making
#: it ask the server again each time a reader taps "mph" would be a round trip to
#: multiply a number it was already looking at.
WIND_FACTORS = {"mph": 0.621371, "kn": 0.539957}

WindUnitQuery = Query(
    "kmh",
    pattern="^(kmh|mph|kn)$",
    description=(
        "Adds a converted pair of wind fields beside the km/h ones: mph gives "
        "wind_speed_mph/wind_gust_mph, kn gives wind_speed_kn/wind_gust_kn. "
        "The km/h fields are always there whatever you ask for."
    ),
)


def _wind(raw: Dict[str, Any], unit: str) -> Dict[str, float]:
    """The converted wind fields for `unit`, or nothing at all for plain km/h.

    Nothing at all is the point: the fields are left *unset* rather than set to
    None, and the routes below serialise with ``exclude_unset``, so a caller who
    never asked for knots never carries an empty knots field. Every other field
    on these models is passed explicitly, so nothing else is affected.
    """
    factor = WIND_FACTORS.get(unit)
    if factor is None:
        return {}
    return {
        f"wind_{kind}_{unit}": round(value * factor, 1)
        for kind, value in (
            ("speed", raw.get("wind_speed_kmh")),
            ("gust", raw.get("wind_gust_kmh")),
        )
        if value is not None
    }


def _summary(lang: str) -> Dict[str, Any]:
    """Fetch the internal payload, or fail loudly if every source is down."""
    payload = service.build_summary(lang)
    if payload["current"] is None and not payload["daily"]:
        raise HTTPException(status_code=503, detail="No weather data available right now")
    return payload


def _meta(payload: Dict[str, Any]) -> schemas.Meta:
    location = payload["location"]
    return schemas.Meta(
        event=payload["event"]["name"],
        location=location["name"],
        latitude=location["latitude"],
        longitude=location["longitude"],
        timezone=location["timezone"],
        station_id=location["station_id"],
        generated_at=payload["generated_at"],
        language=payload["language"],
        attribution=payload["attribution"],
        degraded=payload["degraded"],
    )


def _band_key(score: float) -> str:
    for minimum, key, _color in fsi_engine.BANDS:
        if score >= minimum:
            return key
    return fsi_engine.BANDS[-1][1]


def _weather(raw: Optional[Dict[str, Any]]) -> schemas.Weather:
    raw = raw or {}
    return schemas.Weather(code=raw.get("code"), text=raw.get("text"), icon=raw.get("icon"))


def _fsi(payload: Dict[str, Any]) -> Optional[schemas.FsiNow]:
    raw = payload.get("fsi")
    if not raw:
        return None

    return schemas.FsiNow(
        score=raw["score"],
        band=raw["label"],
        band_key=_band_key(raw["score"]),
        color=raw["color"],
        advice=raw["advice"],
        caps_applied=raw["caps_applied"],
        easter_egg=raw.get("easter_egg"),
        wetbulb_c=raw.get("wetbulb"),
        effective_wetbulb_c=raw.get("effective_wetbulb"),
        dewpoint_c=raw.get("dewpoint"),
        observed_at=(payload.get("current") or {}).get("time_local"),
        subscores=[
            schemas.SubScore(
                key=key,
                label=value["label"],
                score=value["score"],
                weight=value["weight"],
                reason=value["reason"],
            )
            for key, value in raw["subscores"].items()
        ],
        meta=_meta(payload),
    )


def _current(payload: Dict[str, Any], wind_unit: str = "kmh") -> Optional[schemas.CurrentConditions]:
    raw = payload.get("current")
    if not raw:
        return None

    return schemas.CurrentConditions(
        observed_at=raw["time_local"],
        temperature_c=raw.get("temperature"),
        dewpoint_c=raw.get("dewpoint"),
        humidity_percent=raw.get("humidity"),
        wind_speed_kmh=raw.get("wind_speed_kmh"),
        wind_gust_kmh=raw.get("wind_gust_kmh"),
        **_wind(raw, wind_unit),
        wind_direction_deg=raw.get("wind_direction"),
        wind_direction=raw.get("wind_direction_name"),
        beaufort=raw.get("beaufort"),
        pressure_hpa=raw.get("pressure"),
        cloud_cover_percent=raw.get("cloud_cover"),
        precipitation_mm=raw.get("precipitation"),
        visibility_m=raw.get("visibility"),
        weather=_weather(raw.get("weather")),
        meta=_meta(payload),
    )


def _forecast(payload: Dict[str, Any], hours: int, wind_unit: str = "kmh") -> schemas.Forecast:
    # The scored series carries the hours of today that have already gone by so
    # the site can grey them out behind a "now" marker. ``hours`` means hours
    # from now, so those are skipped rather than spent against the horizon.
    now = service.hour_now()
    upcoming = [
        entry for entry in payload["fsi_series"] if datetime.fromisoformat(entry["time"]) >= now
    ]

    entries: List[schemas.ForecastHour] = [
        schemas.ForecastHour(
            time=entry.get("time_local") or entry["time"],
            score=entry["score"],
            band=entry["label"],
            color=entry["color"],
            temperature_c=entry.get("temperature"),
            humidity_percent=entry.get("humidity"),
            wind_speed_kmh=entry.get("wind_speed_kmh"),
            wind_gust_kmh=entry.get("wind_gust_kmh"),
            **_wind(entry, wind_unit),
            precipitation_mm=entry.get("precipitation"),
            precipitation_probability=entry.get("precipitation_prob"),
            weather=_weather(entry.get("weather")),
        )
        for entry in upcoming[:hours]
    ]

    return schemas.Forecast(
        hours=entries, issued_at=payload.get("forecast_issued"), meta=_meta(payload)
    )


def _daily(payload: Dict[str, Any], wind_unit: str = "kmh") -> schemas.Daily:
    days = [
        schemas.DailyEntry(
            date=day["date"],
            weekday=day["weekday"],
            partial=day["partial"],
            hour_count=day["hour_count"],
            temperature_max_c=day.get("temp_max"),
            temperature_min_c=day.get("temp_min"),
            precipitation_mm=day.get("precipitation"),
            precipitation_probability=day.get("precipitation_prob"),
            wind_speed_kmh=day.get("wind_speed_kmh"),
            wind_gust_kmh=day.get("wind_gust_kmh"),
            **_wind(day, wind_unit),
            wind_direction_deg=day.get("wind_direction"),
            wind_direction=day.get("wind_direction_name"),
            humidity_percent=day.get("humidity"),
            sunshine_hours=day.get("sunshine_hours"),
            weather=_weather(day.get("weather")),
            fsi_max=day.get("fsi_max"),
            fsi_min=day.get("fsi_min"),
            fsi_avg=day.get("fsi_avg"),
            best_hour=day.get("fsi_best_hour"),
            worst_hour=day.get("fsi_worst_hour"),
            best_window=_window(day.get("fsi_best_window")),
            worst_window=_window(day.get("fsi_worst_window")),
        )
        for day in payload["daily"]
    ]
    return schemas.Daily(days=days, meta=_meta(payload))


def _warnings(payload: Dict[str, Any]) -> schemas.Warnings:
    items = [
        schemas.WarningItem(
            event=warning["event"],
            label=warning["event_en"],
            kind=warning["kind"],
            severity=warning["severity"],
            advance=warning.get("advance", False),
            level=warning["level"],
            color=warning["color"],
            region=warning["region"],
            start=warning.get("start"),
            end=warning.get("end"),
            headline=warning["headline"],
            description=warning["description"],
            instruction=warning["instruction"],
        )
        for warning in payload["warnings"]
    ]
    return schemas.Warnings(warnings=items, count=len(items), meta=_meta(payload))


def _scale(payload: Dict[str, Any]) -> schemas.Scale:
    return schemas.Scale(
        bands=[schemas.Band(**band) for band in payload["bands"]],
        suitable_from=service.SUITABLE,
        meta=_meta(payload),
    )


def _window(raw: Optional[Dict[str, Any]]) -> Optional[schemas.Window]:
    if not raw:
        return None
    return schemas.Window(
        start=raw["start"], end=raw["end"], hours=raw["hours"], score=raw["peak_score"]
    )


def _cache(response: Response) -> None:
    response.headers["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"


RESPONSES = {503: {"model": schemas.Problem}, 429: {"model": schemas.RateLimited}}

#: Every field on every model below is passed explicitly by the builders above,
#: so "unset" means exactly one thing: a converted wind field for a unit this
#: caller did not ask for. Dropping those is why a plain request is byte for
#: byte what it always was. Anything added to a model later must be passed the
#: same way, or it will quietly stop appearing.
LEAN = {"response_model_exclude_unset": True}


@router.get(
    "/fsi",
    response_model=schemas.FsiNow,
    responses=RESPONSES,
    summary="Current Fursuiting Index",
    description="The headline score with its band, advice and sub-scores. "
    "A few hundred bytes -- use this for bots, widgets and status pages.",
)
def get_fsi(response: Response, lang: str = LanguageQuery) -> schemas.FsiNow:
    payload = _summary(lang)
    result = _fsi(payload)
    if result is None:
        raise HTTPException(status_code=503, detail="No index available right now")
    _cache(response)
    return result


@router.get(
    "/current",
    response_model=schemas.CurrentConditions,
    responses=RESPONSES,
    summary="Current observed conditions",
    description="The latest surface observation from the venue's IMGW SYNOP station, "
    "with precipitation intensity from IMGW's POLRAD SRI radar composite.",
    **LEAN,
)
def get_current(
    response: Response,
    lang: str = LanguageQuery,
    wind_unit: str = WindUnitQuery,
) -> schemas.CurrentConditions:
    payload = _summary(lang)
    result = _current(payload, wind_unit)
    if result is None:
        raise HTTPException(status_code=503, detail="No observation available right now")
    _cache(response)
    return result


@router.get(
    "/forecast",
    response_model=schemas.Forecast,
    responses=RESPONSES,
    summary="Hourly forecast with the index per hour",
    **LEAN,
)
def get_forecast(
    response: Response,
    hours: int = Query(24, ge=1, le=120, description="How many hours from now"),
    lang: str = LanguageQuery,
    wind_unit: str = WindUnitQuery,
) -> schemas.Forecast:
    payload = _summary(lang)
    _cache(response)
    return _forecast(payload, hours, wind_unit)


@router.get(
    "/daily",
    response_model=schemas.Daily,
    responses=RESPONSES,
    summary="Per-day summary with the best and worst hour",
    **LEAN,
)
def get_daily(
    response: Response,
    lang: str = LanguageQuery,
    wind_unit: str = WindUnitQuery,
) -> schemas.Daily:
    payload = _summary(lang)
    _cache(response)
    return _daily(payload, wind_unit)


@router.get(
    "/warnings",
    response_model=schemas.Warnings,
    responses=RESPONSES,
    summary="Active official IMGW warnings",
    description="Warning wording is published by IMGW in Polish only and is passed "
    "through verbatim; only the heading is translated. Warnings are reported "
    "alongside the index and do not alter its score -- combine them yourself if "
    "your use case needs that.",
)
def get_warnings(response: Response, lang: str = LanguageQuery) -> schemas.Warnings:
    payload = _summary(lang)
    _cache(response)
    return _warnings(payload)


@router.get(
    "/scale",
    response_model=schemas.Scale,
    responses=RESPONSES,
    summary="The index scale and its colours",
    description="Colour your own charts to match. Defined once server-side, so "
    "clients never hold a copy that drifts.",
)
def get_scale(response: Response, lang: str = LanguageQuery) -> schemas.Scale:
    payload = _summary(lang)
    _cache(response)
    return _scale(payload)


@router.get(
    "/overview",
    response_model=schemas.Overview,
    responses=RESPONSES,
    summary="Everything at once, in the v1 shapes",
    description="One call for a full dashboard. Larger than the focused endpoints "
    "-- prefer those if you only need one part.",
    **LEAN,
)
def get_overview(
    response: Response,
    hours: int = Query(24, ge=1, le=120),
    lang: str = LanguageQuery,
    wind_unit: str = WindUnitQuery,
) -> schemas.Overview:
    payload = _summary(lang)
    _cache(response)
    return schemas.Overview(
        fsi=_fsi(payload),
        current=_current(payload, wind_unit),
        forecast=_forecast(payload, hours, wind_unit),
        daily=_daily(payload, wind_unit),
        warnings=_warnings(payload),
        best_window=_window(payload.get("best_window")),
        worst_window=_window(payload.get("worst_window")),
        scale=_scale(payload),
        meta=_meta(payload),
    )
