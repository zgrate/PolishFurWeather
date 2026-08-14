"""COSMO 2.8km model fields (cloud + rain, 2m temperature, 10m wind) as map images.

IMGW publishes COSMO_HVD as record-sequential GRIB1: one file per forecast
hour, every field for that hour bundled together, roughly 150 MB each. There
is no per-parameter file the way DWD's ICON-D2 offers one, and the datastore
does not honour HTTP Range requests (confirmed: a ranged GET still streamed
the whole body), so a fetch always pays for the entire file. That is too slow
to do inside a request -- each step is cached for hours once fetched (see
STEP_TTL below) rather than re-fetched per render.

Two things here were confirmed against a real downloaded file rather than
taken from IMGW's own readme.txt, which turned out to describe a different,
coarser COSMO configuration than what COSMO_HVD actually ships:

* Grid geometry. The readme claims a 415x460 grid at 0.0625 deg spacing and a
  rotated pole at (lat 32.5, lon -170). The GDS bytes of a live file instead
  give a 380x405 grid at 0.025 deg spacing (~2.8 km -- consistent with the
  product's own "2k8" name, the readme's number is not) and a south pole at
  (lat -40, lon 10), equivalently a north pole at (lat 40, lon -170deg). The
  rotated<->true coordinate round-trip was verified numerically, and the
  domain it produces is centred almost exactly on Poland.
* Field identity. IMGW's readme lists fixed record positions per field
  (T_2M as record 365, TOT_PREC as 383, etc.). Those positions were
  cross-checked against each message's own PDS parameter/level-type/level
  codes on a live file and matched exactly -- see FIELDS below, which is
  keyed on the PDS codes (the standards-compliant identifier), not on
  position, so a future reordering would still be found correctly rather
  than silently reading the wrong field.

Point-forecast / multi-step blending with Open-Meteo is out of scope here: at
one ~150 MB fetch per forecast hour, walking COSMO's own 60-hour horizon
would mean dozens of these downloads, which is not practical to do for a
single API response. Forecast hours are served by Open-Meteo instead (see
app/providers/open_meteo/forecast.py); this module only drives the model-map
card (/api/model, /api/model.png).
"""

from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw

from app.config import settings
from app.providers.http import cache, field_cache, get_session
from app.providers.imgw import client
from app.providers.imgw import grib1

logger = logging.getLogger(__name__)

RUN_HOURS = (0, 6, 12, 18)
MAX_STEP = 60
#: ~150 MB per file over a datastore that does not support Range requests.
FETCH_TIMEOUT = 180
RUN_TTL = 900
#: A run stays "latest" for up to 6h; keep decoded steps around well past
#: that so replaying the model card's animation does not refetch.
STEP_TTL = 3 * 3600

#: key -> (indicatorOfParameter, levelType, level), confirmed against a live
#: 2026-08-13 00Z COSMO_HVD_00_00 file. See module docstring.
FIELDS: Dict[str, Tuple[int, int, int]] = {
    "t_2m": (11, 105, 2),
    "td_2m": (17, 105, 2),
    "u_10m": (33, 105, 10),
    "v_10m": (34, 105, 10),
    "vmax_10m": (187, 105, 10),
    "pmsl": (2, 102, 0),
    "clct": (71, 1, 0),
    "tot_prec": (61, 1, 0),
    "relhum_2m": (52, 105, 2),
}


def _dataset_product_id(run_hour: int) -> str:
    return f"{settings.imgw.cosmo_product_prefix}_{run_hour:02d}_00"


def _run_files(run_hour: int) -> List[Dict[str, str]]:
    return client.product_files(_dataset_product_id(run_hour))


def latest_run() -> datetime:
    """The most recently published COSMO_HVD run, across the four run hours."""

    def _probe() -> datetime:
        best: Optional[datetime] = None
        for run_hour in RUN_HOURS:
            try:
                files = _run_files(run_hour)
            except client.ProductNotFound:
                continue
            stamp = next((f["file"][:12] for f in files if f["file"] != "readme.txt"), None)
            if not stamp:
                continue
            run = datetime.strptime(stamp, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
            if best is None or run > best:
                best = run
        if best is None:
            raise RuntimeError("No COSMO_HVD run currently published")
        return best

    return cache.get_or_fetch("imgw:cosmo:run", RUN_TTL, _probe)


def _step_suffix(step: int) -> str:
    day, hour = divmod(step, 24)
    return f"lfff{day:02d}{hour:02d}0000"


def _step_url(run: datetime, step: int) -> str:
    files = _run_files(run.hour)
    suffix = _step_suffix(step)
    for entry in files:
        if entry["file"].endswith(suffix):
            return entry["url"]
    raise client.ProductNotFound(f"COSMO +{step}h not published yet for the {run.isoformat()} run")


@dataclass
class CosmoStep:
    run: datetime
    step: int
    valid_time: datetime
    ni: int
    nj: int
    la1: float
    lo1: float
    dlat: float
    dlon: float
    #: Geographic north pole of the rotated grid (see _true_to_rot). GRIB1's
    #: GDS gives the *south* pole (confirmed on a live file: lat -40, lon 10);
    #: these are that point's antipode, converted once in _fetch_step.
    pole_lat: float
    pole_lon: float
    fields: Dict[str, np.ndarray] = field(default_factory=dict)

    def sample_grid(
        self, values: np.ndarray, width: int, height: int, bbox: Tuple[float, float, float, float]
    ) -> np.ndarray:
        """Nearest-neighbour resample onto a north-up true lat/lon output grid.

        The source grid is rotated, so unlike a plain lat/lon field its rows
        and columns are not independently croppable by true latitude and
        longitude -- every output pixel's true coordinate has to be rotated
        into the model's own frame and looked up individually.
        """
        min_lat, min_lon, max_lat, max_lon = bbox
        lons = np.linspace(min_lon, max_lon, width)
        lats = np.linspace(max_lat, min_lat, height)  # top row = north
        lon_grid, lat_grid = np.meshgrid(lons, lats)
        rot_lat, rot_lon = _true_to_rot(lat_grid, lon_grid, self.pole_lat, self.pole_lon)
        row = np.round((rot_lat - self.la1) / self.dlat).astype(np.int64)
        col = np.round((rot_lon - self.lo1) / self.dlon).astype(np.int64)

        in_bounds = (row >= 0) & (row < self.nj) & (col >= 0) & (col < self.ni)
        out = np.full((height, width), np.nan, dtype=np.float64)
        safe_row = np.where(in_bounds, row, 0)
        safe_col = np.where(in_bounds, col, 0)
        out[in_bounds] = values[safe_row, safe_col][in_bounds]
        return out

    def value_at(self, values: np.ndarray, lat: float, lon: float) -> Optional[float]:
        rot_lat, rot_lon = _true_to_rot(lat, lon, self.pole_lat, self.pole_lon)
        row = int(round((rot_lat - self.la1) / self.dlat))
        col = int(round((rot_lon - self.lo1) / self.dlon))
        if 0 <= row < self.nj and 0 <= col < self.ni:
            return float(values[row, col])
        return None


def _true_to_rot(lat, lon, pole_lat: float, pole_lon: float):
    """True lat/lon (deg) -> the model's rotated lat/lon (deg).

    Standard COSMO rotated-pole transform, given the geographic north pole of
    the rotated grid. Verified by round-tripping through the inverse
    transform back to the original point (see module docstring) rather than
    assumed correct.
    """
    sinpol = math.sin(math.radians(pole_lat))
    cospol = math.cos(math.radians(pole_lat))
    lampol = math.radians(pole_lon)
    phi = np.radians(lat)
    rla = np.radians(lon)

    arg = cospol * np.cos(phi) * np.cos(rla - lampol) + sinpol * np.sin(phi)
    rot_lat = np.degrees(np.arcsin(np.clip(arg, -1.0, 1.0)))

    arg1 = -np.sin(rla - lampol) * np.cos(phi)
    arg2 = -sinpol * np.cos(phi) * np.cos(rla - lampol) + cospol * np.sin(phi)
    rot_lon = np.degrees(np.arctan2(arg1, arg2))
    return rot_lat, rot_lon


def _fetch_step(run: datetime, step: int) -> CosmoStep:
    def _load() -> CosmoStep:
        url = _step_url(run, step)
        logger.info("Fetching COSMO_HVD %s +%dh (large file, can take a while)", run.isoformat(), step)
        response = get_session().get(url, timeout=FETCH_TIMEOUT)
        response.raise_for_status()
        data = response.content

        wanted = set(FIELDS.values())
        found: Dict[Tuple[int, int, int], np.ndarray] = {}
        geometry: Optional[grib1.Grib1Message] = None
        for start, _ in grib1.message_bounds(data):
            if len(found) == len(wanted):
                break
            signature = grib1.peek(data, start)
            if signature not in wanted or signature in found:
                continue
            message = grib1.decode_message(data, start)
            found[signature] = message.values
            if geometry is None:
                geometry = message

        missing = [key for key, sig in FIELDS.items() if sig not in found]
        if missing or geometry is None:
            raise grib1.UnsupportedGrib(f"COSMO file is missing expected fields: {missing}")

        # GRIB1's GDS gives the rotated grid's south pole; _true_to_rot wants
        # its north pole, the antipodal point.
        north_pole_lat = -geometry.pole_lat
        north_pole_lon = geometry.pole_lon + 180.0
        if north_pole_lon > 180.0:
            north_pole_lon -= 360.0

        return CosmoStep(
            run=run,
            step=step,
            valid_time=run + timedelta(hours=step),
            ni=geometry.ni,
            nj=geometry.nj,
            la1=geometry.la1,
            lo1=geometry.lo1,
            dlat=geometry.dlat,
            dlon=geometry.dlon,
            pole_lat=north_pole_lat,
            pole_lon=north_pole_lon,
            fields={key: found[sig] for key, sig in FIELDS.items()},
        )

    return field_cache.get_or_fetch(f"imgw:cosmo:{run:%Y%m%d%H}:{step}", STEP_TTL, _load)


def _rain_rate_mm_h(run: datetime, step: int) -> np.ndarray:
    """mm in the hour before ``step``, from the difference of two cumulative totals.

    TOT_PREC is accumulated since the run started, not an hourly figure --
    see FIELDS' comment and the readme's own "accumulated total precipitation"
    wording. Step 0 has nothing accumulated yet, so its rate is zero rather
    than requiring a step -1 that does not exist.
    """
    current = _fetch_step(run, step).fields["tot_prec"]
    if step <= 0:
        return np.zeros_like(current)
    previous = _fetch_step(run, step - 1).fields["tot_prec"]
    # A model can nudge a field down between steps (nowcast blending); a
    # negative rate would be nonsense, so floor at zero rather than show it.
    return np.clip(current - previous, 0.0, None)


# --------------------------------------------------------------- rendering
#
# Colour ramps, banding and legend shape mirror the DWD-era ICON-D2 renderer
# this replaces (same field set, same units after conversion), but the
# resampling step is COSMO's own sample_grid rather than a crop-then-resize:
# a rotated grid has no independently croppable rows/columns, so every output
# pixel is looked up individually and already lands at the final resolution.

Stops = Sequence[Tuple[float, int, int, int]]

RAMPS: Dict[str, Stops] = {
    "temperature": (
        (0.00, 68, 90, 204),
        (0.25, 74, 175, 214),
        (0.45, 96, 190, 130),
        (0.60, 233, 205, 92),
        (0.78, 226, 132, 61),
        (1.00, 196, 58, 58),
    ),
    "clouds": (
        (0.00, 150, 165, 190),
        (1.00, 255, 255, 255),
    ),
    "wind": (
        (0.00, 40, 90, 120),
        (0.35, 90, 190, 190),
        (0.70, 255, 214, 51),
        (1.00, 241, 60, 163),
    ),
    "rain": (
        (0.00, 90, 170, 255),
        (0.35, 60, 225, 255),
        (0.70, 255, 214, 51),
        (1.00, 241, 60, 163),
    ),
}

RAIN_MAX = 10.0
RAIN_MIN = 0.08
RAIN_TICKS = (0.1, 0.5, 1.0, 2.0, 5.0, 10.0)

STEPS: Dict[str, float] = {
    "clouds": 12.5,
    "temperature": 2.0,
    "wind": 5.0,
}


@dataclass(frozen=True)
class Parameter:
    key: str
    unit: str
    vmin: float
    vmax: float


PARAMETERS: Dict[str, Parameter] = {
    "clouds": Parameter("clouds", "%", 0.0, 100.0),
    "temperature": Parameter("temperature", "°C", -6.0, 36.0),
    "wind": Parameter("wind", "km/h", 0.0, 60.0),
}


def ramp_lookup(stops: Stops) -> np.ndarray:
    positions = np.array([s[0] for s in stops])
    colors = np.array([[s[1], s[2], s[3]] for s in stops], dtype=float)
    x = np.linspace(0.0, 1.0, 256)
    return np.stack([np.interp(x, positions, colors[:, c]) for c in range(3)], axis=1).astype(
        np.uint8
    )


def _normalise(values: np.ndarray, vmin: float, vmax: float) -> np.ndarray:
    return np.clip((values - vmin) / (vmax - vmin), 0.0, 1.0)


def _band_count(vmin: float, vmax: float, step: float) -> int:
    return max(1, round((vmax - vmin) / step))


def _colorise(
    values: np.ndarray, stops: Stops, vmin: float, vmax: float, step: Optional[float] = None
) -> np.ndarray:
    scaled = _normalise(np.nan_to_num(values, nan=vmin), vmin, vmax)
    if step:
        count = _band_count(vmin, vmax, step)
        scaled = (np.floor(scaled * count).clip(0, count - 1) + 0.5) / count
    return ramp_lookup(stops)[(scaled * 255).astype(np.uint8)]


def _oktas(cloud: np.ndarray) -> np.ndarray:
    return np.clip(np.ceil(np.nan_to_num(cloud, nan=0.0) / STEPS["clouds"]), 0, 8)


def _render_clouds(clct: np.ndarray, rain_rate: np.ndarray) -> Image.Image:
    oktas = _oktas(clct)
    rgb = ramp_lookup(RAMPS["clouds"])[(oktas / 8.0 * 255).astype(np.uint8)]
    alpha = (oktas / 8.0 * 175).astype(np.uint8)
    image = Image.fromarray(np.dstack([rgb, alpha[..., None]]).astype(np.uint8), mode="RGBA")

    wet = np.isfinite(rain_rate) & (rain_rate >= RAIN_MIN)
    if wet.any():
        positive = np.clip(np.nan_to_num(rain_rate), 0.0, None)
        rain_rgb = _colorise(np.sqrt(positive), RAMPS["rain"], 0.0, math.sqrt(RAIN_MAX))
        strength = np.clip((positive - RAIN_MIN) / 1.2, 0.25, 1.0)
        rain_alpha = np.where(wet, strength * 245, 0).astype(np.uint8)
        image.alpha_composite(
            Image.fromarray(np.dstack([rain_rgb, rain_alpha[..., None]]).astype(np.uint8), "RGBA")
        )
    return image


def _banded(values: np.ndarray, key: str, opacity: int) -> Image.Image:
    parameter = PARAMETERS[key]
    rgb = _colorise(values, RAMPS[key], parameter.vmin, parameter.vmax, STEPS[key])
    alpha = np.where(np.isfinite(values), opacity, 0).astype(np.uint8)
    return Image.fromarray(np.dstack([rgb, alpha[..., None]]).astype(np.uint8), mode="RGBA")


def _draw_arrows(
    image: Image.Image,
    u: np.ndarray,
    v: np.ndarray,
    spacing: int = 54,
    color: Tuple[int, int, int, int] = (255, 255, 255, 225),
) -> None:
    width, height = image.size
    ny, nx = u.shape
    cols = max(2, width // spacing)
    rows = max(2, height // spacing)

    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    for row in range(rows):
        for col in range(cols):
            px = (col + 0.5) * width / cols
            py = (row + 0.5) * height / rows
            i = min(nx - 1, int(px / width * nx))
            j = min(ny - 1, int(py / height * ny))

            ue, vn = u[j, i], v[j, i]
            if not (np.isfinite(ue) and np.isfinite(vn)):
                continue
            speed = math.hypot(ue, vn)
            if speed < 0.5:
                continue

            length = min(spacing * 0.44, 9.0 + speed * 1.5)
            dx = ue / speed * length
            dy = -vn / speed * length
            x0, y0 = px - dx / 2, py - dy / 2
            x1, y1 = px + dx / 2, py + dy / 2

            draw.line([(x0, y0), (x1, y1)], fill=color, width=2)
            angle = math.atan2(dy, dx)
            for offset in (2.5, -2.5):
                draw.line(
                    [
                        (x1, y1),
                        (x1 + math.cos(angle + offset) * 6.5, y1 + math.sin(angle + offset) * 6.5),
                    ],
                    fill=color,
                    width=2,
                )
    image.alpha_composite(layer)


def render(
    param_key: str, step: int, bbox: Tuple[float, float, float, float], width: int = 900
) -> Dict:
    """Return ``{"png": bytes, "run": datetime, "min": float, "max": float}``."""
    run = latest_run()
    cosmo_step = _fetch_step(run, step)
    height = max(1, round(width * (bbox[2] - bbox[0]) / (bbox[3] - bbox[1])))

    if param_key == "clouds":
        clct = cosmo_step.sample_grid(cosmo_step.fields["clct"], width, height, bbox)
        rain = cosmo_step.sample_grid(_rain_rate_mm_h(run, step), width, height, bbox)
        image = _render_clouds(clct, rain)
        scale = clct
    elif param_key == "temperature":
        temp = cosmo_step.sample_grid(cosmo_step.fields["t_2m"], width, height, bbox) - 273.15
        image = _banded(temp, "temperature", 235)
        scale = temp
    elif param_key == "wind":
        u = cosmo_step.sample_grid(cosmo_step.fields["u_10m"], width, height, bbox)
        v = cosmo_step.sample_grid(cosmo_step.fields["v_10m"], width, height, bbox)
        speed = np.hypot(u, v) * 3.6
        image = _banded(speed, "wind", 170)
        _draw_arrows(image, u, v)
        scale = speed
    else:
        raise ValueError(f"Unknown model parameter {param_key!r}")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)

    return {
        "png": buffer.getvalue(),
        "run": cosmo_step.run,
        "valid": cosmo_step.valid_time,
        "min": float(np.nanmin(scale)) if np.isfinite(scale).any() else None,
        "max": float(np.nanmax(scale)) if np.isfinite(scale).any() else None,
    }


def _hex_ramp(name: str) -> List[List]:
    return [[s[0], f"#{s[1]:02x}{s[2]:02x}{s[3]:02x}"] for s in RAMPS[name]]


OPACITY: Dict[str, float] = {"clouds": 175 / 255, "temperature": 235 / 255, "wind": 170 / 255}


def _rgba(name: str, position: float, alpha: float) -> str:
    r, g, b = ramp_lookup(RAMPS[name])[int(np.clip(position, 0.0, 1.0) * 255)]
    return f"rgba({r}, {g}, {b}, {alpha:.3f})"


def _bands(key: str) -> List[Dict]:
    if key == "clouds":
        return [
            {
                "from": okta * STEPS["clouds"],
                "to": (okta + 1) * STEPS["clouds"],
                "color": _rgba(
                    "clouds", (okta + 1) / 8, max(0.16, (okta + 1) / 8 * OPACITY["clouds"])
                ),
                "label": f"{okta + 1}/8",
            }
            for okta in range(8)
        ]

    parameter = PARAMETERS[key]
    step = STEPS[key]
    count = _band_count(parameter.vmin, parameter.vmax, step)
    return [
        {
            "from": round(parameter.vmin + index * step, 3),
            "to": round(parameter.vmin + (index + 1) * step, 3),
            "color": _rgba(key, (index + 0.5) / count, OPACITY[key]),
        }
        for index in range(count)
    ]


def describe_parameters(bbox: Tuple[float, float, float, float]) -> Dict:
    """Metadata for the frontend: fields, ranges, overlays and the current run."""
    try:
        run = latest_run().isoformat()
    except Exception:  # noqa: BLE001 - the card degrades to "unavailable"
        run = None

    return {
        "run": run,
        "max_step": MAX_STEP,
        "bbox": {"min_lat": bbox[0], "min_lon": bbox[1], "max_lat": bbox[2], "max_lon": bbox[3]},
        "parameters": {
            "clouds": {
                "unit": "%",
                "min": 0.0,
                "max": 100.0,
                "step": STEPS["clouds"],
                "bands": _bands("clouds"),
                "ramp": _hex_ramp("clouds"),
                "overlay": {
                    "label": "Rain",
                    "unit": "mm/h",
                    "min": RAIN_MIN,
                    "max": RAIN_MAX,
                    "ramp": _hex_ramp("rain"),
                    "ticks": [
                        {"value": value, "at": math.sqrt(value) / math.sqrt(RAIN_MAX)}
                        for value in RAIN_TICKS
                    ],
                },
            },
            "temperature": {
                "unit": "°C",
                "min": PARAMETERS["temperature"].vmin,
                "max": PARAMETERS["temperature"].vmax,
                "step": STEPS["temperature"],
                "bands": _bands("temperature"),
                "ramp": _hex_ramp("temperature"),
            },
            "wind": {
                "unit": "km/h",
                "min": PARAMETERS["wind"].vmin,
                "max": PARAMETERS["wind"].vmax,
                "step": STEPS["wind"],
                "bands": _bands("wind"),
                "ramp": _hex_ramp("wind"),
                "arrows": True,
            },
        },
        "model": "COSMO 2.8km (IMGW-PIB), rotated lat-lon",
    }
