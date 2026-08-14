"""The Fursuitability Index.

Author: laffiesphere.

A 0-10 score for how pleasant (and how safe) it is to be in a fursuit outdoors.
Four weighted sub-scores feed the result:

    thermal_humidity  heat stress -- wet-bulb temperature plus a solar load,
                      because a suit blocks evaporative cooling and soaks up sun
    precipitation     rain now and the odds of rain, plus a "wet ground" penalty
    wind              U-shaped: still air traps heat, gales knock heads off
    stickiness        dew point, i.e. how clammy the air feels

The result is then held to the *lower* of that mean and the heat and rain
sub-scores, because a weighted mean would otherwise let a perfect rain score
rescue an hour nobody should be suited in -- and, from the other side, let a
mild sunny afternoon carry a wet one. Neither of those two can be averaged away:
whichever is worse is the number the hour gets.

Official DWD warnings deliberately do *not* affect the score. They are published
alongside it -- marked on the hourly bars over the hours they cover -- and left
for the reader to weigh, rather than folded into a single number.
"""

from __future__ import annotations

from math import sqrt
from typing import Dict, Iterable, List, Tuple

from app.config import settings
from app.i18n import t
from app.meteo import (
    apparent_solar_load,
    dewpoint as calc_dewpoint,
    relative_humidity,
    wetbulb,
)
from app.models import FSIResult, WeatherPoint
from app.weather_codes import is_thunderstorm

#: (minimum score, i18n key, colour). Checked from the top down.
#:
#: The single source of truth for the scale -- the service reuses it for the
#: hourly series and the API publishes it so the frontend never keeps its own
#: copy to drift out of step. Colours come from the festival palette, with the
#: two "go outside" greens kept clean and clearly apart.
BANDS: List[Tuple[float, str, str]] = [
    (8.5, "excellent", "#40ad3e"),  # clean green
    (7.0, "good", "#7cc243"),  # lighter green
    (5.0, "fair", "#ffd633"),  # Festival Amber
    (3.0, "poor", "#ff8a3d"),  # amber -> alarm transition
    (0.0, "bad", "#f13ca3"),  # Hot Pink
]


def band_color(score: float) -> str:
    for threshold, _key, color in BANDS:
        if score >= threshold:
            return color
    return BANDS[-1][2]


def band_scale(lang: str = "en") -> List[Dict]:
    """The scale as the API publishes it, for legends and client-side colouring."""
    return [
        {"min": threshold, "key": key, "label": t(lang, f"band.{key}"), "color": color}
        for threshold, key, color in BANDS
    ]


EASTER_EGGS: Dict[float, str] = {
    6.9: "nice",
    6.7: "ravi67",
}

#: The parts of the score, in the order they are reported, and the phrase book
#: entry each one's name comes from. The hourly series does not repeat the
#: names -- they are the same for all 120 hours -- so the summary publishes them
#: once from here, and a score carries the very same strings.
SUBSCORE_LABELS: Dict[str, str] = {
    "thermal_humidity": "sub.thermal",
    "precipitation": "sub.precipitation",
    "wind": "sub.wind",
    "stickiness": "sub.stickiness",
}


def subscore_labels(lang: str = "en") -> Dict[str, str]:
    """What each part of the score is called, for a client that draws them."""
    return {key: t(lang, phrase) for key, phrase in SUBSCORE_LABELS.items()}


def _band(score: float, lang: str) -> Tuple[str, str, str]:
    for threshold, key, color in BANDS:
        if score >= threshold:
            return t(lang, f"band.{key}"), color, t(lang, f"advice.{key}")
    key = BANDS[-1][1]
    return t(lang, f"band.{key}"), BANDS[-1][2], t(lang, f"advice.{key}")


def _thermal_score(
    point: WeatherPoint, wetbulb_c: float, solar_load: float, lang: str
) -> Tuple[float, str]:
    mapping = settings.fsi.thermal_mapping
    effective = wetbulb_c + solar_load

    if effective <= mapping["optimal_max"]:
        score = 10.0
    elif effective <= mapping["good_max"]:
        score = 8.5
    elif effective <= mapping["fair_max"]:
        score = 6.5
    elif effective <= mapping["poor_max"]:
        score = 4.5
    elif effective <= mapping["bad_max"]:
        score = 2.5
    else:
        score = 0.5

    reasons = [t(lang, "reason.wetbulb", value=f"{effective:.1f}")]
    if solar_load >= 0.5:
        reasons.append(t(lang, "reason.sunload", value=f"{solar_load:.1f}"))

    # Still air on a warm day removes the last bit of convective cooling.
    if point.wind_speed is not None and point.wind_speed < 1.0 and effective >= mapping["fair_max"]:
        score = max(0.0, score - 2.0)
        reasons.append(t(lang, "reason.nowind"))

    return score, f"{t(lang, 'reason.heat')}: " + ", ".join(reasons)


#: (rate in mm/h at the top of the step, score). The 0.0 entry is what a bare
#: chance of rain is worth when the forecast puts no amount against it.
RAIN_IF_IT_FALLS: List[Tuple[float, float]] = [
    (0.0, 6.0),
    (0.1, 5.0),
    (0.3, 3.5),
    (0.5, 2.5),
    (1.0, 1.5),
    (2.0, 0.5),
]


def _wet_hour_score(rate: float) -> float:
    """What an hour is worth *if* the rain falls, by rate in mm/h.

    Steep at the bottom on purpose. A suit soaks rain up and stays wet long
    after it stops, so there is no "light enough to ignore" here the way there
    is in ordinary clothes: 0.3 mm in an hour is already enough to end a walk.
    """
    for threshold, score in RAIN_IF_IT_FALLS:
        if rate <= threshold:
            return score
    return 0.0


def _precipitation_score(point: WeatherPoint, lang: str) -> Tuple[float, str]:
    rate = point.precipitation or 0.0
    rate_score = _wet_hour_score(rate)

    observed = point.precipitation_prob is None and rate > 0.0
    if observed:
        probability = 1.0
    else:
        # The square root of the chance, not the chance itself: being caught out
        # in a suit is far worse than a dry hour is good, so a 40 % chance has to
        # weigh more than 40 %. It is also a decision made an hour in advance.
        probability = sqrt(min(100.0, max(0.0, point.precipitation_prob or 0.0)) / 100.0)
    score = (1.0 - probability) * 10.0 + probability * rate_score
    if observed:
        reasons = [t(lang, "reason.rainmeasured", rate=f"{rate:.1f}")]
    else:
        reasons = [
            t(
                lang,
                "reason.rainrate",
                rate=f"{rate:.1f}",
                prob=f"{(point.precipitation_prob or 0):.0f}",
            )
        ]

    # Wet ground means muddy paws for hours after the rain stops.
    if (point.precipitation_24h or 0.0) >= 2.0:
        score -= 2.0
        reasons.append(t(lang, "reason.wetground"))

    if is_thunderstorm(point.weather_code):
        score = min(score, 2.0)
        reasons.append(t(lang, "reason.thunder_cap"))

    return max(0.0, min(10.0, score)), f"{t(lang, 'reason.rain')}: " + ", ".join(reasons)


def _wind_score(point: WeatherPoint, lang: str) -> Tuple[float, str]:
    wind = point.wind_speed
    if wind is None:
        return 8.0, f"{t(lang, 'reason.wind')}: {t(lang, 'reason.nodata_wind')}"

    if wind <= 0.5:
        score = 4.0
    elif wind <= 1.5:
        score = 7.0
    elif wind <= 3.3:
        score = 10.0
    elif wind <= 5.4:
        score = 9.5
    elif wind <= 7.9:
        score = 7.0
    elif wind <= 10.7:
        score = 5.5
    elif wind <= 13.8:
        score = 2.0
    elif wind <= 17.1:
        score = 0.5
    else:
        score = 0

    reasons = [t(lang, "reason.windspeed", value=f"{wind:.1f}")]
    gust = point.wind_gust or 0.0
    if gust >= 20.0:  # ~9 Bft
        score = min(score, 1.0)
        reasons.append(t(lang, "reason.gusts_severe"))
    elif gust >= 15.0:  # ~7 Bft
        score = min(score, 3.0)
        reasons.append(t(lang, "reason.gusts_strong"))

    return score, f"{t(lang, 'reason.wind')}: " + ", ".join(reasons)


def _stickiness_score(point: WeatherPoint, dew: float, lang: str) -> Tuple[float, str]:
    if dew <= 12.0:
        score = 10.0
    elif dew <= 16.0:
        score = 8.5
    elif dew <= 18.0:
        score = 6.5
    elif dew <= 20.0:
        score = 4.5
    elif dew <= 22.0:
        score = 2.5
    else:
        score = 0.5

    reasons = [t(lang, "reason.dewpoint", value=f"{dew:.1f}")]
    if point.wind_speed is not None and point.wind_speed < 1.0:
        score = max(0.0, score - 2.0)
        reasons.append(t(lang, "reason.nowind"))

    return score, f"{t(lang, 'reason.humidity')}: " + ", ".join(reasons)


def compute(point: WeatherPoint, lang: str = "en") -> FSIResult:
    """Score a single observation or forecast step."""
    temperature = point.temperature
    if temperature is None:
        return FSIResult(
            score=0.0,
            label=t(lang, "band.nodata"),
            color="#94a3b8",
            advice=t(lang, "advice.nodata"),
        )

    # DWD always gives us at least one of humidity / dew point; derive the other.
    humidity, dew = point.humidity, point.dewpoint
    if humidity is None:
        humidity = relative_humidity(temperature, dew) if dew is not None else 70.0
    if dew is None:
        dew = calc_dewpoint(temperature, humidity)

    wetbulb_c = wetbulb(temperature, humidity)
    solar_load = apparent_solar_load(point.cloud_cover, point.solar_radiation, point.time.hour)

    thermal, thermal_reason = _thermal_score(point, wetbulb_c, solar_load, lang)
    precipitation, precipitation_reason = _precipitation_score(point, lang)
    wind, wind_reason = _wind_score(point, lang)
    sticky, sticky_reason = _stickiness_score(point, dew, lang)

    weights = settings.fsi.weights
    weighted = (
        weights["thermal_humidity"] * thermal
        + weights["precipitation"] * precipitation
        + weights["wind"] * wind
        + weights["stickiness"] * sticky
    )
    # Heat and rain each decide an hour on their own, so neither is allowed to be
    # averaged away: the index is the worst of the mean and those two sub-scores.
    # One decimal, not half points: the score is reported to a tenth, which is
    # also what makes the 6.9 / 6.7 easter eggs below reachable at all.
    total = max(0.0, min(10.0, round(min(weighted, thermal, precipitation), 1)))

    effective_wetbulb = round(wetbulb_c + solar_load, 1)
    label, color, advice = _band(total, lang)

    easter_egg = EASTER_EGGS.get(total)
    if easter_egg == "nice":
        advice = f"{advice} {t(lang, 'easter.nice')}"

    labels = subscore_labels(lang)
    return FSIResult(
        score=round(total, 1),
        label=label,
        color=color,
        advice=advice,
        subscores={
            "thermal_humidity": {
                "score": round(thermal, 1),
                "weight": weights["thermal_humidity"],
                "label": labels["thermal_humidity"],
                "reason": thermal_reason,
            },
            "precipitation": {
                "score": round(precipitation, 1),
                "weight": weights["precipitation"],
                "label": labels["precipitation"],
                "reason": precipitation_reason,
            },
            "wind": {
                "score": round(wind, 1),
                "weight": weights["wind"],
                "label": labels["wind"],
                "reason": wind_reason,
            },
            "stickiness": {
                "score": round(sticky, 1),
                "weight": weights["stickiness"],
                "label": labels["stickiness"],
                "reason": sticky_reason,
            },
        },
        easter_egg=easter_egg,
        wetbulb=wetbulb_c,
        effective_wetbulb=effective_wetbulb,
        dewpoint=dew,
    )


def compute_series(points: Iterable[WeatherPoint], lang: str = "en") -> List[Dict]:
    """Score a whole forecast so the frontend can plot when to suit up."""
    series: List[Dict] = []
    for point in points:
        result = compute(point, lang)
        entry = {
            "time": point.time.isoformat(),
            "score": result.score,
            "label": result.label,
            "color": result.color,
            # Carried per hour so clicking a bar can show what is behind it.
            "wetbulb": result.wetbulb,
            "dewpoint": result.dewpoint,
            # The parts the hour's score is made of, so picking a bar can move
            # the breakdown onto that hour rather than leaving it on "now".
            # Trimmed to what changes hour by hour: the names are published once
            # as ``subscore_labels`` and the weights are the same throughout,
            # and repeated over 120 hours either would cost more than the
            # numbers themselves.
            "subscores": {
                key: {"score": sub["score"], "reason": sub["reason"]}
                for key, sub in result.subscores.items()
            },
        }
        series.append(entry)
    return series
