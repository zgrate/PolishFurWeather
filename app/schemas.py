"""Response models for the public v1 API.

These are a deliberate, stable contract -- not a mirror of the internal
``build_summary`` payload. Field names carry their unit (``temperature_c``,
``wind_speed_kmh``) so a consumer never has to guess, and the shapes stay put
even if the internals move. Anything reshaped here is safe to depend on; the
aggregate ``/api/summary`` is not.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


#: Wind is measured in km/h and ``wind_speed_kmh`` is always present and always
#: km/h -- that is the contract and it does not move. ``?wind_unit=mph`` or
#: ``kn`` adds a second pair beside it, converted. The pair nobody asked for is
#: left out of the response entirely rather than sent as nulls: a forecast is a
#: hundred hours long, and empty keys on every one of them are pure freight.
MPH = "Only present with ?wind_unit=mph"
KNOTS = "Only present with ?wind_unit=kn"


class Meta(BaseModel):
    """Who and where the numbers describe, plus provenance."""

    event: str = Field(examples=["EF30"])
    location: str = Field(examples=["Hamburg"])
    latitude: float
    longitude: float
    timezone: str = Field(examples=["Europe/Berlin"])
    station_id: str = Field(description="IMGW SYNOP station used for observations")
    generated_at: str = Field(description="ISO 8601, UTC")
    language: str = Field(examples=["en", "de"])
    attribution: str
    degraded: List[str] = Field(
        default_factory=list,
        description="Upstream sources that were unreachable for this response",
    )


class Band(BaseModel):
    """One step of the index scale."""

    min: float = Field(description="Lowest score in this band")
    key: str = Field(examples=["excellent", "good", "fair", "poor", "bad"])
    label: str = Field(description="Translated name")
    color: str = Field(examples=["#40ad3e"])


class SubScore(BaseModel):
    key: str = Field(examples=["thermal_humidity"])
    label: str
    score: float = Field(ge=0, le=10)
    weight: float = Field(ge=0, le=1)
    reason: str


class Weather(BaseModel):
    code: Optional[int] = Field(default=None, description="WMO ww significant weather code")
    text: Optional[str] = None
    icon: Optional[str] = None


class FsiNow(BaseModel):
    """The headline number. Small on purpose -- ideal for bots and widgets."""

    score: float = Field(ge=0, le=10, examples=[6.9])
    band: str = Field(description="Translated band name", examples=["Fair"])
    band_key: str = Field(examples=["fair"])
    color: str
    advice: str
    caps_applied: List[str] = Field(
        default_factory=list,
        description=(
            "Heat ceilings that overrode the weighted score. Official warnings do "
            "not affect the score -- see /api/v1/warnings for those."
        ),
    )
    easter_egg: Optional[str] = Field(default=None, description="Cosmetic only")
    wetbulb_c: Optional[float] = None
    effective_wetbulb_c: Optional[float] = Field(
        default=None, description="Wet-bulb plus the solar load a suit absorbs"
    )
    dewpoint_c: Optional[float] = None
    observed_at: Optional[str] = None
    subscores: List[SubScore] = Field(default_factory=list)
    meta: Meta


class CurrentConditions(BaseModel):
    observed_at: str = Field(description="ISO 8601, station local time")
    temperature_c: Optional[float] = None
    dewpoint_c: Optional[float] = None
    humidity_percent: Optional[float] = None
    wind_speed_kmh: Optional[float] = None
    wind_gust_kmh: Optional[float] = None
    wind_speed_mph: Optional[float] = Field(default=None, description=MPH)
    wind_gust_mph: Optional[float] = Field(default=None, description=MPH)
    wind_speed_kn: Optional[float] = Field(default=None, description=KNOTS)
    wind_gust_kn: Optional[float] = Field(default=None, description=KNOTS)
    wind_direction_deg: Optional[float] = None
    wind_direction: Optional[str] = Field(default=None, examples=["NW"])
    beaufort: Optional[int] = None
    pressure_hpa: Optional[float] = None
    cloud_cover_percent: Optional[float] = None
    precipitation_mm: Optional[float] = Field(default=None, description="Last hour")
    visibility_m: Optional[float] = None
    weather: Weather
    meta: Meta


class ForecastHour(BaseModel):
    time: str = Field(description="ISO 8601 with offset, local time")
    score: float = Field(ge=0, le=10)
    band: str
    color: str
    temperature_c: Optional[float] = None
    humidity_percent: Optional[float] = None
    wind_speed_kmh: Optional[float] = None
    wind_gust_kmh: Optional[float] = None
    wind_speed_mph: Optional[float] = Field(default=None, description=MPH)
    wind_gust_mph: Optional[float] = Field(default=None, description=MPH)
    wind_speed_kn: Optional[float] = Field(default=None, description=KNOTS)
    wind_gust_kn: Optional[float] = Field(default=None, description=KNOTS)
    precipitation_mm: Optional[float] = None
    precipitation_probability: Optional[float] = None
    weather: Weather


class Forecast(BaseModel):
    hours: List[ForecastHour]
    issued_at: Optional[str] = Field(default=None, description="Forecast fetch time")
    meta: Meta


class Window(BaseModel):
    """A run of consecutive hours worth planning around, or avoiding."""

    start: str
    end: str
    hours: int
    score: float = Field(description="Best score in a good window, worst in a bad one")


class DailyEntry(BaseModel):
    date: str = Field(examples=["2026-09-04"])
    weekday: str
    partial: bool = Field(description="True when the horizon only covers part of the day")
    hour_count: int
    temperature_max_c: Optional[float] = None
    temperature_min_c: Optional[float] = None
    precipitation_mm: Optional[float] = None
    precipitation_probability: Optional[float] = None
    wind_speed_kmh: Optional[float] = None
    wind_gust_kmh: Optional[float] = None
    wind_speed_mph: Optional[float] = Field(default=None, description=MPH)
    wind_gust_mph: Optional[float] = Field(default=None, description=MPH)
    wind_speed_kn: Optional[float] = Field(default=None, description=KNOTS)
    wind_gust_kn: Optional[float] = Field(default=None, description=KNOTS)
    wind_direction_deg: Optional[float] = Field(
        default=None, description="Speed-weighted vector mean over the day"
    )
    wind_direction: Optional[str] = Field(default=None, examples=["NW"])
    humidity_percent: Optional[float] = None
    sunshine_hours: Optional[float] = None
    weather: Weather
    fsi_max: Optional[float] = None
    fsi_min: Optional[float] = None
    fsi_avg: Optional[float] = None
    best_hour: Optional[str] = Field(default=None, examples=["07:00"])
    worst_hour: Optional[str] = Field(default=None, examples=["14:00"])
    best_window: Optional[Window] = Field(
        default=None, description="The stretch of hours the day's best hour sits in"
    )
    worst_window: Optional[Window] = Field(
        default=None, description="The stretch of hours the day's worst hour sits in"
    )


class Daily(BaseModel):
    days: List[DailyEntry]
    meta: Meta


class WarningItem(BaseModel):
    event: str = Field(description="The issuing service's own event name, in its native language")
    label: str = Field(description="Translated heading, e.g. 'Heat warning (moderate)'")
    kind: str = Field(examples=["heat", "thunderstorm", "wind", "rain"])
    severity: str = Field(examples=["minor", "moderate", "severe", "extreme"])
    advance: bool = Field(
        description="True for a Vorabinformation -- possible severe weather, not yet certain"
    )
    level: int = Field(description="The issuing service's own raw severity level/degree")
    color: str
    region: str
    start: Optional[str] = None
    end: Optional[str] = None
    headline: str = Field(description="Official wording, Polish")
    description: str = Field(description="Official wording, Polish")
    instruction: str = Field(description="Official wording, Polish")


class Warnings(BaseModel):
    warnings: List[WarningItem]
    count: int
    meta: Meta


class Scale(BaseModel):
    """The index scale, so a client can colour its own charts consistently."""

    bands: List[Band]
    suitable_from: float = Field(description="At or above this an hour counts as suitable")
    meta: Meta


class Overview(BaseModel):
    """Everything at once, in the v1 shapes -- one call for a full dashboard."""

    fsi: Optional[FsiNow] = None
    current: Optional[CurrentConditions] = None
    forecast: Forecast
    daily: Daily
    warnings: Warnings
    best_window: Optional[Window] = None
    worst_window: Optional[Window] = None
    scale: Scale
    meta: Meta


class Problem(BaseModel):
    detail: str


class RateLimited(BaseModel):
    detail: str
    retry_after_seconds: int


class SiteLoad(BaseModel):
    """How busy the machine serving this site is."""

    enabled: bool = Field(description="False means nothing is counted and the numbers are meaningless")
    visitors: int = Field(description="Distinct client addresses seen inside the window")
    level: str = Field(
        description="normal, busy or crowded -- what the site shows a notice for",
        examples=["normal"],
    )
    in_flight_requests: int = Field(description="Requests being served at this instant")
    window_seconds: int = Field(description="How long since their last request someone still counts")
    busy_at: int
    crowded_at: int
    peak_visitors: int = Field(description="Highest count since the process started")


class Health(BaseModel):
    status: str
    event: str
    location: str
    station: str
    cache_age_seconds: Dict[str, Optional[int]]
