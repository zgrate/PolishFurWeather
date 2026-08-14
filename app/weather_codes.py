"""WMO ``ww`` significant-weather codes -> plain text and an icon.

MOSMIX and the POI reports both use the WMO code table. Only the codes DWD
actually emits for Germany are listed; anything else falls back by decade.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from app.i18n import normalise

# code -> (english, german, polish, day icon, night icon)
CODES: Dict[int, Tuple[str, str, str, str, str]] = {
    # The new-moon and waning-crescent glyphs render as near-black discs, which
    # read as a rendering fault rather than "clear night" -- use a crescent.
    0: ("Clear sky", "Klarer Himmel", "Bezchmurnie", "☀️", "\U0001f319"),
    1: ("Mainly clear", "Überwiegend klar", "Przeważnie bezchmurnie", "\U0001f324️", "\U0001f319"),
    2: ("Partly cloudy", "Teilweise bewölkt", "Częściowe zachmurzenie", "⛅", "☁️"),
    3: ("Overcast", "Bedeckt", "Zachmurzenie całkowite", "☁️", "☁️"),
    45: ("Fog", "Nebel", "Mgła", "\U0001f32b️", "\U0001f32b️"),
    48: ("Freezing fog", "Gefrierender Nebel", "Marznąca mgła", "\U0001f32b️", "\U0001f32b️"),
    51: ("Light drizzle", "Leichter Nieselregen", "Lekka mżawka", "\U0001f327️", "\U0001f327️"),
    53: ("Drizzle", "Nieselregen", "Mżawka", "\U0001f327️", "\U0001f327️"),
    55: ("Heavy drizzle", "Starker Nieselregen", "Silna mżawka", "\U0001f327️", "\U0001f327️"),
    56: ("Freezing drizzle", "Gefrierender Nieselregen", "Marznąca mżawka", "\U0001f9ca", "\U0001f9ca"),
    57: (
        "Heavy freezing drizzle",
        "Starker gefrierender Nieselregen",
        "Silna marznąca mżawka",
        "\U0001f9ca",
        "\U0001f9ca",
    ),
    61: ("Light rain", "Leichter Regen", "Lekki deszcz", "\U0001f326️", "\U0001f327️"),
    63: ("Rain", "Regen", "Deszcz", "\U0001f327️", "\U0001f327️"),
    65: ("Heavy rain", "Starkregen", "Ulewny deszcz", "\U0001f327️", "\U0001f327️"),
    66: ("Freezing rain", "Gefrierender Regen", "Marznący deszcz", "\U0001f9ca", "\U0001f9ca"),
    67: (
        "Heavy freezing rain",
        "Starker gefrierender Regen",
        "Silny marznący deszcz",
        "\U0001f9ca",
        "\U0001f9ca",
    ),
    71: ("Light snow", "Leichter Schneefall", "Lekki śnieg", "\U0001f328️", "\U0001f328️"),
    73: ("Snow", "Schneefall", "Śnieg", "\U0001f328️", "\U0001f328️"),
    75: ("Heavy snow", "Starker Schneefall", "Silny śnieg", "❄️", "❄️"),
    77: ("Snow grains", "Schneegriesel", "Ziarna śniegu", "❄️", "❄️"),
    80: ("Light rain showers", "Leichte Regenschauer", "Lekkie przelotne opady deszczu", "\U0001f326️", "\U0001f327️"),
    81: ("Rain showers", "Regenschauer", "Przelotne opady deszczu", "\U0001f327️", "\U0001f327️"),
    82: (
        "Heavy rain showers",
        "Starke Regenschauer",
        "Silne przelotne opady deszczu",
        "\U0001f327️",
        "\U0001f327️",
    ),
    85: ("Snow showers", "Schneeschauer", "Przelotne opady śniegu", "\U0001f328️", "\U0001f328️"),
    86: ("Heavy snow showers", "Starke Schneeschauer", "Silne przelotne opady śniegu", "❄️", "❄️"),
    95: ("Thunderstorm", "Gewitter", "Burza", "⛈️", "⛈️"),
    96: ("Thunderstorm with hail", "Gewitter mit Hagel", "Burza z gradem", "⛈️", "⛈️"),
    99: (
        "Severe thunderstorm with hail",
        "Schweres Gewitter mit Hagel",
        "Silna burza z gradem",
        "⛈️",
        "⛈️",
    ),
}

_FALLBACK_BY_DECADE = {
    4: ("Fog", "Nebel", "Mgła", "\U0001f32b️"),
    5: ("Drizzle", "Nieselregen", "Mżawka", "\U0001f327️"),
    6: ("Rain", "Regen", "Deszcz", "\U0001f327️"),
    7: ("Snow", "Schneefall", "Śnieg", "\U0001f328️"),
    8: ("Showers", "Schauer", "Przelotne opady", "\U0001f327️"),
    9: ("Thunderstorm", "Gewitter", "Burza", "⛈️"),
}


def describe(code: Optional[int], is_day: bool = True, lang: str = "en") -> Dict[str, Optional[str]]:
    """Return ``{"code", "text", "icon"}`` for a ww code."""
    if code is None:
        return {"code": None, "text": None, "icon": None}

    code = int(code)
    resolved = normalise(lang)

    if code in CODES:
        english, deutsch, polski, day_icon, night_icon = CODES[code]
        text = {"de": deutsch, "pl": polski}.get(resolved, english)
        return {
            "code": code,
            "text": text,
            "icon": day_icon if is_day else night_icon,
        }

    english, deutsch, polski, icon = _FALLBACK_BY_DECADE.get(code // 10, ("Cloudy", "Bewölkt", "Zachmurzenie", "☁️"))
    text = {"de": deutsch, "pl": polski}.get(resolved, english)
    return {"code": code, "text": text, "icon": icon}


def is_thunderstorm(code: Optional[int]) -> bool:
    return code is not None and 95 <= int(code) <= 99
