"""POLRAD SRI (precipitation rate) -- venue sampling and the map overlay PNG.

IMGW has no public WMS the way DWD does, so unlike the old ``app/dwd/radar.py``
(which only computed a bbox for the browser to hand straight to DWD's
GeoServer) this module does real work: download the HDF5 composite, decode it,
and either sample one point or render a crop as a PNG this app serves itself.

Format, confirmed by inspecting a live file
(``COMPO_SRI.comp.sri`` -> the newest ``*.sri.h5``, 22 KB, ODIM_H5/V2_3)::

    root.Conventions        = "ODIM_H5/V2_3"           standard OPERA/EUMETNET format
    dataset1/what.product   = "PCAPPI"
    dataset1/what.quantity  = "RATE"                    precipitation rate
    dataset1/what.gain/offset = 1.0 / 0.0                physical = raw*gain + offset
    dataset1/what.nodata    = -2.0                       outside radar coverage
    dataset1/what.undetect  = -1.0                       covered, no precipitation detected
    dataset1/data1/data     = float32[800, 800]           mm/h, row 0 = north edge
    where.projdef           = "+proj=aeqd +lon_0=19.0926 +lat_0=52.3469 +ellps=sphere"
    where.{UL,UR,LL,LR}_{lat,lon}, xscale, yscale, xsize, ysize   corner geolocation

Pixel <-> geographic conversion projects the UL corner and the query point into
the same azimuthal-equidistant plane and divides by xscale/yscale -- the
standard ODIM_H5 area transform, not a locally invented one.

The catalogue's file *listing* itself was observed to lag wall-clock time by
more than two hours during testing (new files exist on disk well before the
listing endpoint reflects them, or the listing itself is cached upstream), so
freshness is judged from the timestamp embedded in the file/its own metadata,
never from "it was the newest entry in the listing".
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

import h5py
import numpy as np
from PIL import Image
from pyproj import Transformer

from app.config import settings
from app.providers.http import fetch_bytes, field_cache
from app.providers.imgw import client

logger = logging.getLogger(__name__)

FILE_SUFFIX = ".sri.h5"

#: Composite is published every 5 minutes; no point asking IMGW more often.
RADAR_TTL = 240

#: field_cache key the decoded frame lands under. Exposed for
#: /api/health's cache-age reporting.
CACHE_KEY = "imgw:polrad:sri"


@dataclass
class RadarField:
    data: np.ndarray  # (ysize, xsize) float32, physical units (mm/h), raw values
    gain: float
    offset: float
    nodata: float
    undetect: float
    proj4: str
    ul_lon: float
    ul_lat: float
    lr_lon: float
    lr_lat: float
    ll_lon: float
    ll_lat: float
    ur_lon: float
    ur_lat: float
    xscale: float
    yscale: float
    xsize: int
    ysize: int
    valid_time: datetime

    def _to_proj(self) -> Transformer:
        return Transformer.from_crs("EPSG:4326", self.proj4, always_xy=True)

    def pixel_of(self, lat: float, lon: float) -> Optional[Tuple[int, int]]:
        """(row, col) for a lat/lon, or None if it falls outside the grid."""
        to_proj = self._to_proj()
        x_ul, y_ul = to_proj.transform(self.ul_lon, self.ul_lat)
        x, y = to_proj.transform(lon, lat)
        col = int(round((x - x_ul) / self.xscale))
        row = int(round((y_ul - y) / self.yscale))
        if 0 <= row < self.ysize and 0 <= col < self.xsize:
            return row, col
        return None

    def value_at(self, lat: float, lon: float) -> Optional[float]:
        """Physical value (mm/h) at a point, or None for nodata/out of coverage."""
        pixel = self.pixel_of(lat, lon)
        if pixel is None:
            return None
        raw = float(self.data[pixel])
        if raw == self.nodata:
            return None
        if raw == self.undetect:
            return 0.0
        return raw * self.gain + self.offset

    def grid_lonlat(self, width: int, height: int, bbox: Tuple[float, float, float, float]):
        """lon/lat meshgrid for an output image of ``width`` x ``height`` over ``bbox``."""
        min_lat, min_lon, max_lat, max_lon = bbox
        lons = np.linspace(min_lon, max_lon, width)
        lats = np.linspace(max_lat, min_lat, height)  # top row = north
        return np.meshgrid(lons, lats)

    def sample_grid(self, width: int, height: int, bbox: Tuple[float, float, float, float]) -> np.ndarray:
        """Physical values resampled (nearest) onto an output lat/lon grid. NaN = no data."""
        lon_grid, lat_grid = self.grid_lonlat(width, height, bbox)
        to_proj = self._to_proj()
        x, y = to_proj.transform(lon_grid, lat_grid)
        x_ul, y_ul = to_proj.transform(self.ul_lon, self.ul_lat)
        col = np.round((x - x_ul) / self.xscale).astype(np.int64)
        row = np.round((y_ul - y) / self.yscale).astype(np.int64)

        in_bounds = (row >= 0) & (row < self.ysize) & (col >= 0) & (col < self.xsize)
        out = np.full((height, width), np.nan, dtype=np.float64)
        safe_row = np.where(in_bounds, row, 0)
        safe_col = np.where(in_bounds, col, 0)
        raw = self.data[safe_row, safe_col]

        physical = raw * self.gain + self.offset
        physical = np.where(raw == self.undetect, 0.0, physical)
        valid = in_bounds & (raw != self.nodata)
        out[valid] = physical[valid]
        return out


def _decode(payload: bytes) -> RadarField:
    with h5py.File(io.BytesIO(payload), "r") as f:
        what = f["dataset1/what"].attrs
        where = f["where"].attrs
        data = np.array(f["dataset1/data1/data"][:], dtype=np.float64)

        def s(key: str) -> str:
            value = where[key]
            return value.decode() if isinstance(value, bytes) else str(value)

        date = what["startdate"]
        time_ = what["starttime"]
        date = date.decode() if isinstance(date, bytes) else str(date)
        time_ = time_.decode() if isinstance(time_, bytes) else str(time_)
        valid_time = datetime.strptime(date + time_, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)

        return RadarField(
            data=data,
            gain=float(what["gain"]),
            offset=float(what["offset"]),
            nodata=float(what["nodata"]),
            undetect=float(what["undetect"]),
            proj4=s("projdef"),
            ul_lon=float(where["UL_lon"]),
            ul_lat=float(where["UL_lat"]),
            lr_lon=float(where["LR_lon"]),
            lr_lat=float(where["LR_lat"]),
            ll_lon=float(where["LL_lon"]),
            ll_lat=float(where["LL_lat"]),
            ur_lon=float(where["UR_lon"]),
            ur_lat=float(where["UR_lat"]),
            xscale=float(where["xscale"]),
            yscale=float(where["yscale"]),
            xsize=int(where["xsize"]),
            ysize=int(where["ysize"]),
            valid_time=valid_time,
        )


def _fetch_latest() -> RadarField:
    def _fetch() -> RadarField:
        entry = client.latest_file(settings.imgw.radar_product, suffix=FILE_SUFFIX)
        payload = fetch_bytes(entry["url"])
        field = _decode(payload)
        logger.info("POLRAD SRI: decoded frame valid %s", field.valid_time.isoformat())
        return field

    return field_cache.get_or_fetch(CACHE_KEY, RADAR_TTL, _fetch)


def _fresh(field: RadarField) -> bool:
    age = datetime.now(timezone.utc) - field.valid_time
    return age <= timedelta(minutes=settings.imgw.radar_max_age_minutes)


def latest_field() -> Optional[RadarField]:
    """The newest decoded POLRAD SRI frame, or None if unavailable/too stale."""
    try:
        field = _fetch_latest()
    except Exception as exc:  # noqa: BLE001 - radar down must not break /api/summary
        logger.warning("POLRAD SRI unavailable: %s", exc)
        return None
    if not _fresh(field):
        logger.warning(
            "POLRAD SRI frame valid %s is older than the %d-minute staleness threshold",
            field.valid_time.isoformat(),
            settings.imgw.radar_max_age_minutes,
        )
        return None
    return field


def precipitation_intensity(lat: float, lon: float) -> Tuple[Optional[float], Optional[datetime]]:
    """Current precipitation intensity (mm/h) at a point, radar-estimated.

    Returns ``(None, None)`` rather than 0 when the radar is unavailable, too
    stale, or the point falls outside the composite -- see
    ``POLRAD_SRI_Implementation_Summary.md``: an absent reading must never be
    reported as "no rain".
    """
    field = latest_field()
    if field is None:
        return None, None
    return field.value_at(lat, lon), field.valid_time


def bbox_around(lat: float, lon: float, span_deg: float, aspect: float = 1.0) -> Tuple[float, float, float, float]:
    lon_span = span_deg / 0.6 * aspect  # cos(latitude) approximation, same as the old DWD helper
    return (
        round(lat - span_deg, 4),
        round(lon - lon_span, 4),
        round(lat + span_deg, 4),
        round(lon + lon_span, 4),
    )


def radar_info(span_deg: float = 1.6) -> Dict:
    """What the frontend needs to put the radar overlay on its map."""
    field = latest_field()
    bbox = bbox_around(settings.location.latitude, settings.location.longitude, span_deg)
    return {
        "provider": "imgw-polrad",
        "product": settings.imgw.radar_product,
        "available": field is not None,
        "valid_time": field.valid_time.isoformat() if field else None,
        "bbox": {"min_lat": bbox[0], "min_lon": bbox[1], "max_lat": bbox[2], "max_lon": bbox[3]},
        "image_url": "/api/radar.png",
        "attribution": (
            "Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej "
            "– Państwowy Instytut Badawczy. Dane Instytutu Meteorologii i Gospodarki "
            "Wodnej – Państwowego Instytutu Badawczego zostały przetworzone."
        ),
        "refresh_seconds": RADAR_TTL,
    }


#: mm/h lower bound -> RGBA. Values below the first band's bound render
#: transparent, same as "no rain here" rather than a coloured zero.
RAIN_RAMP = (
    (0.1, (100, 181, 246, 110)),
    (0.5, (33, 150, 243, 150)),
    (2.0, (76, 175, 80, 170)),
    (5.0, (255, 235, 59, 190)),
    (10.0, (255, 152, 0, 210)),
    (20.0, (244, 67, 54, 225)),
    (50.0, (156, 39, 176, 240)),
)


def _colorise(values: np.ndarray) -> np.ndarray:
    """mm/h grid (NaN = no data) -> RGBA uint8 image."""
    height, width = values.shape
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    for lower, color in RAIN_RAMP:
        mask = values >= lower
        rgba[mask] = color
    return rgba


def render(bbox: Tuple[float, float, float, float], width: int, height: int) -> Optional[Dict]:
    """A transparent PNG of the precipitation-rate composite over ``bbox``.

    Returns None if the radar is unavailable or too stale -- callers should
    treat that as "no overlay right now", never fall back to a fabricated
    image.
    """
    field = latest_field()
    if field is None:
        return None

    values = field.sample_grid(width, height, bbox)
    rgba = _colorise(values)

    buffer = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buffer, format="PNG")

    finite = values[np.isfinite(values)]
    return {
        "png": buffer.getvalue(),
        "valid": field.valid_time,
        "min": float(finite.min()) if finite.size else None,
        "max": float(finite.max()) if finite.size else None,
    }
