"""Shared bits for the two Open-Meteo endpoints this app calls.

No API key: both ``api.open-meteo.com`` and ``air-quality-api.open-meteo.com``
are free, unauthenticated, non-commercial-use endpoints.
"""

from __future__ import annotations

from app.providers.http import fetch_json

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"


def get(url: str, params: dict) -> dict:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return fetch_json(f"{url}?{query}")
