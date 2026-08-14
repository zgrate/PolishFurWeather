# EurofurenceWeather

Eurofurence Weather is an easy to use tool that allows convention goers to quickly assess
the ability to hang outside either with or without suit. This is a **Polish-data fork**:
the original project runs on DWD (Germany); this one runs on
[IMGW-PIB](https://danepubliczne.imgw.pl) and [Open-Meteo](https://open-meteo.com)
instead, for conventions in Poland. See [docs/polish-data.md](docs/polish-data.md) for
exactly what comes from where.

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/B0B6BCXY7)
![alt text](media/Site_preview.png "Site preview")

My friends and I have always looked for an easy method to answer the following the question: 
**can I go outside in suit right now?**

This fork is built on IMGW's public data feeds and Open-Meteo: IMGW SYNOP observations,
official IMGW meteorological warnings, the COSMO 2.8 km model, and the POLRAD radar
composite, with Open-Meteo filling in the forecast horizon beyond COSMO's own reach and
CAMS pollen. It features a wide range of features:


- **Fursuiting Index**: a 0–10 score for how comfortable and safe suiting is, with one
  bar per hour and a weather icon above each so you can see how the day builds.
  **Click any bar** for the conditions behind it
- **Weather overview and warnings**: current conditions, official IMGW warnings, and a
  five-day outlook where **every day gets its own hour-by-hour chart** plus its best and
  worst hour, because during the con every hour matters
- **Rain radar**: the IMGW POLRAD precipitation-rate composite on an interactive dark map
- **Model maps**: COSMO 2.8 km decoded straight from IMGW's GRIB1 files: cloud cover with
  the rain rate painted on top, 2 m temperature, and 10 m wind with direction arrows.
  One button per forecast hour out to +60, or press play and watch them run
- **Pollen**: pick your allergy and the model card grows a tab for it: the Open-Meteo
  CAMS forecast for alder, birch, grasses or ragweed (CAMS does not publish a hazel
  product, so hazel is not offered rather than shown as zero)
- **Works offline**: the page and the last forecast are cached, so it still opens
  on a congested convention network and says how old the numbers are
- **ConOps display** at `/display`: a full-screen board for the info desk
- **Public API** at `/api/v1`: versioned, documented, open
- **EN/DE/PL** language support.
- **Unit conversion** if you prefer Fahrenheit or a different clock type.

## ConOps display

`/display` is a self-refreshing board sized for a screen at the ConOps desk.
Warnings are marked directly on the hourly bars rather than in a tile of their own. Open it fullscreen (F11) and leave it.

By default it is a plain monitor: nothing on it responds to a click, because most boards
are a screen on a wall with no input device near them.

Add `?touch` — `/display?touch` — on a screen that really is a touchscreen. Tapping a bar
then switches the conditions strip to that hour, which is handy at the desk when someone
asks about tonight. It says so in the heading and goes back to live conditions by itself
after 90 seconds, so an unattended board never sits on a forecast.

It fits a 1080p landscape screen exactly and falls back to a single column on portrait or
tablet displays. Might not work in older versions of browsers.

---

## Quick start

Before starting the container, set the venue's IMGW station and TERYT code — see
[Configuration](#configuration) below. Without them the app still starts, but
observations and warnings stay unavailable.

```bash
docker compose up -d
```

Open <http://localhost:8000>.

Without Docker:

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Configuration

This fork does not (and per its own project rules, must not) guess a Polish venue's
station or coordinates — they need to be set before the IMGW-sourced data works:

| Setting | Env var | Where to find it |
|---|---|---|
| IMGW SYNOP station id | `EFW_IMGW_STATION_ID` | [danepubliczne.imgw.pl/api/data/synop](https://danepubliczne.imgw.pl/api/data/synop) |
| Station display name | `EFW_IMGW_STATION_NAME` | same listing |
| Powiat TERYT code(s) | `EFW_IMGW_TERYT` (comma-separated) | [danepubliczne.imgw.pl/pl/dane/warningsmeteo](https://danepubliczne.imgw.pl/pl/dane/warningsmeteo) |
| Venue name/coordinates | `EFW_LOCATION_NAME` / `config.json`'s `location` block | still default to Hamburg from the upstream DWD fork — override for the actual venue, since these also drive the Open-Meteo forecast/pollen point and the model/radar map bounding boxes |

See [docs/polish-data.md](docs/polish-data.md) for the full configuration
reference and every other `EFW_*` variable.

## Data sources

Everything comes from IMGW-PIB and Open-Meteo. IMGW data is published under
its own [terms](https://danepubliczne.imgw.pl/) and requires the attribution
this app already carries in `meta.attribution`; Open-Meteo is
[free for non-commercial use](https://open-meteo.com/en/license).

| Source | What it provides | Updates |
|---|---|---|
| [IMGW SYNOP](https://danepubliczne.imgw.pl/api/data/synop) | Current surface observations, per station | hourly |
| [IMGW warningsmeteo](https://danepubliczne.imgw.pl/pl/dane/warningsmeteo) | Official meteorological warnings per powiat (TERYT) | ~every few minutes |
| [IMGW COSMO_HVD (GRIB1)](https://danepubliczne.imgw.pl/api/data/product) | Cloud cover, temperature, wind on a 2.8 km grid | every 6 h, 60 h horizon |
| [IMGW POLRAD SRI (ODIM_H5)](https://danepubliczne.imgw.pl/api/data/product) | Precipitation-rate radar composite | every 5 min |
| [Open-Meteo forecast](https://open-meteo.com/en/docs) | Hourly point forecast beyond COSMO's horizon | ~hourly |
| [Open-Meteo CAMS air quality](https://open-meteo.com/en/docs/air-quality-api) | Pollen (alder, birch, grasses, ragweed — no hazel product) | few times/day |

Full field-by-field mapping and format notes: [docs/polish-data.md](docs/polish-data.md).

## The Fursuiting Index

The index rates suiting conditions from 0 (stay out of suit) to 10 (perfect). Four
weighted sub-scores feed it:

| Factor | Weight | Why it matters |
|---|---|---|
| Temperature | 50 % | **Wet-bulb temperature** plus a sun load. A suit blocks the sweat evaporation your body relies on, so this dominates. The bands sit on the [heat index](https://de.wikipedia.org/wiki/Hitzeindex) steps — 18.5 °C effective wet-bulb is where its *caution* begins, 24 °C *extreme caution*, 27 °C *danger* — with the suit in the sun load added before them rather than in tighter thresholds. |
| Rain | 30 % | Rain rate blended with probability, plus a penalty for ground still wet from the last 24 h. Steep at the bottom: a suit soaks rain up and stays wet, so 0.3 mm in an hour is already a problem. |
| Wind | 12 % | U-shaped: dead calm turns a suit into an oven, gales are dangerous. Best around 1–3 m/s. |
| Humidity | 8 % | Dew point. |

**Heat and rain are ceilings, not just terms in the mean**, so a good sub-score can never
mask a bad hour: the index is the *lowest* of the weighted mean, the temperature score and
the rain score. Without this, "it isn't raining" (10/10) would drag a dangerously hot hour
up into the middle of the scale — and, the other way round, rain is only 30 % of the mean,
so on an otherwise perfect afternoon the worst it could do was take three points off. An
hour that ends with a suit too wet to wear is not a three-point problem.

There is nothing else to read: when heat or rain decides an hour, the index simply equals
that bar in the breakdown. The chance of rain is folded into the rain score as its square
root, not raw: being caught out in suit is far worse than a dry hour is good, and it is a
call you make an hour ahead.

Warnings are shown **on the hourly bars**, over the hours they cover. The UI still
supports an "advance notice" style (red diagonal hatching, so it is never mistaken for a
warning in force) inherited from DWD's *Vorabinformation* concept, but IMGW has no
equivalent tier — every IMGW-sourced warning carries `advance: false`, so this fork never
renders it. On the public site the wording collapses to a single line that expands on
click; the board shows the marks only.

Overlapping warnings are packed into as few rows as they need, and the chart reserves
exactly that much space, the row count is handed to the stylesheet, so a fourth warning
grows the panel instead of landing across the bars. A band too narrow to hold its wording
(a one-hour warning is a sliver of a 24-hour chart) keeps the marker and moves its label
out to whichever side has room.

## Declaration

AI decliration: AI was used in this project to assist in the building process.

## Licence

MIT — see [LICENSE](LICENSE). Weather, warning, model and radar data © Instytut
Meteorologii i Gospodarki Wodnej – Państwowy Instytut Badawczy (IMGW-PIB). Forecast and
pollen data © Open-Meteo. Forked from
[laffiesphere/EurofurenceWeather](https://github.com/laffiesphere/EurofurenceWeather)
(DWD-based original).
