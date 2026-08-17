"""Assembles observations, forecast, warnings and FSI into one API payload.

The frontend makes a single request to ``/api/summary`` and gets everything it
needs, so a page load is one round trip and the DWD sources stay behind the
cache.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app import fsi
from app.config import settings
from app.providers import poland
from app.meteo import beaufort, wind_direction_name
from app.models import WeatherPoint, Warning
from app.i18n import normalise as normalise_lang
from app.weather_codes import describe

logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def _tz(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def _local_tz() -> ZoneInfo:
    return _tz(settings.location.timezone)


def _is_day(moment: datetime) -> bool:
    """Rough daylight test -- good enough for picking a sun or moon icon."""
    return 6 <= moment.astimezone(_local_tz()).hour < 21


def hour_now() -> datetime:
    """The hour we are currently in. Shared with the public API.

    The forecast keeps the hours of today that have already gone by so the
    charts can grey them out, which means anything that answers "what should I
    do next" -- the best and worst stretches, a day's best hour -- has to say
    explicitly that it means from here on.
    """
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _serialise_point(point: WeatherPoint, lang: str = "en") -> Dict[str, Any]:
    data = point.to_dict()
    local = point.time.astimezone(_local_tz())
    data["time_local"] = local.isoformat()
    data["hour"] = local.strftime("%H:%M")
    data["weather"] = describe(point.weather_code, _is_day(point.time), lang)
    data["beaufort"] = beaufort(point.wind_speed)
    data["wind_direction_name"] = wind_direction_name(point.wind_direction)
    if point.wind_speed is not None:
        data["wind_speed_kmh"] = round(point.wind_speed * 3.6, 1)
    if point.wind_gust is not None:
        data["wind_gust_kmh"] = round(point.wind_gust * 3.6, 1)
    return data


def _series_detail(point: WeatherPoint, lang: str = "en") -> Dict[str, Any]:
    """The conditions behind one bar of the hourly chart.

    Clicking a bar opens these, so every hour the frontend draws carries them,
    day cards included -- and the public forecast reads them too, which is why
    it no longer runs out of weather detail past +48 h. Only the fields that
    panel shows, to keep the aggregate from doubling in size.
    """
    local = point.time.astimezone(_local_tz())
    weather = describe(point.weather_code, _is_day(point.time), lang)
    return {
        "time_local": local.isoformat(),
        "hour": local.strftime("%H:%M"),
        "icon": weather["icon"],
        "weather": weather,
        "temperature": point.temperature,
        "humidity": point.humidity,
        "wind_speed_kmh": round(point.wind_speed * 3.6, 1)
        if point.wind_speed is not None
        else None,
        "wind_gust_kmh": round(point.wind_gust * 3.6, 1)
        if point.wind_gust is not None
        else None,
        "wind_direction_name": wind_direction_name(point.wind_direction),
        "precipitation": point.precipitation,
        "precipitation_prob": point.precipitation_prob,
        "cloud_cover": point.cloud_cover,
    }


def _mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 1) if values else None


def _mean_wind_direction(points: List[WeatherPoint]) -> Optional[float]:
    """Where the wind blew from over a day, as one bearing.

    Averaged as vectors and weighted by speed: the arithmetic mean of 350 deg
    and 10 deg is due south, the exact opposite of the truth, and a calm hour
    should not pull the figure around as hard as a windy one.
    """
    east = north = 0.0
    for point in points:
        if point.wind_direction is None:
            continue
        speed = point.wind_speed if point.wind_speed is not None else 1.0
        angle = math.radians(point.wind_direction)
        east += speed * math.sin(angle)
        north += speed * math.cos(angle)

    # Winds that cancel out exactly leave no direction worth naming.
    if abs(east) < 1e-9 and abs(north) < 1e-9:
        return None
    # Wrapped again after rounding: a hair under north rounds up to 360.0, and
    # the API should report that as 0.
    return round(math.degrees(math.atan2(east, north)) % 360.0, 1) % 360.0


#: How far below the day's peak an hour may sit and still belong to the same
#: stretch. Half a point is inside the noise of the forecast itself.
PEAK_TOLERANCE = 0.5


def _peak_window(
    scored: List[tuple], best: bool, tz: ZoneInfo
) -> Optional[Dict[str, Any]]:
    """The stretch of hours around the day's best (or worst) hour.

    A single hour is a poor answer to "when should I suit up": the hours either
    side are usually just as good, and naming only the peak made the row read
    like a train departure. Grow outwards from the peak while the score stays
    within ``PEAK_TOLERANCE`` of it, so the row names a window to plan around.
    """
    if not scored:
        return None

    order = range(len(scored))
    index = (max if best else min)(order, key=lambda i: scored[i][1])
    peak = scored[index][1]
    inside = (
        (lambda score: score >= peak - PEAK_TOLERANCE)
        if best
        else (lambda score: score <= peak + PEAK_TOLERANCE)
    )

    def adjacent(earlier: WeatherPoint, later: WeatherPoint) -> bool:
        # Elapsed hours are filtered out before we get here, but a gap in the
        # forecast must still break the stretch rather than be papered over.
        return later.time - earlier.time == timedelta(hours=1)

    first = last = index
    while first > 0 and inside(scored[first - 1][1]) and adjacent(
        scored[first - 1][0], scored[first][0]
    ):
        first -= 1
    while last + 1 < len(scored) and inside(scored[last + 1][1]) and adjacent(
        scored[last][0], scored[last + 1][0]
    ):
        last += 1

    return {
        # ISO, not "HH:MM": the reader chooses 24- or 12-hour clock, and only
        # the frontend knows which.
        "start": scored[first][0].time.astimezone(tz).isoformat(),
        "end": (scored[last][0].time + timedelta(hours=1)).astimezone(tz).isoformat(),
        "hours": last - first + 1,
        # Named as in _longest_run, so both kinds of window read the same.
        "peak_score": peak,
    }


def _dominant_code(points: List[WeatherPoint]) -> Optional[int]:
    """Pick the ww code that best represents a day.

    Rain and thunder are what people need to know about, so they outrank cloud
    cover -- but only once they last more than a single hour, otherwise one
    passing shower would label an otherwise sunny day as wet. With no
    significant weather we take the most common sky condition, not the worst,
    so a day with eleven hours of sun does not get filed as "Overcast".
    """
    codes = [p.weather_code for p in points if p.weather_code is not None]
    if not codes:
        return None

    significant = [c for c in codes if c >= 45]
    if len(significant) >= 2:
        return max(significant)

    counts = Counter(codes)
    most_common = max(counts.values())
    return min(code for code, count in counts.items() if count == most_common)


def _daily_summary(
    points: List[WeatherPoint],
    scores: Dict[str, float],
    extremes: Dict[str, Dict[str, float]],
    lang: str = "en",
) -> List[Dict[str, Any]]:
    """Group the hourly forecast into per-day cards.

    Daytime figures (07:00-22:00 local) drive the fursuiting numbers, because
    that is when anyone is actually outside in suit.
    """
    tz = _local_tz()
    now = hour_now()
    by_day: Dict[str, List[WeatherPoint]] = {}
    for point in points:
        by_day.setdefault(point.time.astimezone(tz).date().isoformat(), []).append(point)

    days: List[Dict[str, Any]] = []
    for day, day_points in sorted(by_day.items()):
        temps = [p.temperature for p in day_points if p.temperature is not None]
        daytime = [p for p in day_points if 7 <= p.time.astimezone(tz).hour <= 22]
        # "Best hour" is advice, so an hour that has already gone by cannot win
        # it -- the bars still show those hours, greyed, for context.
        pool = daytime or day_points
        ahead = [p for p in pool if p.time >= now] or pool
        scored = [(p, scores[p.time.isoformat()]) for p in ahead if p.time.isoformat() in scores]
        best = max(scored, key=lambda item: item[1], default=None)
        worst = min(scored, key=lambda item: item[1], default=None)

        dominant = _dominant_code(daytime or day_points)

        # Every hour of the day, so each card can carry its own bar chart.
        series = [
            {
                "time": p.time.isoformat(),
                "hour": p.time.astimezone(tz).strftime("%H"),
                "score": scores[p.time.isoformat()],
                "color": _score_color(scores[p.time.isoformat()]),
                "temperature": p.temperature,
                # Icons above the bars show how the weather builds through the day.
                "icon": describe(p.weather_code, _is_day(p.time), lang)["icon"],
            }
            for p in day_points
            if p.time.isoformat() in scores
        ]

        recorded = extremes.get(day, {})
        direction = _mean_wind_direction(day_points)

        best_window = _peak_window(scored, True, tz)
        worst_window = _peak_window(scored, False, tz)
        # Late in the day only an hour or two is left to forecast, and the two
        # windows collapse onto the same stretch. Naming one hour twice, once as
        # the best and once as the worst, tells the reader nothing.
        if best_window == worst_window:
            worst_window = None

        days.append(
            {
                "date": day,
                "weekday": datetime.fromisoformat(day).strftime("%A"),  # frontend re-formats per locale
                # The first and last day of the horizon are only part-covered,
                # so the frontend can label them instead of showing a
                # misleading one-hour "high and low".
                "hour_count": len(day_points),
                "partial": len(day_points) < 12,
                "series": series,
                "temp_max": recorded.get("temp_max", max(temps) if temps else None),
                "temp_min": recorded.get("temp_min", min(temps) if temps else None),
                "precipitation": round(
                    sum(p.precipitation or 0.0 for p in day_points), 1
                ),
                "precipitation_prob": max(
                    (p.precipitation_prob or 0.0 for p in day_points), default=None
                ),
                "wind_speed_kmh": _mean(
                    [p.wind_speed * 3.6 for p in day_points if p.wind_speed is not None]
                ),
                "wind_gust_kmh": round(
                    max((p.wind_gust or 0.0 for p in day_points), default=0.0) * 3.6, 1
                ),
                "wind_direction": direction,
                "wind_direction_name": wind_direction_name(direction),
                "humidity": _mean([p.humidity for p in day_points if p.humidity is not None]),
                "sunshine_hours": round(
                    sum(p.sunshine_minutes or 0.0 for p in day_points) / 60.0, 1
                ),
                "weather": describe(dominant, True, lang),
                # Best and worst are taken from 07:00-22:00, the hours anyone is
                # realistically outside in suit.
                "fsi_max": best[1] if best else None,
                "fsi_best_hour": best[0].time.astimezone(tz).strftime("%H:%M") if best else None,
                "fsi_min": worst[1] if worst else None,
                "fsi_worst_hour": worst[0].time.astimezone(tz).strftime("%H:%M") if worst else None,
                # The stretch each peak sits in, which is what the day row shows.
                "fsi_best_window": best_window,
                "fsi_worst_window": worst_window,
                "fsi_avg": _mean([score for _, score in scored]),
            }
        )
    return days


SUITABLE = 5.0  # at or above this an hour counts as worth going out in

#: Colours live in app/fsi.py so the scale is defined exactly once.
_score_color = fsi.band_color


def _longest_run(
    points: List[WeatherPoint],
    scores: Dict[str, float],
    suitable: bool,
    within_hours: int,
) -> Optional[Dict[str, Any]]:
    """Longest unbroken stretch of hours that is (or is not) worth going out in.

    ``suitable=True`` gives the best window to plan around; ``suitable=False``
    gives the stretch to warn people away from.
    """
    tz = _local_tz()
    now = hour_now()
    cutoff = now + timedelta(hours=within_hours)
    # Bounded at both ends: the series carries elapsed hours for the charts, and
    # a window that opened this morning is no use to anyone reading this now.
    candidates = [p for p in points if now <= p.time <= cutoff and p.time.isoformat() in scores]

    best_run: List[WeatherPoint] = []
    current: List[WeatherPoint] = []
    for point in candidates:
        if (scores[point.time.isoformat()] >= SUITABLE) == suitable:
            current.append(point)
            if len(current) > len(best_run):
                best_run = list(current)
        else:
            current = []

    if not best_run:
        return None

    run_scores = [scores[p.time.isoformat()] for p in best_run]
    start = best_run[0].time.astimezone(tz)
    end = (best_run[-1].time + timedelta(hours=1)).astimezone(tz)
    return {
        # ISO, not a formatted string: weekday names have to follow the
        # reader's language, which only the frontend knows.
        "start": start.isoformat(),
        "end": end.isoformat(),
        "same_day": end.date() == start.date(),
        "hours": len(best_run),
        "peak_score": max(run_scores) if suitable else min(run_scores),
    }


def _collect(fetch, label: str, default):
    """Run one upstream fetch, degrading to a default instead of failing the page."""
    try:
        return fetch(), None
    except Exception as exc:  # noqa: BLE001
        logger.error("Source %s unavailable: %s", label, exc)
        return default, f"{label} unavailable"


def _pollen_here() -> List[Dict[str, Any]]:
    """Today's pollen over the venue, heaviest species first."""
    return poland.pollen_at_point(settings.location.latitude, settings.location.longitude)


def _fill_elapsed_gaps(points: List[WeatherPoint]) -> List[WeatherPoint]:
    """Put back elapsed hours of today that no forecast we hold covers.

    Open-Meteo's hourly series starts from whenever it was last fetched, not
    from local midnight, so the hours between midnight and the first cached
    point are missing from the chart until this backfills them.

    ``app.providers.imgw.observations`` keeps the last ~24h of *measured*
    SYNOP readings in SQLite, so the hole can be filled from what actually
    happened rather than from memory -- and, being on disk, it survives a
    restart the way an in-process cache would not. Only genuine gaps are
    filled, so a page load with a complete day fetches nothing extra, and
    observed hours are never used to overwrite a forecast hour we already have.
    """
    if not points:
        return points

    now = hour_now()
    # Same boundary the forecast parser uses, so the two cannot disagree about
    # where "today" starts.
    floor = poland.day_floor(now).astimezone(timezone.utc)

    have = {p.time for p in points}
    wanted = []
    stamp = floor
    while stamp <= now:
        if stamp not in have:
            wanted.append(stamp)
        stamp += timedelta(hours=1)
    if not wanted:
        return points

    observed, _ = _collect(poland.fetch_recent, "observations", [])
    by_hour = {
        p.time.replace(minute=0, second=0, microsecond=0): p for p in observed or []
    }
    recovered = [by_hour[stamp] for stamp in wanted if stamp in by_hour]
    if not recovered:
        logger.warning(
            "%d elapsed hour(s) of today missing and not in the POI report either",
            len(wanted),
        )
        return points

    logger.info(
        "Filled %d of %d missing elapsed hour(s) from POI observations",
        len(recovered),
        len(wanted),
    )
    return sorted(points + recovered, key=lambda p: p.time)


def build_summary(lang: str = "en") -> Dict[str, Any]:
    lang = normalise_lang(lang)
    forecast, forecast_error = _collect(
        poland.fetch_forecast, "forecast", {"points": [], "issued": None, "extremes": {}}
    )
    # Fetched first and handed to fetch_current below, so the headline card
    # borrows its weather_code from the exact same Open-Meteo series the
    # hourly chart is drawn from, rather than a second independent fetch that
    # can disagree with the chart about whether this hour is a thunderstorm.
    forecast_points = forecast.get("points", [])
    current_point, current_error = _collect(
        lambda: poland.fetch_current(forecast_points=forecast_points), "observations", None
    )
    fetched_warnings, warnings_error = _collect(
        lambda: poland.fetch_warnings(lang=lang), "warnings", []
    )
    # What is in the air here, not the map of where it is worst. Only the
    # species in season answer, so this is two file reads in summer and none at
    # all in December, both of them cached for six hours behind the daily run.
    pollen_here = (
        _collect(_pollen_here, "pollen", [])[0] if settings.pollen.enabled else []
    )

    points: List[WeatherPoint] = _fill_elapsed_gaps(forecast.get("points", []))
    warning_list: List[Warning] = fetched_warnings or []

    # Fall back to the forecast if the station report is missing -- the hour we
    # are in, not points[0], which is now the first hour of *today*.
    if current_point is None and points:
        now = hour_now()
        current_point = next((p for p in points if p.time >= now), points[-1])

    series = fsi.compute_series(points, lang)
    detail = {p.time.isoformat(): p for p in points}
    for entry in series:
        point = detail.get(entry["time"])
        if point is not None:
            entry.update(_series_detail(point, lang))
    scores = {entry["time"]: entry["score"] for entry in series}

    # Warnings ride alongside the score, they do not change it: they are marked
    # on the bars and listed for the reader to weigh themselves.
    current_fsi = fsi.compute(current_point, lang) if current_point else None

    degraded = [e for e in (current_error, forecast_error, warnings_error) if e]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "language": lang,
        "bands": fsi.band_scale(lang),
        # What the four parts of the score are called. Once, rather than on
        # every hour of the series that carries them -- see compute_series.
        "subscore_labels": fsi.subscore_labels(lang),
        "site_name": settings.site_name,
        "notifications": {
            "telegram_url": settings.notifications.telegram_url,
            "channel_name": settings.notifications.channel_name,
            "disclaimer": settings.notifications.disclaimer.get(lang)
            or settings.notifications.disclaimer.get("en"),
        },
        "event": {"name": settings.event.name, "short_name": settings.event.short_name},
        "location": {
            "name": settings.location.name,
            "latitude": settings.location.latitude,
            "longitude": settings.location.longitude,
            "timezone": settings.location.timezone,
            "station_id": settings.imgw.station_id,
            # The operator's configured name wins; Open-Meteo's forecast (the
            # only source that could suggest one) never names a station, so the
            # fallback is only ever reached if the operator left this blank.
            "station_name": settings.imgw.station_name or forecast.get("station_name"),
        },
        "current": _serialise_point(current_point, lang) if current_point else None,
        "fsi": current_fsi.to_dict() if current_fsi else None,
        "fsi_series": series,
        "best_window": _longest_run(points, scores, suitable=True, within_hours=24),
        "worst_window": _longest_run(points, scores, suitable=False, within_hours=24),
        # No separate "hourly" list any more: every scored hour carries its own
        # conditions, which the public forecast and the click-a-bar panel both
        # read. The old one stopped at +48 h and repeated 28 kB of it.
        "daily": _daily_summary(points, scores, forecast.get("extremes", {}), lang),
        "warnings": [w.to_dict() for w in warning_list],
        # Alongside the warnings rather than among them: DWD issues those, this
        # is our own reading of a research forecast, and the two should never be
        # mistaken for one another. `warn` marks the ones heavy enough to say
        # out loud -- see pollen.WARN_FROM_LEVEL.
        "pollen": pollen_here,
        "forecast_issued": forecast.get("issued").isoformat()
        if forecast.get("issued")
        else None,
        "degraded": degraded,
        "attribution": (
            "Data: IMGW-PIB (observations, warnings, POLRAD radar) "
            "and Open-Meteo (forecast, pollen)"
        ),
    }
