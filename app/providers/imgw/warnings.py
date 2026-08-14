"""Official meteorological warnings from IMGW, filtered to the venue's TERYT.

Field mapping, confirmed against a live response from
``https://danepubliczne.imgw.pl/api/data/warningsmeteo``::

    nazwa_zdarzenia      -> event (Polish, e.g. "Upał")
    stopien               -> level / severity (see LEVELS below)
    tresc                 -> description (Polish, official wording)
    komentarz              -> instruction (closest field IMGW offers -- usually a
                              short editorial remark like "may be updated", not an
                              actionable instruction the way DWD's field is; kept
                              in the same slot so the API shape does not change)
    obowiazuje_od/do       -> start / end
    teryt                 -> the powiat codes the warning applies to

Timestamps (``obowiazuje_od``, ``obowiazuje_do``, ``opublikowano``) are
**Europe/Warsaw local time, not UTC** -- confirmed by comparing a warning's
``opublikowano`` field against the UTC timestamp embedded in its own ``id``
(``Sk20260813094307...`` = 09:43:07 UTC, ``opublikowano`` = "11:43:00" the
same day, a two-hour offset matching CEST). Parsed with ``zoneinfo`` so the
DST transition is handled correctly rather than a fixed offset.

IMGW's degrees run 1-3 (there is no fourth, DWD-style "extreme" tier observed
in the live feed or documented by IMGW); mapped onto this app's existing
minor/moderate/severe/extreme scale leaves "extreme" unused for now rather
than inventing a warning IMGW does not issue.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.config import settings
from app.models import Warning
from app.providers.http import cache, fetch_json

logger = logging.getLogger(__name__)

WARNINGS_URL = "https://danepubliczne.imgw.pl/api/data/warningsmeteo"

#: TTLCache key the feed lands under -- one feed for every venue, so this
#: needs no parameters. Exposed for /api/health's cache-age reporting.
CACHE_KEY = "imgw:warnings"

WARSAW = ZoneInfo("Europe/Warsaw")

#: IMGW stopien (degree) -> (severity, colour). Colours follow the same scale
#: used for DWD's warnings so the frontend needs no per-source styling.
LEVELS = {
    1: ("minor", "#ffeb3b"),
    2: ("moderate", "#fb8c00"),
    3: ("severe", "#e53935"),
}

#: Polish event name -> our coarse kind, used by the FSI thunderstorm cap and
#: the warning icons. Populated from IMGW's published event list
#: (https://danepubliczne.imgw.pl/pl/dane/warningsmeteo); anything not listed
#: here falls back to "other" rather than being guessed.
KINDS = {
    "Upał": "heat",
    "Burze": "thunderstorm",
    "Burze z gradem": "thunderstorm",
    "Silny wiatr": "wind",
    "Intensywne opady deszczu": "rain",
    "Intensywne opady śniegu": "snow",
    "Oblodzenie": "ice",
    "Mróz": "frost",
    "Przymrozki": "ground_frost",
    "Gęsta mgła": "fog",
    "Roztopy": "thaw",
    "Śnieg z deszczem": "ice_rain",
}


def _parse_local(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=WARSAW)
    except ValueError:
        logger.warning("Unparseable IMGW warning timestamp: %r", value)
        return None


def _kind_of(event: str) -> str:
    return KINDS.get(event.strip(), "other")


def _normalise(entry: Dict[str, Any]) -> Warning:
    event = (entry.get("nazwa_zdarzenia") or "").strip()
    try:
        level = int(entry.get("stopien") or 1)
    except (TypeError, ValueError):
        level = 1
    severity, color = LEVELS.get(level, ("minor", "#ffeb3b"))
    kind = _kind_of(event)

    return Warning(
        event=event or "Ostrzeżenie",
        event_en=event or "Warning",
        headline=event,
        description=" ".join((entry.get("tresc") or "").split()),
        instruction=" ".join((entry.get("komentarz") or "").split()),
        severity=severity,
        level=level,
        kind=kind,
        region=(entry.get("biuro") or "").strip(),
        start=_parse_local(entry.get("obowiazuje_od")),
        end=_parse_local(entry.get("obowiazuje_do")),
        color=color,
    )


def _matches(entry: Dict[str, Any], teryt: List[str]) -> bool:
    codes = entry.get("teryt") or []
    return any(code in codes for code in teryt)


def fetch_warnings(teryt: Optional[List[str]] = None, lang: str = "en") -> List[Warning]:
    """Warnings whose TERYT list intersects the venue's configured codes.

    ``lang`` is accepted for signature parity with the rest of the provider
    layer (DWD's equivalent renders labels per-language); IMGW's payload has
    no per-language variant, so the Polish text is returned as-is regardless.
    """
    teryt = teryt if teryt is not None else settings.imgw.teryt
    if not teryt:
        logger.warning("imgw.teryt is not configured -- no warnings can be matched to the venue")
        return []

    def _fetch() -> List[Dict[str, Any]]:
        data = fetch_json(WARNINGS_URL)
        return data if isinstance(data, list) else []

    entries = cache.get_or_fetch(CACHE_KEY, settings.cache.warnings, _fetch)
    matched = [_normalise(e) for e in entries if _matches(e, teryt)]

    order = {"extreme": 0, "severe": 1, "moderate": 2, "minor": 3}
    matched.sort(key=lambda w: (order.get(w.severity, 9), w.start or datetime.max.replace(tzinfo=WARSAW)))

    logger.info("Warnings for TERYT %s: %d active", ",".join(teryt), len(matched))
    return matched
