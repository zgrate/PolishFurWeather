# Polish data sources

This fork replaces every DWD (Germany) data source with a Polish/EU equivalent:
[IMGW-PIB](https://danepubliczne.imgw.pl) (Instytut Meteorologii i Gospodarki
Wodnej – Państwowy Instytut Badawczy) for observations, warnings, the COSMO
model and the POLRAD radar composite, and [Open-Meteo](https://open-meteo.com)
for the forecast horizon beyond COSMO's own reach and for CAMS pollen. The
public `/api/v1/*` contract, the FSI calculation, and the UI are unchanged;
only where the numbers come from is different. Architecture: `app/service.py`
calls a single facade, `app/providers/poland.py`, which dispatches to
`app/providers/imgw/*` and `app/providers/open_meteo/*` — the same shape the
old `app/dwd/*` package had, now removed.

Documentation/data interfaces last verified: 2026-08-13

## Data-source matrix

| Data | Source | Module | Update cadence | Cache TTL |
|---|---|---|---|---|
| Current conditions | IMGW SYNOP | `app/providers/imgw/observations.py` | hourly | 10 min (`cache.observations`) |
| Observation history | Local SQLite, built from SYNOP polls | `app/providers/imgw/observations.py` | — | — |
| Meteorological warnings | IMGW `warningsmeteo` feed | `app/providers/imgw/warnings.py` | every few minutes | 3 min (`cache.warnings`) |
| Model maps (clouds+rain, temp, wind) | IMGW COSMO_HVD (2.8 km GRIB1) | `app/providers/imgw/cosmo.py` | every 6 h (00/06/12/18Z), one ~150 MB file per forecast hour | 15 min run check, 3 h per decoded step |
| Precipitation radar | IMGW POLRAD SRI composite (ODIM_H5) | `app/providers/imgw/radar.py` | every 5 min | 4 min |
| Hourly forecast | Open-Meteo `/v1/forecast` | `app/providers/open_meteo/forecast.py` | ~hourly | 30 min (`cache.forecast`) |
| Pollen | Open-Meteo CAMS air-quality `/v1/air-quality` | `app/providers/open_meteo/pollen.py` | few times/day | 6 h |

IMGW's product catalogue (`https://danepubliczne.imgw.pl/api/data/product`)
is resolved live for every fetch that needs a file URL — no product id, file
name, or URL is ever hardcoded (`app/providers/imgw/client.py`). Its own file
*listing* was observed to lag wall-clock time by more than two hours during
testing, so radar/model freshness is judged from the timestamp embedded in
each file's own metadata, never from "it was newest in the listing".

## Why COSMO doesn't drive the point forecast

IMGW publishes COSMO_HVD as one record-sequential GRIB1 file per forecast
hour — every field for that hour bundled together, roughly 150 MB — with no
per-parameter file the way DWD's ICON-D2 offered, and the datastore does not
honour HTTP Range requests (confirmed: a ranged GET still streamed the whole
body). Walking COSMO's own 60-hour horizon for a single point-forecast
response would mean dozens of these ~150 MB downloads per request, which is
not practical to do inline. COSMO_HVD is therefore used **only** for the
model-map card (`/api/model`, `/api/model.png`); the point forecast
(`/api/v1/forecast`, `/api/v1/daily`) comes entirely from Open-Meteo. This is
a scope decision, not a missing feature — see
`EurofurenceWeather_Polish_Backend_Agent_Instructions.md` and
`POLRAD_SRI_Implementation_Summary.md` for the fuller reasoning.

Two things about COSMO_HVD were confirmed against a real downloaded file
rather than taken from IMGW's own `readme.txt`, which turned out to describe
a different, coarser COSMO configuration than what COSMO_HVD actually ships:

- **Grid geometry.** The readme claims a 415×460 grid at 0.0625° spacing and
  a rotated pole at (lat 32.5, lon −170). The GDS bytes of a live file
  instead give a 380×405 grid at 0.025° spacing (~2.8 km, consistent with the
  product's own "2k8" name — the readme's number is not) and a south pole at
  (lat −40, lon 10), equivalently a north pole at (lat 40, lon −170°). The
  rotated↔true coordinate round-trip was verified numerically (see
  `tests/test_cosmo.py`), and the domain it produces is centred almost
  exactly on Poland.
- **Field identity.** IMGW's readme lists fixed record positions per field
  (e.g. T_2M as record 365, TOT_PREC as 383). Those positions were
  cross-checked against each message's own PDS parameter/level-type/level
  codes on a live file and matched exactly — see `FIELDS` in
  `app/providers/imgw/cosmo.py`, which is keyed on the PDS codes (the
  standards-compliant identifier), not on file position, so a future
  reordering would still be found correctly rather than silently reading the
  wrong field.

## Field mappings

### Current conditions — IMGW SYNOP

`app/providers/imgw/observations.py`, `_parse_synop`.

| IMGW field | App field | Notes |
|---|---|---|
| `data_pomiaru` + `godzina_pomiaru` | `time` | UTC |
| `temperatura` | `temperature` | °C |
| `wilgotnosc_wzgledna` | `humidity` | % |
| — | `dewpoint` | derived from temperature + humidity (Magnus formula), not published directly |
| `predkosc_wiatru` | `wind_speed` | m/s |
| `kierunek_wiatru` | `wind_direction` | degrees |
| `cisnienie` | `pressure` | hPa |
| `suma_opadu` (WO6G) | *not mapped to* `precipitation` | see below |
| — | `wind_gust`, `cloud_cover`, `visibility`, `weather_code` | always `None` — IMGW SYNOP does not publish these |

**`suma_opadu` (WO6G) is deliberately never used as this hour's rain.** It is
a 6-hour gauge total published for quality control, not an hourly figure, and
mapping it to `precipitation` would silently misrepresent it as something
it isn't. `WeatherPoint.precipitation` from this source is always `None`;
current precipitation instead comes from the POLRAD radar estimate
(`app/providers/imgw/radar.py`'s `precipitation_intensity`). See
`test_wo6g_never_populates_hourly_precipitation` in
`tests/test_imgw_observations.py` and `POLRAD_SRI_Implementation_Summary.md`.

IMGW's SYNOP endpoint returns only the current reading, with no bundled
history the way DWD's POI reports had. `app/providers/imgw/observations.py`
keeps its own small SQLite store (path: `EFW_IMGW_DB_PATH`, default inside
the container) populated by each poll, and `fetch_recent` reads it back.

### Meteorological warnings — IMGW `warningsmeteo`

`app/providers/imgw/warnings.py`, `_normalise`.

| IMGW field | App field | Notes |
|---|---|---|
| `nazwa_zdarzenia` | `event`, `headline` | mapped to a `kind` via a lookup table; unrecognised names fall back to `"other"` rather than erroring |
| `tresc` | `description` | whitespace runs collapsed |
| `komentarz` | `instruction` | |
| `stopien` (1–3) | `severity`, `color`, `level` | 1=minor, 2=moderate, 3=severe; an undocumented value falls back to minor rather than crashing |
| `biuro` | `region` | |
| `obowiazuje_od` / `obowiazuje_do` | `start` / `end` | parsed as **Europe/Warsaw local time** (so CEST/CET is handled), not UTC |
| `teryt` | — | intersected against `settings.imgw.teryt` to decide whether a warning applies to the venue |
| — | `advance` | always `False` — IMGW has no *Vorabinformation*/advance-notice tier the way DWD did |

### Model maps — COSMO_HVD (GRIB1)

`app/providers/imgw/cosmo.py`, `FIELDS`.

| Field key | GRIB1 (param, levelType, level) | Used for |
|---|---|---|
| `t_2m` | (11, 105, 2) | Temperature map |
| `td_2m` | (17, 105, 2) | (decoded, not currently surfaced on a map) |
| `u_10m` / `v_10m` | (33/34, 105, 10) | Wind map + arrows |
| `vmax_10m` | (187, 105, 10) | (decoded, not currently surfaced) |
| `pmsl` | (2, 102, 0) | (decoded, not currently surfaced) |
| `clct` | (71, 1, 0) | Cloud cover map |
| `tot_prec` | (61, 1, 0) | Rain overlay, via the delta between consecutive forecast steps (`_rain_rate_mm_h`) — it is a run-cumulative total, not an hourly figure |
| `relhum_2m` | (52, 105, 2) | (decoded, not currently surfaced) |

### Precipitation radar — POLRAD SRI (ODIM_H5)

`app/providers/imgw/radar.py`, confirmed against a live
`COMPO_SRI.comp.sri` file (`*.sri.h5`, ODIM_H5/V2_3):

| HDF5 path/attribute | Meaning |
|---|---|
| `dataset1/what.quantity` | `"RATE"` — precipitation rate |
| `dataset1/what.gain` / `.offset` | `physical = raw*gain + offset` |
| `dataset1/what.nodata` | outside radar coverage → reported as `None`, never `0` |
| `dataset1/what.undetect` | covered, no precipitation detected → `0.0` mm/h, a real reading |
| `dataset1/data1/data` | `float32[800, 800]`, row 0 = north edge |
| `where.projdef` | `+proj=aeqd +lon_0=19.0926 +lat_0=52.3469 +ellps=sphere` — azimuthal-equidistant, projected via `pyproj` |
| `where.{UL,UR,LL,LR}_{lat,lon}`, `xscale`, `yscale` | corner geolocation used for the pixel↔lat/lon transform |

A frame older than `imgw.radar_max_age_minutes` (default 15) is treated as
unavailable (`None`) rather than shown stale.

### Hourly forecast — Open-Meteo

`app/providers/open_meteo/forecast.py`, confirmed against a live response
from `https://api.open-meteo.com/v1/forecast` (`windspeed_unit=ms`):

| Open-Meteo field | App field |
|---|---|
| `temperature_2m` | `temperature` (°C) |
| `dew_point_2m` | `dewpoint` (°C) |
| `relative_humidity_2m` | `humidity` (%) |
| `wind_speed_10m` | `wind_speed` (m/s) |
| `wind_gusts_10m` | `wind_gust` (m/s) |
| `wind_direction_10m` | `wind_direction` (deg) |
| `pressure_msl` | `pressure` (hPa) |
| `cloud_cover` | `cloud_cover` (%) |
| `precipitation` | `precipitation` (mm in the hour) |
| `precipitation_probability` | `precipitation_prob` (%) |
| `shortwave_radiation` | `solar_radiation` (W/m²) |
| `visibility` | `visibility` (m) |
| `weather_code` | `weather_code` (WMO ww, same table `app/weather_codes.py` already used) |

### Pollen — Open-Meteo CAMS air quality

`app/providers/open_meteo/pollen.py`, confirmed against a live response from
`https://air-quality-api.open-meteo.com/v1/air-quality`:

| CAMS field | App species key |
|---|---|
| `alder_pollen` | `alder` |
| `birch_pollen` | `birch` |
| `grass_pollen` | `grasses` |
| `ragweed_pollen` | `ragweed` |

**CAMS does not publish a hazel product at all** — there is no
`hazel_pollen` field, not a zero-valued one. Per this project's rule (never
report an unavailable species as zero), hazel is simply absent from the
species table and never appears in a reading, rather than being mapped to
anything. `mugwort_pollen`/`olive_pollen` exist in the source but are outside
this app's species set and are ignored, same as before. Any species value
that comes back `null` for the current hour (out of season / not modelled)
is likewise omitted from the response, not shown as `0`. See
`test_hazel_is_never_reported_because_cams_has_no_such_field` and
`test_a_null_reading_is_omitted_rather_than_shown_as_zero` in
`tests/test_open_meteo.py`.

## Attribution

IMGW-sourced responses (`/api/v1/*`'s `meta.attribution`, and the radar
overlay) carry, verbatim, the two strings IMGW's terms require:

> Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej –
> Państwowy Instytut Badawczy

> Dane Instytutu Meteorologii i Gospodarki Wodnej – Państwowego Instytutu
> Badawczego zostały przetworzone.

Open-Meteo attribution is kept separate and is never presented as an
official IMGW forecast — Open-Meteo covers the forecast horizon and pollen
only, both clearly out of IMGW's own remit (IMGW does not publish a public
point forecast or pollen product).

GIOŚ air quality is out of scope for this fork.

## Configuration an operator must supply

Per the project rule against inventing venue-specific values, the following
have no working default and must be set in `config.json`'s `imgw` block (or
the matching `EFW_*` environment variable) before the Polish sources return
anything — the app starts without them, logging a warning, rather than
failing or silently pointing at the wrong town:

| Setting | Env var | How to find it |
|---|---|---|
| IMGW SYNOP station id | `EFW_IMGW_STATION_ID` | [danepubliczne.imgw.pl/api/data/synop](https://danepubliczne.imgw.pl/api/data/synop) |
| Station display name | `EFW_IMGW_STATION_NAME` | same listing |
| Powiat TERYT code(s) | `EFW_IMGW_TERYT` (comma-separated) | [danepubliczne.imgw.pl/pl/dane/warningsmeteo](https://danepubliczne.imgw.pl/pl/dane/warningsmeteo) — each warning entry carries the TERYT codes it applies to |

**Also still carrying the old Hamburg/DWD-fork defaults** and needing an
operator override for a Polish venue: `location.name`, `location.latitude`,
`location.longitude`, `location.timezone` in `app/config.py`'s `Location`
(env vars `EFW_LOCATION_NAME` for the name; latitude/longitude/timezone are
`config.json`-only today). These drive the COSMO/radar map bounding boxes
and the Open-Meteo forecast/pollen query point, so leaving them unset means
those sources are queried for Hamburg's coordinates even once the IMGW
settings above are filled in.

## Networking

`app/net.py` can pin outbound requests to one IP family (`EFW_IP_FAMILY` =
`auto` (default) / `4` / `6`) and runs a startup preflight against IMGW and
Open-Meteo so a misconfigured pin (e.g. forcing IPv6 against a host that only
answers on IPv4) is logged clearly rather than surfacing as opaque per-request
timeouts later.

## Removed

`app/dwd/` (DWD MOSMIX/POI/warnings/ICON-D2/ICON-ART pollen client) has been
deleted along with its dedicated tests (`tests/test_grib2.py`,
`tests/test_parsers.py`, `tests/test_pollen.py`) and the `dwd` block from
`config.json`/`app/config.py`'s `Settings` — nothing in the running app
imported it any longer. It is preserved in git history for reference; the
upstream project (`laffiesphere/EurofurenceWeather`) it was forked from
remains the DWD-based original.
