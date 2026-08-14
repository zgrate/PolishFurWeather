"""Open-Meteo hourly forecast -- the fallback/extension beyond COSMO's horizon.

Field mapping, confirmed against a live response from
``https://api.open-meteo.com/v1/forecast`` (``windspeed_unit=ms``)::

    temperature_2m          -> temperature (deg C)
    dew_point_2m             -> dewpoint (deg C)
    relative_humidity_2m     -> humidity (%)
    wind_speed_10m           -> wind_speed (m/s, via windspeed_unit=ms)
    wind_gusts_10m           -> wind_gust (m/s)
    wind_direction_10m       -> wind_direction (deg)
    pressure_msl              -> pressure (hPa)
    cloud_cover               -> cloud_cover (%)
    precipitation             -> precipitation (mm in the hour)
    precipitation_probability -> precipitation_prob (%)
    shortwave_radiation       -> solar_radiation (W/m^2)
    visibility                 -> visibility (m)
    weather_code               -> weather_code (WMO ww -- same table app/weather_codes.py uses)

No dedicated ``precipitation_24h``/sunshine-minutes field is requested: the
forecast series only ever needs the hourly amount, and daily sums are already
computed downstream from the hourly points.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import List, Optional

from app.config import settings
from app.models import WeatherPoint
from app.providers.http import cache
from app.providers.open_meteo.client import FORECAST_URL, get

logger = logging.getLogger(__name__)

HOURLY_PARAMS = ",".join(
    [
        "temperature_2m",
        "dew_point_2m",
        "relative_humidity_2m",
        "wind_speed_10m",
        "wind_gusts_10m",
        "wind_direction_10m",
        "pressure_msl",
        "cloud_cover",
        "precipitation",
        "precipitation_probability",
        "shortwave_radiation",
        "visibility",
        "weather_code",
    ]
)


def _days_for(hours: int) -> int:
    return max(1, min(16, math.ceil(hours / 24.0) + 1))


def cache_key(lat: Optional[float] = None, lon: Optional[float] = None, hours: Optional[int] = None) -> str:
    """The TTLCache key a given forecast request lands under -- for /api/health."""
    lat = lat if lat is not None else settings.location.latitude
    lon = lon if lon is not None else settings.location.longitude
    hours = hours if hours is not None else settings.forecast_hours
    return f"open-meteo:forecast:{lat:.3f}:{lon:.3f}:{_days_for(hours)}"


def _fetch(lat: float, lon: float, hours: int) -> List[WeatherPoint]:
    days = _days_for(hours)

    def _get() -> List[WeatherPoint]:
        data = get(
            FORECAST_URL,
            {
                "latitude": lat,
                "longitude": lon,
                "hourly": HOURLY_PARAMS,
                "windspeed_unit": "ms",
                "timezone": "UTC",
                "forecast_days": days,
            },
        )
        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []

        def col(name: str, index: int) -> Optional[float]:
            values = hourly.get(name)
            if not values or index >= len(values):
                return None
            return values[index]

        points: List[WeatherPoint] = []
        for i, stamp in enumerate(times):
            time = datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)
            code = col("weather_code", i)
            points.append(
                WeatherPoint(
                    time=time,
                    temperature=col("temperature_2m", i),
                    dewpoint=col("dew_point_2m", i),
                    humidity=col("relative_humidity_2m", i),
                    wind_speed=col("wind_speed_10m", i),
                    wind_gust=col("wind_gusts_10m", i),
                    wind_direction=col("wind_direction_10m", i),
                    pressure=col("pressure_msl", i),
                    cloud_cover=col("cloud_cover", i),
                    precipitation=col("precipitation", i),
                    precipitation_prob=col("precipitation_probability", i),
                    solar_radiation=col("shortwave_radiation", i),
                    visibility=col("visibility", i),
                    weather_code=int(code) if code is not None else None,
                    source="open-meteo",
                )
            )
        logger.info("Open-Meteo forecast for %.3f,%.3f: %d hourly points", lat, lon, len(points))
        return points

    return cache.get_or_fetch(
        f"open-meteo:forecast:{lat:.3f}:{lon:.3f}:{days}", settings.cache.forecast, _get
    )


def fetch_forecast(
    lat: Optional[float] = None, lon: Optional[float] = None, hours: Optional[int] = None
) -> List[WeatherPoint]:
    lat = lat if lat is not None else settings.location.latitude
    lon = lon if lon is not None else settings.location.longitude
    hours = hours if hours is not None else settings.forecast_hours
    return _fetch(lat, lon, hours)
