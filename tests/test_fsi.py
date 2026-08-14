"""Tests for the meteorological helpers and the Fursuitability Index."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from app import fsi
from app.config import settings
from app.meteo import beaufort, dewpoint, relative_humidity, wetbulb, wind_direction_name
from app.models import WeatherPoint


def point(hour: int = 3, **kwargs) -> WeatherPoint:
    """A forecast step at 03:00 UTC, i.e. no solar load unless asked for."""
    base = dict(
        temperature=18.0,
        humidity=60.0,
        wind_speed=2.0,
        wind_gust=4.0,
        precipitation=0.0,
        precipitation_prob=0.0,
        cloud_cover=50.0,
    )
    base.update(kwargs)
    return WeatherPoint(time=datetime(2026, 9, 4, hour, tzinfo=timezone.utc), **base)


# ------------------------------------------------------------------- meteo


def test_wetbulb_is_below_dry_bulb_and_tracks_humidity():
    assert wetbulb(25.0, 50.0) < 25.0
    assert wetbulb(25.0, 90.0) > wetbulb(25.0, 40.0)


def test_wetbulb_equals_dry_bulb_at_saturation():
    assert wetbulb(20.0, 100.0) == pytest.approx(20.0, abs=0.5)


def test_dewpoint_and_humidity_round_trip():
    assert relative_humidity(20.0, dewpoint(20.0, 65.0)) == pytest.approx(65.0, abs=1.0)


def test_beaufort_and_direction():
    assert beaufort(0.1) == 0
    assert beaufort(10.0) == 5
    assert beaufort(40.0) == 12
    assert beaufort(None) is None
    assert wind_direction_name(0) == "N"
    assert wind_direction_name(180) == "S"
    assert wind_direction_name(280) == "W"


# --------------------------------------------------------------------- FSI


def test_cool_dry_calm_night_scores_excellent():
    result = fsi.compute(point(temperature=14.0, humidity=55.0, wind_speed=2.0))
    assert result.score >= 8.5
    assert result.label == "Excellent"


def test_hot_and_humid_scores_badly():
    result = fsi.compute(point(hour=12, temperature=33.0, humidity=75.0, wind_speed=0.5))
    assert result.score <= 1.0
    assert result.label == "Bad"


def test_dangerous_heat_is_not_offset_by_dry_calm_weather():
    """A weighted mean would let a perfect 10/10 rain score rescue a lethal hour."""
    lethal = point(hour=12, temperature=33.0, humidity=75.0, wind_speed=0.5)
    result = fsi.compute(lethal)

    assert result.effective_wetbulb >= 27.0
    assert result.subscores["precipitation"]["score"] == 10.0  # dry and sunny
    assert result.score <= 1.0
    # The heat sub-score is the ceiling, so the hour scores exactly what it says.
    assert result.score == result.subscores["thermal_humidity"]["score"]


def test_heat_holds_the_score_down_wherever_it_is_dangerous():
    """Both hours are "extreme caution" or worse on the heat index."""
    for kwargs in (
        dict(temperature=29.0, humidity=65.0),
        dict(temperature=34.0, humidity=80.0),
    ):
        result = fsi.compute(point(hour=12, wind_speed=2.0, **kwargs))
        assert result.score == result.subscores["thermal_humidity"]["score"] <= 2.5
        assert result.label == "Bad"


def test_a_warm_sunny_afternoon_is_uncomfortable_not_dangerous():
    """24 °C in full sun is "extreme caution" on the heat index, not heat stroke.

    The bands used to bottom out here, which put an ordinary warm convention
    afternoon in the same place as a genuinely unsafe one.
    """
    result = fsi.compute(point(hour=12, temperature=24.0, humidity=60.0, cloud_cover=0.0))
    assert 4.0 <= result.subscores["thermal_humidity"]["score"] <= 6.5


def test_mild_weather_is_not_held_down_by_the_heat_score():
    result = fsi.compute(point(temperature=17.0, humidity=60.0))
    assert result.subscores["thermal_humidity"]["score"] >= 8.5
    assert result.score >= 7.0


def test_sun_load_lowers_the_score_versus_an_overcast_hour():
    sunny = fsi.compute(point(hour=12, temperature=24.0, cloud_cover=0.0, solar_radiation=800.0))
    cloudy = fsi.compute(point(hour=12, temperature=24.0, cloud_cover=100.0, solar_radiation=0.0))
    assert sunny.score < cloudy.score
    assert sunny.effective_wetbulb > cloudy.effective_wetbulb


def test_heavy_rain_dominates_the_score():
    dry = fsi.compute(point(precipitation=0.0, precipitation_prob=0.0))
    wet = fsi.compute(point(precipitation=5.0, precipitation_prob=100.0))
    assert wet.score < dry.score
    assert wet.subscores["precipitation"]["score"] < 2.0


def test_light_rain_pulls_an_otherwise_perfect_hour_down_to_fair():
    """A cool, calm 0.4 mm hour used to score 9.5 and read "Excellent".

    Every other part of that hour is a 10, and rain is 30 % of the mean, so the
    weighted total cannot describe it: 0.4 mm at a 39 % chance is a suit you
    carry home wet.
    """
    drizzle = fsi.compute(
        point(
            temperature=13.6,
            humidity=91.0,
            wind_speed=3.0,
            precipitation=0.4,
            precipitation_prob=39.0,
        )
    )
    assert drizzle.label == "Fair"
    # The rain sub-score, not the mean, is what said so.
    assert drizzle.score == drizzle.subscores["precipitation"]["score"]


def test_the_rain_ceiling_scales_with_how_wet_the_hour_is():
    mild = dict(temperature=14.0, humidity=60.0, wind_speed=2.0)
    dry = fsi.compute(point(**mild))
    damp = fsi.compute(point(precipitation=0.3, precipitation_prob=40.0, **mild))
    wet = fsi.compute(point(precipitation=0.5, precipitation_prob=80.0, **mild))
    soaked = fsi.compute(point(precipitation=5.0, precipitation_prob=100.0, **mild))

    assert soaked.score < wet.score < damp.score < dry.score
    assert [dry.label, damp.label, wet.label, soaked.label] == ["Excellent", "Fair", "Poor", "Bad"]


def test_a_chance_of_rain_costs_something_on_its_own():
    """The forecast often carries a real chance against 0.0 mm for the hour."""
    mild = dict(temperature=14.0, humidity=60.0, wind_speed=2.0, precipitation=0.0)
    certain_dry = fsi.compute(point(precipitation_prob=0.0, **mild))
    maybe = fsi.compute(point(precipitation_prob=50.0, **mild))

    assert certain_dry.subscores["precipitation"]["score"] == 10.0
    assert maybe.score < certain_dry.score


def test_the_chance_of_rain_is_weighed_risk_averse():
    """Half a chance of rain has to cost more than half the malus.

    Getting caught out in a suit is worse than a dry hour is good, so the blend
    uses the square root of the probability rather than the probability itself.
    """
    wet = dict(temperature=14.0, humidity=60.0, wind_speed=2.0, precipitation=1.0)
    certain = fsi.compute(point(precipitation_prob=100.0, **wet))
    half = fsi.compute(point(precipitation_prob=50.0, **wet))
    never = fsi.compute(point(precipitation_prob=0.0, **wet))

    midpoint = (
        never.subscores["precipitation"]["score"] + certain.subscores["precipitation"]["score"]
    ) / 2
    assert half.subscores["precipitation"]["score"] < midpoint


def test_a_trace_of_rain_at_a_low_chance_still_leaves_a_good_hour():
    """The malus is steep, not a cliff at the first drop the model shows."""
    result = fsi.compute(
        point(
            temperature=14.0,
            humidity=60.0,
            wind_speed=2.0,
            precipitation=0.1,
            precipitation_prob=20.0,
        )
    )
    assert result.score >= 7.0


def test_wind_is_u_shaped():
    still = fsi.compute(point(wind_speed=0.2)).subscores["wind"]["score"]
    ideal = fsi.compute(point(wind_speed=2.0)).subscores["wind"]["score"]
    gale = fsi.compute(point(wind_speed=15.0)).subscores["wind"]["score"]
    assert ideal > still
    assert ideal > gale


def test_severe_gusts_cap_the_wind_subscore():
    result = fsi.compute(point(wind_speed=2.0, wind_gust=22.0))
    assert result.subscores["wind"]["score"] <= 1.0


def test_thunderstorm_weather_code_caps_precipitation():
    result = fsi.compute(point(weather_code=95))
    assert result.subscores["precipitation"]["score"] <= 2.0


def test_missing_temperature_reports_no_data():
    result = fsi.compute(WeatherPoint(time=datetime(2026, 9, 4, tzinfo=timezone.utc)))
    assert result.label == "No data"
    assert result.score == 0.0


def test_humidity_is_derived_when_only_dewpoint_is_known():
    result = fsi.compute(
        WeatherPoint(
            time=datetime(2026, 9, 4, 3, tzinfo=timezone.utc), temperature=20.0, dewpoint=10.0
        )
    )
    assert result.label != "No data"
    assert result.dewpoint == 10.0


# ------------------------------------------------------------ hourly series


def test_compute_series_scores_each_hour_on_its_own_weather():
    start = datetime(2026, 7, 29, 9, tzinfo=timezone.utc)
    points = [
        WeatherPoint(
            time=start + timedelta(hours=offset),
            temperature=temperature,
            humidity=55.0,
            wind_speed=2.0,
            precipitation=0.0,
            precipitation_prob=0.0,
            cloud_cover=100.0,
        )
        for offset, temperature in enumerate((15.0, 15.0, 34.0))
    ]

    series = fsi.compute_series(points)
    assert [entry["time"] for entry in series] == [p.time.isoformat() for p in points]
    assert series[0]["score"] == series[1]["score"]
    assert series[2]["score"] < series[0]["score"]  # the hot hour, and only it
    assert series[0]["color"] == fsi.band_color(series[0]["score"])


def test_every_hour_carries_the_parts_its_score_is_made_of():
    """Clicking a bar moves the breakdown onto that hour, so it needs one.

    The same numbers and the same wording as the score itself: two ways of
    saying what an hour is like would eventually disagree about one.
    """
    hot = point(hour=13, temperature=31.0, humidity=70.0)
    entry = fsi.compute_series([hot])[0]
    result = fsi.compute(hot)

    assert set(entry["subscores"]) == set(result.subscores)
    for key, part in entry["subscores"].items():
        assert part["score"] == result.subscores[key]["score"]
        assert part["reason"] == result.subscores[key]["reason"]
        # The names are the same for all 120 hours, so the series leaves them to
        # subscore_labels() rather than repeating them per hour.
        assert "label" not in part
        assert "weight" not in part


def test_a_held_down_hour_scores_the_part_that_held_it():
    """No note needed: the hour's score is one of the bars the series carries."""
    hot = fsi.compute_series([point(hour=13, temperature=38.0, humidity=80.0)])[0]
    assert hot["score"] == hot["subscores"]["thermal_humidity"]["score"]


def test_subscore_labels_are_published_once_and_translated():
    labels = fsi.subscore_labels("en")
    assert set(labels) == set(fsi.compute(point()).subscores)
    assert labels["thermal_humidity"] == "Temperature"
    assert fsi.subscore_labels("de")["precipitation"] == "Regen"
    # The score's own names come from the same place, so a client can put the
    # two together for an hour off the series.
    assert fsi.compute(point()).subscores["wind"]["label"] == labels["wind"]


def test_warnings_are_not_an_input_to_the_index():
    """Warnings are reported next to the score, never folded into it.

    Removing the caps was a deliberate call: a warning covers a whole region for
    hours, so it was flattening the very hour-by-hour detail the bars exist to
    show. This guards the signature so the caps cannot quietly return.
    """
    assert "warnings" not in inspect.signature(fsi.compute).parameters
    assert "warnings" not in inspect.signature(fsi.compute_series).parameters
    assert not hasattr(settings.fsi, "warning_caps")


# ------------------------------------------------------- band scale


def test_band_colour_matches_the_scale():
    assert fsi.band_color(10.0) == "#40ad3e"
    assert fsi.band_color(8.5) == "#40ad3e"
    assert fsi.band_color(7.0) == "#7cc243"
    assert fsi.band_color(5.0) == "#ffd633"
    assert fsi.band_color(3.0) == "#ff8a3d"
    assert fsi.band_color(0.0) == "#f13ca3"


def test_band_scale_is_ordered_and_complete():
    scale = fsi.band_scale("en")
    assert [b["min"] for b in scale] == sorted((b["min"] for b in scale), reverse=True)
    assert scale[-1]["min"] == 0.0  # every score falls into a band
    assert all(b["color"].startswith("#") and b["label"] for b in scale)


def test_compute_and_band_color_agree():
    """The panel tint and the bar colour must never disagree for one score."""
    result = fsi.compute(point(temperature=14.0, humidity=55.0))
    assert result.color == fsi.band_color(result.score)


# ------------------------------------------------------- easter eggs


def _point_scoring(target: float) -> WeatherPoint:
    """Find inputs that land the index exactly on `target`.

    With no rain the precipitation sub-score is pinned at 10 and the total is
    coarse, so the rain rate and probability are what reach the odd tenths.
    """
    for temperature in (t / 10 for t in range(120, 260, 5)):
        for humidity in range(35, 96, 5):
            for rate in (0.0, 0.3, 0.8, 1.5, 3.0):
                for prob in range(0, 101):
                    candidate = point(
                        temperature=temperature,
                        humidity=humidity,
                        precipitation=rate,
                        precipitation_prob=prob,
                    )
                    if fsi.compute(candidate).score == target:
                        return candidate
    raise AssertionError(f"no inputs produce a score of {target}")


def test_the_score_is_reported_to_a_tenth():
    """Half-point rounding would make 6.9 and 6.7 unreachable."""
    assert fsi.compute(_point_scoring(6.9)).score == 6.9


def test_six_point_nine_is_nice():
    result = fsi.compute(_point_scoring(6.9))
    assert result.easter_egg == "nice"
    assert result.advice.endswith("Nice.")


def test_six_point_seven_asks_for_the_video():
    result = fsi.compute(_point_scoring(6.7))
    assert result.easter_egg == "ravi67"
    # The video is the whole joke; the advice stays untouched.
    assert "Nice." not in result.advice


def test_ordinary_scores_have_no_easter_egg():
    result = fsi.compute(point(temperature=14.0, humidity=55.0))
    assert result.easter_egg is None
    assert "Nice." not in result.advice


def test_the_eggs_are_purely_cosmetic():
    """They must not move the score, its band or its colour."""
    for target, egg in ((6.9, "nice"), (6.7, "ravi67")):
        result = fsi.compute(_point_scoring(target))
        assert result.easter_egg == egg
        assert result.score == target
        assert result.color == fsi.band_color(target)
        # 6.9 and 6.7 both sit in the "Fair" band and must stay there.
        assert result.label == fsi.band_scale("en")[2]["label"]
