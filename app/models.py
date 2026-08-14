"""Shared data structures passed between the weather providers, the FSI engine and the API."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


def _clean(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


@dataclass
class WeatherPoint:
    """One point in time -- either an observation or a forecast step.

    All optional fields are ``None`` when the source does not publish them, so
    the frontend can tell "no data" apart from "zero".
    """

    time: datetime
    temperature: Optional[float] = None  # deg C
    dewpoint: Optional[float] = None  # deg C
    humidity: Optional[float] = None  # %
    wind_speed: Optional[float] = None  # m/s
    wind_gust: Optional[float] = None  # m/s
    wind_direction: Optional[float] = None  # deg
    pressure: Optional[float] = None  # hPa
    cloud_cover: Optional[float] = None  # %
    precipitation: Optional[float] = None  # mm in the hour
    precipitation_prob: Optional[float] = None  # %
    precipitation_24h: Optional[float] = None  # mm
    solar_radiation: Optional[float] = None  # W/m^2
    sunshine_minutes: Optional[float] = None  # min in the hour
    visibility: Optional[float] = None  # m
    weather_code: Optional[int] = None  # WMO ww
    source: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return _clean(asdict(self))


@dataclass
class Warning:
    """An official meteorological warning, normalised to something the frontend can render directly."""

    event: str  # the issuing service's own event name, in its native language
    event_en: str  # English label so international attendees can scan it
    headline: str
    description: str
    instruction: str
    severity: str  # minor | moderate | severe | extreme
    level: int  # the issuing service's own raw severity level/degree
    kind: str  # thunderstorm | wind | rain | snow | ice | heat | ...
    region: str
    #: True for a "Vorabinformation": DWD's own concept of signalling possible
    #: severe weather before it is certain enough to warn on, rendered hatched
    #: rather than solid. IMGW has no equivalent tier, so this is always False
    #: for IMGW-sourced warnings -- not fabricated, just unsupported.
    advance: bool = False
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    color: str = "#facc15"

    def to_dict(self) -> Dict[str, Any]:
        return _clean(asdict(self))


@dataclass
class FSIResult:
    score: float
    label: str
    color: str
    advice: str
    subscores: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: Set on a couple of specific scores; purely cosmetic.
    easter_egg: Optional[str] = None
    wetbulb: Optional[float] = None
    effective_wetbulb: Optional[float] = None
    dewpoint: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return _clean(asdict(self))
