"""Single facade over IMGW + Open-Meteo -- what app/service.py used to get
from importing ``app.dwd.*`` module-by-module.

Shaped to match what those DWD modules returned (``fetch_current`` ->
``WeatherPoint``, ``fetch_forecast`` -> ``{"points","issued","extremes",
"station_name"}``, etc.) so service.py's own logic barely has to change --
only its imports.

Current conditions combine three sources per
``POLRAD_SRI_Implementation_Summary.md``: SYNOP for temperature/humidity/wind/
pressure, POLRAD SRI radar for precipitation *intensity*, and Open-Meteo's
nearest hourly forecast point for wind gust and weather code -- SYNOP reports
neither at all. SYNOP's own ``suma_opadu`` (WO6G, a 6h gauge total) never
reaches ``WeatherPoint`` -- see ``app/providers/imgw/observations.py``.

Forecast is Open-Meteo only for now: IMGW's own 60h COSMO 2.8km model
(app/providers/imgw/cosmo.py) is not wired into it, because each of its
forecast hours is a ~150 MB single-file fetch (see that module's docstring) --
practical for the model-map card refreshed in the background, not for a
120-hour point series inside one API response. Every forecast hour therefore
comes from Open-Meteo rather than a mix of the two.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.config import settings
from app.models import WeatherPoint, Warning
from app.providers.imgw import observations as synop
from app.providers.imgw import radar, radar_store
from app.providers.imgw import warnings as imgw_warnings
from app.providers.open_meteo import forecast as open_meteo_forecast
from app.providers.open_meteo import pollen as open_meteo_pollen

logger = logging.getLogger(__name__)


def day_floor(moment: datetime) -> datetime:
    """Local midnight of the day ``moment`` falls in.

    Same rule ``app.dwd.mosmix.day_floor`` used -- ``app.service`` repairs the
    day window against this boundary and the two have to agree where it starts.
    """
    return moment.astimezone(ZoneInfo(settings.location.timezone)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _nearest_point(points: List[WeatherPoint], moment: datetime) -> Optional[WeatherPoint]:
    if not points:
        return None
    return min(points, key=lambda p: abs((p.time - moment).total_seconds()))


def _with_open_meteo_extras(
    point: WeatherPoint,
    lat: float,
    lon: float,
    forecast_points: Optional[List[WeatherPoint]] = None,
) -> WeatherPoint:
    """Fills ``wind_gust``/``weather_code``/rain chance from Open-Meteo's nearest hour.

    SYNOP never reports wind gust, weather code, or a chance of rain at all
    (see ``app/providers/imgw/observations.py``), and POLRAD only ever answers
    "how hard is it raining this instant" -- never "how likely". Left alone,
    a radar miss (no fresh frame, point outside coverage) reads as a
    confident 0 mm/h and 0% chance, which lets ``_precipitation_score`` score
    a thunderstorm hour "excellent" purely because the live radar frame
    happened to be dry or missing. Borrowing the forecast's own rate and
    probability for a miss keeps the headline card's rain read consistent
    with the hourly chart instead of quietly defaulting to "no rain".

    ``forecast_points``, when given, is that already-fetched series -- the exact
    one the hourly chart is drawn from. Without it this used to run its own
    second fetch, which can land on a different cached Open-Meteo payload than
    the chart's and hand the headline card a different weather_code (thunder
    vs. not) for what is supposed to be the same hour.
    """
    need_extras = point.wind_gust is None or point.weather_code is None
    need_rain = point.precipitation is None or point.precipitation_prob is None
    if not (need_extras or need_rain):
        return point

    if forecast_points is None:
        forecast_points = open_meteo_forecast.fetch_forecast(lat, lon)
    nearest = _nearest_point(forecast_points, point.time)
    if nearest is None:
        return point

    filled = False
    if point.wind_gust is None and nearest.wind_gust is not None:
        point.wind_gust = nearest.wind_gust
        filled = True
    if point.weather_code is None and nearest.weather_code is not None:
        point.weather_code = nearest.weather_code
        filled = True
    if point.precipitation is None and nearest.precipitation is not None:
        point.precipitation = nearest.precipitation
        filled = True
    if point.precipitation_prob is None and nearest.precipitation_prob is not None:
        point.precipitation_prob = nearest.precipitation_prob
        filled = True
    if filled:
        point.source = f"{point.source}+open-meteo"
    return point


def _with_radar_precipitation(point: WeatherPoint, lat: float, lon: float) -> WeatherPoint:
    """Overrides ``precipitation`` with the POLRAD SRI rate at the venue.

    A miss -- no fresh frame, or the point falls outside radar coverage --
    leaves ``precipitation`` as ``None``. Never falls back to SYNOP's WO6G as
    an hourly figure: that field measures something else entirely (a 6h gauge
    total), and reporting it as "this hour's rain" would misrepresent it.
    """
    intensity, product_time = radar.precipitation_intensity(lat, lon)
    if intensity is None or product_time is None:
        point.precipitation = None
        return point

    radar_store.record(lat, lon, intensity, product_time)
    point.precipitation = intensity
    point.source = f"{point.source}+polrad-sri"
    return point


def fetch_current(
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    forecast_points: Optional[List[WeatherPoint]] = None,
) -> WeatherPoint:
    lat = lat if lat is not None else settings.location.latitude
    lon = lon if lon is not None else settings.location.longitude
    point = synop.fetch_current()
    point = _with_radar_precipitation(point, lat, lon)
    return _with_open_meteo_extras(point, lat, lon, forecast_points)


def fetch_recent() -> List[WeatherPoint]:
    """Last ~24h of SYNOP observations, oldest first.

    Radar precipitation is only combined onto the *current* reading -- history
    here is what the elapsed-hours gap filler in service.py reads, and it
    predates radar-derived intensity being sampled at all for most of it.
    """
    return synop.fetch_recent()


def fetch_forecast(
    lat: Optional[float] = None, lon: Optional[float] = None, hours: Optional[int] = None
) -> Dict[str, Any]:
    """Hourly forecast, shaped like ``app.dwd.mosmix.fetch_forecast``'s return.

    ``extremes`` stays empty: Open-Meteo publishes no separate daily min/max
    series the way DWD's MOSMIX KML does, and service.py already computes a
    day's high/low from the hourly points when ``extremes`` has nothing for
    that day. ``issued`` is the time of this fetch, since Open-Meteo's
    response carries no model-run timestamp to report instead.
    """
    points = open_meteo_forecast.fetch_forecast(lat, lon, hours)
    return {
        "points": points,
        "issued": datetime.now(timezone.utc) if points else None,
        "extremes": {},
        "station_name": None,
    }


def fetch_warnings(lang: str = "en") -> List[Warning]:
    return imgw_warnings.fetch_warnings(lang=lang)


def pollen_at_point(lat: Optional[float] = None, lon: Optional[float] = None) -> List[Dict[str, Any]]:
    return open_meteo_pollen.at_point(lat, lon)
