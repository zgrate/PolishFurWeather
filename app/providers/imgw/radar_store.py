"""Local persistence of sampled POLRAD SRI values, for later rainfall accumulation.

Per ``POLRAD_SRI_Implementation_Summary.md``: POLRAD SRI gives a precipitation
*rate* (mm/h) at one instant, not an accumulated amount. To answer
"how much rain fell in the last hour" the app has to hold on to the samples
itself and integrate them -- there is no IMGW endpoint that hands that back
directly the way SYNOP's WO6G does for its own 6h gauge total.

Every sample recorded here is a radar-estimated rate, not a gauge reading, so
anything derived from ``accumulated()`` must be labelled as an estimate, never
presented as the same kind of figure as WO6G.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.providers.imgw.observations import db_path

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_connection: Optional[sqlite3.Connection] = None

#: A day's worth of accumulation windows plus slack for a slow prune.
RETENTION = timedelta(hours=30)

#: Product refresh is ~5 minutes; a gap bigger than this means the radar was
#: down, not that the rain rate held steady across the hole.
MAX_INTEGRATION_GAP = timedelta(minutes=30)


def _connect() -> sqlite3.Connection:
    global _connection
    with _lock:
        if _connection is None:
            path = db_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            _connection = sqlite3.connect(path, check_same_thread=False)
            _connection.execute(
                """
                CREATE TABLE IF NOT EXISTS radar_precipitation (
                    timestamp TEXT NOT NULL,
                    product_timestamp TEXT NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    intensity_mm_h REAL NOT NULL,
                    source TEXT NOT NULL,
                    PRIMARY KEY (latitude, longitude, product_timestamp)
                )
                """
            )
            _connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_radar_precip_time "
                "ON radar_precipitation (latitude, longitude, product_timestamp)"
            )
            _connection.commit()
        return _connection


def record(lat: float, lon: float, intensity_mm_h: float, product_time: datetime) -> None:
    """Log one venue sample so a later accumulation query can integrate it."""
    conn = _connect()
    lat, lon = round(lat, 4), round(lon, 4)
    with _lock:
        conn.execute(
            """
            INSERT INTO radar_precipitation
                (timestamp, product_timestamp, latitude, longitude, intensity_mm_h, source)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(latitude, longitude, product_timestamp) DO UPDATE SET
                intensity_mm_h = excluded.intensity_mm_h,
                timestamp = excluded.timestamp
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                product_time.astimezone(timezone.utc).isoformat(),
                lat,
                lon,
                intensity_mm_h,
                "polrad-sri",
            ),
        )
        cutoff = (datetime.now(timezone.utc) - RETENTION).isoformat()
        conn.execute(
            "DELETE FROM radar_precipitation WHERE latitude = ? AND longitude = ? "
            "AND product_timestamp < ?",
            (lat, lon, cutoff),
        )
        conn.commit()


def accumulated(lat: float, lon: float, hours: float) -> Optional[float]:
    """Radar-estimated rainfall (mm) over the last ``hours`` at a point.

    Each stored sample is held constant across the gap to the next one (the
    product's own ~5 minute refresh interval), which is how ODIM/OPERA radar
    QPE accumulation is normally done from instantaneous rate composites. A
    gap bigger than ``MAX_INTEGRATION_GAP`` is treated as "radar was down" and
    excluded rather than assumed to have rained at the last known rate.

    Returns None -- not 0 -- when there are no samples in the window at all,
    since that means "not observed yet", not "no rain".
    """
    conn = _connect()
    lat, lon = round(lat, 4), round(lon, 4)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn.row_factory = sqlite3.Row
    with _lock:
        rows = conn.execute(
            "SELECT product_timestamp, intensity_mm_h FROM radar_precipitation "
            "WHERE latitude = ? AND longitude = ? AND product_timestamp >= ? "
            "ORDER BY product_timestamp ASC",
            (lat, lon, cutoff),
        ).fetchall()
    if not rows:
        return None

    times = [datetime.fromisoformat(r["product_timestamp"]) for r in rows]
    values = [r["intensity_mm_h"] for r in rows]

    total = 0.0
    for i in range(len(rows) - 1):
        gap = times[i + 1] - times[i]
        if gap > MAX_INTEGRATION_GAP:
            continue
        total += values[i] * (gap.total_seconds() / 3600.0)
    return round(total, 2)
