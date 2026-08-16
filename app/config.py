"""Application configuration.

Values come from ``config.json`` (bundled, overridable via ``EFW_CONFIG``) and
may be overridden individually by environment variables so the container can be
retuned without rebuilding the image.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


@dataclass
class Event:
    """The convention edition this instance is running for.

    Both names are "EF30" on purpose. The full festival title was three lines of
    tracked caps on a phone and read as a front door this site is not; everywhere
    the name appears -- the page, the API title, ``meta.event`` -- the short form
    is the one that belongs there. ``short_name`` stays a separate field for the
    next fork, which may well want the long one back.
    """

    name: str = "EF30"
    short_name: str = "EF30"


@dataclass
class Location:
    name: str = "Hamburg"
    latitude: float = 53.561337
    longitude: float = 9.986310
    timezone: str = "Europe/Berlin"
    elevation: float = 16.0


@dataclass
class IMGWSettings:
    """The Polish venue this instance watches.

    Left blank on purpose: the DWD fork could default to Hamburg because DWD's
    own station table has an entry that happens to be right for it. IMGW's
    station/TERYT ids do not, and guessing one would silently point the whole
    site at the wrong town. An operator must fill these in (``config.json``'s
    ``imgw`` block or the ``EFW_*`` env vars below) before the Polish sources
    return anything.
    """

    #: IMGW SYNOP station id, e.g. "12500" (Kraków-Balice). See
    #: https://danepubliczne.imgw.pl/api/data/synop for the live list.
    station_id: str = ""
    station_name: str = ""
    #: 4-digit powiat TERYT codes the venue sits in, for warning filtering.
    #: See https://danepubliczne.imgw.pl/pl/dane/warningsmeteo -- each warning
    #: carries the TERYT codes it applies to, and this list is intersected
    #: against them.
    teryt: List[str] = field(default_factory=list)
    #: IMGW product-catalogue id for the COSMO 2.8km surface dataset. Not the
    #: transient file URL -- resolved fresh via
    #: https://danepubliczne.imgw.pl/api/data/product each run.
    cosmo_product_prefix: str = "COSMO_HVD"
    #: Radar composite product id (POLCOMP SRI, precipitation rate).
    radar_product: str = "COMPO_SRI.comp.sri"
    #: How stale a POLRAD frame may be before it is treated as unavailable.
    radar_max_age_minutes: int = 15


@dataclass
class OpenMeteoSettings:
    """Fallback forecast (beyond the COSMO horizon) and pollen (CAMS)."""

    enabled: bool = True
    #: Extend the forecast with Open-Meteo once COSMO's own horizon ends.
    forecast_fallback: bool = True
    pollen_enabled: bool = True


@dataclass
class FSIConfig:
    weights: Dict[str, float] = field(
        default_factory=lambda: {
            "thermal_humidity": 0.50,
            "precipitation": 0.30,
            "wind": 0.12,
            "stickiness": 0.08,
        }
    )
    #: Effective wet-bulb temperature (deg C) at the upper edge of each band, set
    #: on the heat-index steps: 18.5 is where "caution" begins (heat index 27 °C),
    #: 24.0 "extreme caution" (32 °C) and 27.0 "danger" (40 °C). The fursuit is in
    #: the solar load added before this, not in tighter thresholds here.
    thermal_mapping: Dict[str, float] = field(
        default_factory=lambda: {
            "optimal_max": 16.0,
            "good_max": 18.5,
            "fair_max": 21.0,
            "poor_max": 24.0,
            "bad_max": 27.0,
        }
    )
    thresholds: Dict[str, float] = field(
        default_factory=lambda: {"excellent": 8.5, "good": 7.0, "fair": 5.0, "poor": 3.0}
    )
    #: The heat and rain sub-scores are also ceilings on the total -- see
    #: ``app/fsi.py`` -- so their own mappings above are the only knobs for it.


@dataclass
class APISettings:
    """Public API behaviour."""

    #: Requests per minute per client for /api/*. The data is cached, so this
    #: only exists to stop one consumer crowding out the site and the board.
    rate_limit_per_minute: int = 100
    #: Set false to serve only the pages and the internal endpoints.
    public_api_enabled: bool = True


@dataclass
class CapacitySettings:
    """When to tell visitors the site is busy.

    One container serves the whole convention, so "too many people at once" is a
    state it can reach whatever it is hosted on. The notice says a crowd is here,
    not what the site runs on. The counting is cookie-free; see ``app/presence.py``.

    The defaults are a starting guess for a single container: watch
    ``/api/load`` during a busy hour and move them to where the site actually
    starts feeling slow, which depends far more on the uplink than on the CPU.
    """

    #: Set false to count nothing and show nothing.
    enabled: bool = True
    #: How long after their last request someone still counts as "here". Must
    #: comfortably exceed the frontend's five-minute refresh, or an open tab
    #: would flicker in and out of the count between polls.
    active_window_seconds: int = 360
    #: Visitors at which the page mentions it is busy...
    busy_at: int = 40
    #: ...and at which it warns that things will be slow.
    crowded_at: int = 80


@dataclass
class PollenSettings:
    """The CAMS-backed pollen layer (Open-Meteo).

    CAMS publishes concentrations in grains/m3, not severity levels, so the
    bands the map and its key are drawn in are this site's own. They are here
    rather than in the code so an instance can retune them without a rebuild
    -- and so it is obvious that they are a judgement call, not something
    official.
    """

    #: Set false to drop the allergen picker and the pollen map entirely.
    enabled: bool = True
    #: species -> [moderate, high, very high] lower bounds in grains/m3.
    #: Anything absent falls back to app/providers/open_meteo/pollen.py's
    #: defaults. Note CAMS has no hazel product at all -- see that module's
    #: docstring -- so a "hazel" entry here is unused, not wrong.
    thresholds: Dict[str, List[float]] = field(default_factory=dict)


@dataclass
class NetworkSettings:
    """Outbound IP family pin and the interface uvicorn binds to.

    See ``app/net.py`` for what the pin actually does and why a host would
    ever want one; ``bind_host`` just documents/exposes the value the process
    should be started with (``Dockerfile``'s ``CMD`` still passes ``--host``
    directly, so this is informational -- e.g. for ``/api/health`` -- rather
    than something that reaches back and changes how uvicorn was already
    launched).
    """

    ip_family: str = "auto"
    bind_host: str = "0.0.0.0"


@dataclass
class CacheTTL:
    """Seconds to keep each upstream response. IMGW SYNOP publishes hourly,
    Open-Meteo's forecast about once an hour, IMGW warnings every few minutes."""

    observations: int = 600
    forecast: int = 1800
    warnings: int = 180


@dataclass
class LegalSettings:
    """Where the privacy notice actually lives.

    The site runs on Eurofurence e.V. infrastructure by default, so their
    notice is the one that governs it -- link it rather than keeping a second,
    drifting copy here. A fork not hosted there must point this at its own.
    """

    privacy_url: str = "https://help.eurofurence.org/legal/privacy"
    #: Feedback form -- same reasoning as privacy_url, a fork wants its own.
    feedback_url: str = "https://forms.gle/LMTQHvd5frtd7QJH6"


@dataclass
class NotificationsSettings:
    """The convention-announcements channel plugged into the footer disclaimer.

    A fork watching a different convention (or a community running its own
    channel alongside the official one) needs its own link, label and wording
    here -- same reasoning as ``LegalSettings.privacy_url``.
    """

    telegram_url: str = "https://t.me/efnotifications"
    channel_name: str = "Eurofurence Notifications"
    disclaimer: Dict[str, str] = field(
        default_factory=lambda: {
            "en": "Official Eurofurence site. Always follow the official warnings, "
            "and for convention announcements the official Telegram channel:",
            "de": "Offizielle Eurofurence-Seite. Befolge stets die amtlichen "
            "Warnungen, und für Ansagen der Convention den offiziellen "
            "Telegram-Kanal:",
            "pl": "Oficjalna strona Eurofurence. Zawsze stosuj się do oficjalnych "
            "ostrzeżeń, a po ogłoszenia dotyczące konwentu sprawdzaj oficjalny "
            "kanał Telegram:",
        }
    )


@dataclass
class Settings:
    event: Event = field(default_factory=Event)
    location: Location = field(default_factory=Location)
    imgw: IMGWSettings = field(default_factory=IMGWSettings)
    open_meteo: OpenMeteoSettings = field(default_factory=OpenMeteoSettings)
    network: NetworkSettings = field(default_factory=NetworkSettings)
    fsi: FSIConfig = field(default_factory=FSIConfig)
    api: APISettings = field(default_factory=APISettings)
    capacity: CapacitySettings = field(default_factory=CapacitySettings)
    pollen: PollenSettings = field(default_factory=PollenSettings)
    cache: CacheTTL = field(default_factory=CacheTTL)
    legal: LegalSettings = field(default_factory=LegalSettings)
    notifications: NotificationsSettings = field(default_factory=NotificationsSettings)
    forecast_hours: int = 120
    request_timeout: int = 20
    user_agent: str = "EurofurenceWeather/2.0 (+https://github.com/laffiesphere/EurofurenceWeather)"
    #: The site's own brand, distinct from ``event.name`` -- this is "who runs
    #: the page", the event is "which convention it's watching right now".
    site_name: str = "Eurofurence Weather"


def _merge(target: Any, data: Dict[str, Any]) -> None:
    """Shallow-merge a dict of overrides onto a dataclass instance."""
    for key, value in data.items():
        if not hasattr(target, key):
            logger.warning("Ignoring unknown config key %r", key)
            continue
        current = getattr(target, key)
        if isinstance(current, dict) and isinstance(value, dict):
            current.update(value)
        else:
            setattr(target, key, value)


def load_settings(path: str | os.PathLike[str] | None = None) -> Settings:
    settings = Settings()

    config_path = Path(path or os.environ.get("EFW_CONFIG") or DEFAULT_CONFIG_PATH)
    if config_path.exists():
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Could not read config %s: %s -- using defaults", config_path, exc)
            raw = {}
        _merge(settings.event, raw.get("event", {}))
        _merge(settings.location, raw.get("location", {}))
        _merge(settings.imgw, raw.get("imgw", {}))
        _merge(settings.open_meteo, raw.get("open_meteo", {}))
        _merge(settings.network, raw.get("network", {}))
        _merge(settings.fsi, raw.get("fsi", {}))
        _merge(settings.api, raw.get("api", {}))
        _merge(settings.capacity, raw.get("capacity", {}))
        _merge(settings.pollen, raw.get("pollen", {}))
        _merge(settings.cache, raw.get("cache", {}))
        _merge(settings.legal, raw.get("legal", {}))
        _merge(settings.notifications, raw.get("notifications", {}))
        for key in ("forecast_hours", "request_timeout", "site_name"):
            if key in raw:
                setattr(settings, key, raw[key])
        logger.info("Loaded config from %s", config_path)
    else:
        logger.warning("Config %s not found -- using built-in defaults", config_path)

    # Environment overrides (handy for docker-compose).
    if imgw_station := os.environ.get("EFW_IMGW_STATION_ID"):
        settings.imgw.station_id = imgw_station
    if imgw_station_name := os.environ.get("EFW_IMGW_STATION_NAME"):
        settings.imgw.station_name = imgw_station_name
    if teryt := os.environ.get("EFW_IMGW_TERYT"):
        settings.imgw.teryt = [c.strip() for c in teryt.split(",") if c.strip()]
    if ip_family := os.environ.get("EFW_IP_FAMILY"):
        settings.network.ip_family = ip_family
    if bind_host := os.environ.get("EFW_BIND_HOST"):
        settings.network.bind_host = bind_host
    if name := os.environ.get("EFW_LOCATION_NAME"):
        settings.location.name = name
    if event := os.environ.get("EFW_EVENT_NAME"):
        settings.event.name = event
    if site_name := os.environ.get("EFW_SITE_NAME"):
        settings.site_name = site_name
    if hours := os.environ.get("EFW_FORECAST_HOURS"):
        settings.forecast_hours = int(hours)
    if limit := os.environ.get("EFW_RATE_LIMIT"):
        settings.api.rate_limit_per_minute = int(limit)
    if busy := os.environ.get("EFW_BUSY_AT"):
        settings.capacity.busy_at = int(busy)
    if crowded := os.environ.get("EFW_CROWDED_AT"):
        settings.capacity.crowded_at = int(crowded)

    # Crowded below busy is a transposition, not an opinion: taken literally the
    # milder notice could never appear, since the louder one wins first.
    if settings.capacity.crowded_at < settings.capacity.busy_at:
        logger.warning(
            "capacity.crowded_at (%d) is below busy_at (%d); reading them the other "
            "way round",
            settings.capacity.crowded_at,
            settings.capacity.busy_at,
        )
        settings.capacity.busy_at, settings.capacity.crowded_at = (
            settings.capacity.crowded_at,
            settings.capacity.busy_at,
        )

    if not settings.imgw.station_id or not settings.imgw.station_name:
        logger.warning(
            "imgw.station_id/station_name not configured -- observations will be "
            "unavailable until the operator sets them (config.json's 'imgw' block or "
            "EFW_IMGW_STATION_ID/EFW_IMGW_STATION_NAME)."
        )
    if not settings.imgw.teryt:
        logger.warning(
            "imgw.teryt not configured -- warnings will be unavailable until the "
            "operator sets the venue's powiat TERYT code(s) (config.json's 'imgw' "
            "block or EFW_IMGW_TERYT)."
        )

    return settings


settings = load_settings()
