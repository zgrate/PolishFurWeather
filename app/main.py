"""Eurofurence Weather -- FastAPI application entry point.

Originally by laffiesphere (https://github.com/laffiesphere/EurofurenceWeather),
forked for a Polish venue. Weather data comes from IMGW-PIB (observations,
warnings, POLRAD radar) and Open-Meteo (forecast, pollen); see /api-docs for
the public API and the attribution it requires.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import api_v1, net, presence, ratelimit, schemas, service
from app.config import settings
from app.providers.http import cache, field_cache
from app.providers.imgw import observations as synop
from app.providers.imgw import radar as imgw_radar
from app.providers.imgw import warnings as imgw_warnings
from app.providers.open_meteo import forecast as open_meteo_forecast

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MEDIA_DIR = Path(__file__).resolve().parent.parent / "media"

app = FastAPI(
    title=f"{settings.event.name} — Weather API",
    version="2.1.0",
    description=(
        "Fursuiting index, weather overview and warnings for "
        f"{settings.location.name}, built on IMGW-PIB and Open-Meteo data.\n\n"
        "**Everything published here is JSON under `/api/v1`, and those shapes are "
        "a stable contract.**\n\n"
        "Open access, no key. Please respect the rate limit in the `X-RateLimit-*` "
        "headers and cache responses — the upstream data only changes hourly.\n\n"
        "Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej "
        "– Państwowy Instytut Badawczy. Dane Instytutu Meteorologii i Gospodarki "
        "Wodnej – Państwowego Instytutu Badawczego zostały przetworzone. "
        "Forecast and pollen data © Open-Meteo, CC BY 4.0."
    ),
    contact={"name": "Project source", "url": "https://github.com/laffiesphere/EurofurenceWeather"},
    license_info={"name": "MIT"},
)

# Pin outbound requests to one IP family if the operator asked for it, and
# warn early if IMGW/Open-Meteo turn out not to publish that family -- see
# app/net.py. A no-op under the "auto" default.
net.apply_ip_family(settings.network.ip_family)
net.preflight()

# Politeness ceiling for the open API; see app/ratelimit.py on worker scope.
limiter = ratelimit.SlidingWindowLimiter(settings.api.rate_limit_per_minute)
app.middleware("http")(ratelimit.build_middleware(limiter))

# How busy the machine is, so the page can say so. Registered after the limiter
# and therefore wrapping it: a visitor who runs into the 429 is still here, and
# a rush is exactly when we want to be counting.
visitors = presence.PresenceTracker(
    window_seconds=settings.capacity.active_window_seconds,
    busy_at=settings.capacity.busy_at,
    crowded_at=settings.capacity.crowded_at,
)
if settings.capacity.enabled:
    app.middleware("http")(presence.build_middleware(visitors))

if settings.api.public_api_enabled:
    app.include_router(api_v1.router)

# Convenient if someone embeds the widgets elsewhere (e.g. the EF app or info screens).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
    # Without this a cross-origin consumer cannot read any of it -- including
    # the rate limit headers the docs ask them to respect.
    expose_headers=[
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "Retry-After",
        "X-Site-Load",
        "X-Site-Visitors",
    ],
)


@app.get("/api/summary", include_in_schema=False)
def get_summary(
    lang: str = Query("en", pattern="^(en|de|pl)$", description="Language for generated text"),
) -> JSONResponse:
    """This project's own aggregate. Deliberately absent from the schema.

    Its shape follows whatever the page needs and changes without notice, so
    publishing it only invites someone to build on it and be broken later.
    /api/v1 is the contract; this is plumbing that happens to be reachable.
    """
    payload = service.build_summary(lang)
    if payload["current"] is None and not payload["daily"]:
        raise HTTPException(status_code=503, detail="No weather data available right now")
    # Let a CDN or reverse proxy hold this briefly; upstream data is not second-fresh.
    return JSONResponse(payload, headers={"Cache-Control": "public, max-age=120"})


# ----------------------------------------------------------------- imagery
#
# The interactive forecast map is an embedded Windy iframe (see static/app.js,
# static/index.html) rather than anything this backend renders -- Windy is
# a visualization service, not a data source this app treats as authoritative.
# The one image endpoint left is the pollen stub below, kept 404 rather than
# removed since nothing else here needs raster generation any more.


@app.get("/api/pollen.png", include_in_schema=False)
def get_pollen_image() -> Response:
    """A gridded pollen layer. Not implemented yet.

    Open-Meteo's CAMS-backed pollen endpoint (app/providers/open_meteo/pollen.py)
    only offers point values, not a raster grid to render -- there is no
    Polish/CAMS equivalent of DWD's ICON-ART pollen maps to draw here.
    """
    raise HTTPException(status_code=404, detail="Gridded pollen data is not available")


@app.get(
    "/api/load",
    response_model=schemas.SiteLoad,
    summary="How busy the site is right now",
    description="A load signal, not an audience metric: visitors are counted per client "
    "address inside a short window, so one shared network counts once and a crawler "
    "counts as a person. Nothing about a visitor is stored.",
    tags=["Public API v1"],
)
def site_load() -> JSONResponse:
    # `enabled` matters: with counting switched off nothing calls touch(), so
    # the zeros below would otherwise read as a very quiet afternoon.
    payload = {**visitors.snapshot(), "enabled": settings.capacity.enabled}
    # Never cached: a stale "all quiet" is worse than no answer at all.
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@app.get("/api/health", response_model=schemas.Health, summary="Liveness and cache status", tags=["Public API v1"])
def health() -> dict:
    return {
        "status": "ok",
        "site_name": settings.site_name,
        "event": settings.event.name,
        "station": settings.imgw.station_id,
        "location": settings.location.name,
        "cache_age_seconds": {
            "observations": _age(synop.cache_key()),
            "forecast": _age(open_meteo_forecast.cache_key()),
            "warnings": _age(imgw_warnings.CACHE_KEY),
            "radar": _age(imgw_radar.CACHE_KEY, field_cache),
        },
    }


def _age(key: str, source=cache):
    age = source.age(key)
    return None if age is None else int(age)


# Page markup and its scripts are versioned together. If a browser keeps a
# stale app.js while fetching fresh HTML it will look up elements that no
# longer exist and the page dies on load, so make both revalidate every time.
# ETags keep that to a cheap 304; these files are a few kilobytes.
REVALIDATE = "no-cache, must-revalidate"


class RevalidatingStatic(StaticFiles):
    """StaticFiles that pins app assets to revalidate but lets vendor code cache.

    ``vendor/`` is where version-pinned third-party files would live -- safe to
    cache hard under a given name, unlike app.js/index.html above. Empty for
    now (the last occupant, Leaflet, went with the model-map card), kept for
    whatever needs it next.
    """

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        path = str(kwargs.get("full_path") or (args[0] if args else ""))
        if "vendor" in path.replace("\\", "/"):
            response.headers["Cache-Control"] = "public, max-age=604800"
        else:
            response.headers["Cache-Control"] = REVALIDATE
        return response


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": REVALIDATE})


@app.get("/display", include_in_schema=False)
def display() -> FileResponse:
    """Full-screen board for the ConOps desk / info screens."""
    return FileResponse(STATIC_DIR / "display.html", headers={"Cache-Control": REVALIDATE})


@app.get("/privacy", include_in_schema=False)
@app.get("/datenschutz", include_in_schema=False)
def privacy() -> RedirectResponse:
    """Both old paths still resolve: they are in links, bookmarks and QR codes.

    307 rather than 301: a permanent redirect is cached by browsers effectively
    forever, and the destination is not ours to be that certain about.
    """
    return RedirectResponse(settings.legal.privacy_url, status_code=307)


@app.get("/api-docs", include_in_schema=False)
def api_docs() -> FileResponse:
    """Human-readable API notes. /docs stays the generated, interactive reference.

    Deliberately not under /api/, so the rate limit never turns the explanation
    of the rate limit into a 429.
    """
    return FileResponse(STATIC_DIR / "api-docs.html", headers={"Cache-Control": REVALIDATE})


# Media is content, not code: it can be cached hard and mounts before the
# catch-all so /media/... never falls through to the static handler.
if MEDIA_DIR.is_dir():
    app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")

app.mount("/", RevalidatingStatic(directory=STATIC_DIR), name="static")
