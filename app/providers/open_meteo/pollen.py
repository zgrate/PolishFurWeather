"""Pollen at the venue, from Open-Meteo's CAMS-backed air-quality endpoint.

Species mapping, confirmed against a live response from
``https://air-quality-api.open-meteo.com/v1/air-quality``::

    alder_pollen    -> alder
    birch_pollen    -> birch
    grass_pollen    -> grasses
    ragweed_pollen  -> ragweed

CAMS (the model behind this endpoint) does not publish a hazel species at
all -- there is no ``hazel_pollen`` field, not a zero-valued one. Per the
project's own rule (never report an unavailable species as zero), hazel is
simply left out of the reading list rather than mapped to anything.
``mugwort_pollen``/``olive_pollen`` exist in the source but are not in this
app's species set and are ignored, same as before.

The severity bands (LEVELS/thresholds) are the same shape the DWD ICON-ART
pollen module used and read from the same ``pollen.thresholds`` config, so a
site that retuned them for the DWD source keeps that tuning here.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

from app.config import settings
from app.providers.http import cache
from app.providers.open_meteo.client import AIR_QUALITY_URL, get

logger = logging.getLogger(__name__)

#: Species this app tracks -> the CAMS variable name. Deliberately excludes
#: hazel: CAMS has no hazel product, see module docstring.
SPECIES = {
    "alder": "alder_pollen",
    "birch": "birch_pollen",
    "grasses": "grass_pollen",
    "ragweed": "ragweed_pollen",
}

#: Same four bands and colours the DWD-sourced pollen module used, so the
#: frontend needs no changes to render this source's readings.
LEVELS: Tuple[Tuple[str, str], ...] = (
    ("low", "#7cc243"),
    ("moderate", "#ffd633"),
    ("high", "#ff8a3d"),
    ("very_high", "#f13ca3"),
)

DEFAULT_THRESHOLDS: Dict[str, Tuple[float, float, float]] = {
    "alder": (10.0, 100.0, 250.0),
    "birch": (10.0, 50.0, 200.0),
    "grasses": (10.0, 30.0, 80.0),
    "ragweed": (3.0, 10.0, 25.0),
}

WARN_FROM_LEVEL = 2

FETCH_TTL = 6 * 3600


def _thresholds(key: str) -> Tuple[float, float, float]:
    configured = settings.pollen.thresholds.get(key)
    if configured and len(configured) == 3:
        return tuple(sorted(float(v) for v in configured))  # type: ignore[return-value]
    return DEFAULT_THRESHOLDS[key]


def _level_index(key: str, value: float) -> int:
    edges = _thresholds(key)
    index = 0
    for edge in edges:
        if value >= edge:
            index += 1
    return min(index, len(LEVELS) - 1)


def _fetch_hourly(lat: float, lon: float) -> dict:
    def _get() -> dict:
        return get(
            AIR_QUALITY_URL,
            {
                "latitude": lat,
                "longitude": lon,
                "hourly": ",".join(SPECIES.values()),
                "timezone": "UTC",
                "forecast_days": 1,
            },
        )

    return cache.get_or_fetch(f"open-meteo:pollen:{lat:.3f}:{lon:.3f}", FETCH_TTL, _get)


def at_point(lat: Optional[float] = None, lon: Optional[float] = None) -> List[Dict]:
    """What is in the air over the venue right now, heaviest species first."""
    lat = lat if lat is not None else settings.location.latitude
    lon = lon if lon is not None else settings.location.longitude

    try:
        data = _fetch_hourly(lat, lon)
    except Exception as exc:  # noqa: BLE001 - pollen down must not break /api/summary
        logger.warning("Open-Meteo pollen unavailable: %s", exc)
        return []

    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    now = datetime.now(timezone.utc)
    index = 0
    for i, stamp in enumerate(times):
        if datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc) <= now:
            index = i

    readings: List[Dict] = []
    for key, variable in SPECIES.items():
        values = hourly.get(variable)
        if not values or index >= len(values):
            continue
        value = values[index]
        if value is None:
            continue  # out of season / not modelled right now -- not zero
        value = float(value)
        level_index = _level_index(key, value)
        readings.append(
            {
                "key": key,
                "value": round(value, 1),
                "level": LEVELS[level_index][0],
                "level_index": level_index,
                "color": LEVELS[level_index][1],
                "warn": level_index >= WARN_FROM_LEVEL,
                "valid": date.today().isoformat() if not times else times[index][:10],
            }
        )

    readings.sort(key=lambda r: (-r["level_index"], -r["value"]))
    return readings
