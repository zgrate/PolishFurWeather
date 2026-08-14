"""Current and recent surface observations from IMGW SYNOP.

Unlike DWD's POI report, IMGW's SYNOP endpoint hands back exactly one reading
per station -- there is no ~24h history bundled in. So the "recent" half of
this module is not a parser, it is a small SQLite-backed store: every fetch of
the current reading is also appended to it, and ``fetch_recent`` reads that
store back. History accumulates for as long as the process (and its data
directory) is around, and survives restarts because it is on disk rather than
in memory.

Field mapping, confirmed against a live response from
``https://danepubliczne.imgw.pl/api/data/synop/id/{station}``::

    temperatura           -> temperature (deg C, already)
    wilgotnosc_wzgledna   -> humidity (%, already)
    predkosc_wiatru       -> wind_speed (m/s, already)
    kierunek_wiatru       -> wind_direction (deg, already)
    cisnienie             -> pressure (hPa, already)
    suma_opadu            -> NOT precipitation/precipitation_24h -- see below.

``suma_opadu`` is IMGW's WO6G, a 6-hour accumulated gauge total (confirmed
against IMGW's own archival format documentation), only meaningful right after
a synoptic term. Mapping it to ``precipitation`` (mm in *this* hour) would
misrepresent it, so it is kept out of ``WeatherPoint`` entirely and stored
separately in SQLite as quality-control data -- see
``app/providers/imgw/radar.py`` for where current precipitation intensity
actually comes from (POLRAD SRI).

IMGW does not publish dewpoint, wind gust, cloud cover, visibility or a
significant-weather code in this feed; those fields stay ``None``.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from app.config import settings
from app.meteo import dewpoint as calc_dewpoint
from app.models import WeatherPoint
from app.providers.http import cache, fetch_json

logger = logging.getLogger(__name__)

SYNOP_URL = "https://danepubliczne.imgw.pl/api/data/synop/id/{station}"

#: How long a station's history is kept. The forecast-gap-filling logic in
#: service.py only ever asks for the last ~24h, but a bit of slack survives a
#: cache TTL hiccup or a slow prune.
RETENTION = timedelta(hours=48)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "imgw_observations.sqlite3"


def db_path() -> Path:
    """Where the shared IMGW SQLite store lives -- also used by radar_store.py
    so sampled precipitation and SYNOP readings sit in the same file."""
    return Path(os.environ.get("EFW_IMGW_DB_PATH", DEFAULT_DB_PATH))


_db_lock = threading.Lock()
_connection: Optional[sqlite3.Connection] = None


def _connect() -> sqlite3.Connection:
    global _connection
    with _db_lock:
        if _connection is None:
            path = db_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            _connection = sqlite3.connect(path, check_same_thread=False)
            _connection.execute(
                """
                CREATE TABLE IF NOT EXISTS weather_observations (
                    station_id TEXT NOT NULL,
                    observation_time TEXT NOT NULL,
                    temperature REAL,
                    dewpoint REAL,
                    humidity REAL,
                    wind_speed REAL,
                    wind_direction REAL,
                    pressure REAL,
                    precipitation_6h REAL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (station_id, observation_time)
                )
                """
            )
            _connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_obs_station_time "
                "ON weather_observations (station_id, observation_time)"
            )
            _connection.commit()
        return _connection


def _row_to_point(row: sqlite3.Row) -> WeatherPoint:
    return WeatherPoint(
        time=datetime.fromisoformat(row["observation_time"]),
        temperature=row["temperature"],
        dewpoint=row["dewpoint"],
        humidity=row["humidity"],
        wind_speed=row["wind_speed"],
        wind_direction=row["wind_direction"],
        pressure=row["pressure"],
        source=row["source"],
    )


def _store(point: WeatherPoint, station_id: str, precipitation_6h: Optional[float]) -> None:
    conn = _connect()
    with _db_lock:
        conn.execute(
            """
            INSERT INTO weather_observations
                (station_id, observation_time, temperature, dewpoint, humidity,
                 wind_speed, wind_direction, pressure, precipitation_6h, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(station_id, observation_time) DO UPDATE SET
                temperature=excluded.temperature,
                dewpoint=excluded.dewpoint,
                humidity=excluded.humidity,
                wind_speed=excluded.wind_speed,
                wind_direction=excluded.wind_direction,
                pressure=excluded.pressure,
                precipitation_6h=excluded.precipitation_6h,
                source=excluded.source
            """,
            (
                station_id,
                point.time.astimezone(timezone.utc).isoformat(),
                point.temperature,
                point.dewpoint,
                point.humidity,
                point.wind_speed,
                point.wind_direction,
                point.pressure,
                precipitation_6h,
                point.source,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        cutoff = (datetime.now(timezone.utc) - RETENTION).isoformat()
        conn.execute(
            "DELETE FROM weather_observations WHERE station_id = ? AND observation_time < ?",
            (station_id, cutoff),
        )
        conn.commit()


def _to_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_synop(payload: dict) -> tuple[WeatherPoint, Optional[float]]:
    date_token = payload.get("data_pomiaru")
    hour_token = payload.get("godzina_pomiaru")
    if not date_token or hour_token is None:
        raise ValueError("SYNOP response has no measurement timestamp")

    # SYNOP terms are UTC.
    stamp = datetime.strptime(date_token, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    stamp += timedelta(hours=int(hour_token))

    temperature = _to_float(payload.get("temperatura"))
    humidity = _to_float(payload.get("wilgotnosc_wzgledna"))

    point = WeatherPoint(
        time=stamp,
        temperature=temperature,
        humidity=humidity,
        wind_speed=_to_float(payload.get("predkosc_wiatru")),
        wind_direction=_to_float(payload.get("kierunek_wiatru")),
        pressure=_to_float(payload.get("cisnienie")),
        source="imgw-synop",
    )
    if temperature is not None and humidity is not None:
        point.dewpoint = calc_dewpoint(temperature, humidity)

    return point, _to_float(payload.get("suma_opadu"))


def cache_key(station_id: Optional[str] = None) -> str:
    """The TTLCache key a station's current reading lands under -- for /api/health."""
    return f"imgw:synop:{station_id or settings.imgw.station_id}"


def _fetch_current(station_id: str) -> WeatherPoint:
    def _fetch() -> WeatherPoint:
        payload = fetch_json(SYNOP_URL.format(station=station_id))
        if not isinstance(payload, dict) or "temperatura" not in payload:
            raise ValueError(f"Unexpected SYNOP response for station {station_id}")
        point, precip_6h = _parse_synop(payload)
        _store(point, station_id, precip_6h)
        logger.info(
            "SYNOP %s: %s at %.1f degC, %s%% RH",
            station_id,
            point.time.isoformat(),
            point.temperature if point.temperature is not None else float("nan"),
            point.humidity,
        )
        return point

    return cache.get_or_fetch(f"imgw:synop:{station_id}", settings.cache.observations, _fetch)


def fetch_current(station_id: Optional[str] = None) -> WeatherPoint:
    station_id = station_id or settings.imgw.station_id
    if not station_id:
        raise ValueError("imgw.station_id is not configured")
    return _fetch_current(station_id)


def fetch_recent(station_id: Optional[str] = None) -> List[WeatherPoint]:
    """The last ~24h of observations this process has recorded, oldest first.

    Triggers a current-reading fetch first so the store is never behind the
    cache TTL, then reads back from SQLite -- which is what actually supplies
    the history, since a fresh process has an empty database and only starts
    filling it in from here.
    """
    station_id = station_id or settings.imgw.station_id
    if not station_id:
        raise ValueError("imgw.station_id is not configured")

    try:
        _fetch_current(station_id)
    except Exception as exc:  # noqa: BLE001 - a stale store beats none at all
        logger.warning("Could not refresh SYNOP %s before reading history: %s", station_id, exc)

    conn = _connect()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    conn.row_factory = sqlite3.Row
    with _db_lock:
        rows = conn.execute(
            "SELECT * FROM weather_observations WHERE station_id = ? AND observation_time >= ? "
            "ORDER BY observation_time ASC",
            (station_id, cutoff),
        ).fetchall()
    return [_row_to_point(row) for row in rows]
