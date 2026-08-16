/* Eurofurence Weather
   Author: laffiesphere. */

const REFRESH_MS = 5 * 60 * 1000;

const $ = (id) => document.getElementById(id);
const T = (key, vars) => EFW_I18N.T(key, vars);

const fmt = (value, digits = 0, unit = '') =>
  value === null || value === undefined ? '–' : `${Number(value).toFixed(digits)}${unit}`;

function text(el, value) {
  if (!el) {
    console.warn('EF Weather: missing element for text update', value);
    return;
  }
  el.textContent = value;
}

let latest = null;
const PAST_CONTEXT_HOURS = 6;
const STALE_AFTER_MS = 15 * 60 * 1000;

/* When the service worker stored the payload we are showing, or null when it
   came off the network just now. Set from the stamp sw.js puts on its copies. */
let offlineCopyAt = null;
let lastLoadAt = 0;

/* ---------------------------------------------------------------- warnings */

function renderWarnings(warnings) {
  const host = $('warnings');
  host.innerHTML = '';
  if (!warnings.length) return;
  const worst = warnings[0];
  const advance = warnings.filter((w) => w.advance).length;

  const details = document.createElement('details');
  details.className = 'warnings-summary';
  details.style.setProperty('--level', worst.color);

  const summary = document.createElement('summary');
  summary.append(document.createTextNode('⚠️ '));

  const headline = document.createElement('span');
  headline.textContent = worst.event_en || worst.event;
  summary.append(headline);

  if (worst.start || worst.end) {
    const when = document.createElement('span');
    when.className = 'count';
    when.textContent = formatRange(worst.start, worst.end);
    summary.append(when);
  }

  if (advance) {
    const tag = document.createElement('span');
    tag.className = 'advance-tag';
    tag.textContent = T('warnings.advance');
    summary.append(tag);
  }

  if (warnings.length > 1) {
    const more = document.createElement('span');
    more.className = 'count';
    more.textContent = T('warnings.more', { n: warnings.length - 1 });
    summary.append(more);
  }

  details.append(summary);

  const detail = document.createElement('div');
  detail.className = 'detail';
  for (const warning of warnings) detail.append(warningCard(warning));
  details.append(detail);

  host.append(details);
}

function warningCard(warning) {
  const article = document.createElement('article');
  article.className = `warning${warning.advance ? ' is-advance' : ''}`;
  article.style.setProperty('--level', warning.advance ? '#e53935' : warning.color);

  const title = document.createElement('h3');
  title.textContent = warning.event_en || warning.event;
  article.append(title);

  const when = document.createElement('p');
  when.className = 'when';
  when.textContent = [formatRange(warning.start, warning.end), warning.region]
    .filter(Boolean)
    .join(' · ');
  article.append(when);

  if (warning.advance) {
    const note = document.createElement('p');
    note.className = 'advance-note';
    note.textContent = T('warnings.advanceNote');
    article.append(note);
  }

  for (const [value, className] of [
    [warning.headline, 'official'],
    [warning.description, ''],
    [warning.instruction, 'instruction'],
  ]) {
    if (!value) continue;
    const paragraph = document.createElement('p');
    if (className) paragraph.className = className;
    paragraph.lang = 'pl';
    paragraph.textContent = value;
    article.append(paragraph);
  }
  return article;
}

function formatRange(start, end) {
  const options = { weekday: 'short' };
  const from = start ? EFW_I18N.dateTime(start, options) : '';
  const to = end ? EFW_I18N.dateTime(end, options) : '';
  if (from && to) return `${from} – ${to}`;
  return from || to || '';
}

/* -------------------------------------------------------------------- FSI */

function renderFSI(data) {
  const fsi = data.fsi;
  nowFsi = fsi || null; // what the card falls back to when nothing is picked
  const card = $('fsi-card');
  if (!fsi) {
    text($('fsi-label'), T('band.bad'));
    return;
  }

  /* The tint and the ink chosen to read against it are one decision, so they go
     on together. Set the other way round, anything that went wrong between the
     two lines left the panel wearing its colour with no ink to match -- which
     falls back to the page's near-white text on a pale card. Ink first, and the
     worst case is an untinted panel that is still perfectly readable. */
  const ink = EFW.contrastText(fsi.color);
  card.style.setProperty('--ink', ink);
  card.style.setProperty('--fsi', fsi.color);

  text($('fsi-score')?.querySelector('.value'), fsi.score.toFixed(1));
  text($('fsi-label'), fsi.label);
  text($('fsi-advice'), fsi.advice);

  /* The headline is scored from the station report -- the same reading "Right
     now" shows -- while the bars underneath are the Open-Meteo forecast. The
     two disagree now and then, and calling a measurement a "forecast hour"
     made that read as a contradiction rather than as two different things.
     Say which this one is. The station report does go missing, and then this
     number does come from the forecast, so the wording follows the source
     rather than assuming. */
  const observed = data.current ? data.current.time_local : null;
  const measured = data.current && data.current.source === 'poi';
  const when = T(measured ? 'fsi.measured' : 'fsi.hour');
  text($('fsi-time'), observed ? `· ${when} ${EFW_I18N.time(observed)}` : '');

  // The headline hour, unless a bar on the strip has been picked -- then this
  // shows that hour instead. Drawn from the same place either way.
  syncCardBreakdown();

  renderWeights(fsi.subscores);
}

/**
 * The parts a score is made of, as a row of horizontal bars.
 *
 * The headline score and any hour picked off a chart are broken down the same
 * way, so one function draws both. An hour out of the series carries no names
 * with it -- they are the same four for all 120 of them, so the payload
 * publishes them once -- and `subLabels` puts them back.
 */
function renderSubscores(list, subscores) {
  list.innerHTML = '';
  for (const key of Object.keys(subscores || {})) {
    const entry = subscores[key];
    const item = document.createElement('li');
    if (entry.reason) item.title = entry.reason;

    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = entry.label || subLabels[key] || key;

    const bar = document.createElement('span');
    bar.className = 'bar';
    const fill = document.createElement('span');
    fill.className = 'fill';
    fill.style.width = `${entry.score * 10}%`;
    bar.append(fill);

    const num = document.createElement('span');
    num.className = 'num';
    num.textContent = entry.score.toFixed(1);

    item.append(name, bar, num);
    list.append(item);
  }
}

/** The names of the four parts, which the series no longer repeats per hour.
    An older payload out of the offline cache has them on the current score
    instead, so that is the fallback rather than four untranslated keys. */
function labelsFor(data) {
  if (data.subscore_labels) return data.subscore_labels;
  const subscores = (data.fsi && data.fsi.subscores) || {};
  const labels = {};
  for (const key of Object.keys(subscores)) labels[key] = subscores[key].label;
  return labels;
}

/**
 * Point the card's breakdown at whichever hour is selected.
 *
 * Only for the strip in this card: a bar picked in a day card is several
 * screens further down, where nothing changing up here could be seen at all,
 * so those open their own bars in the panel beside them.
 */
function syncCardBreakdown() {
  const list = $('subscores');
  if (!list) return;

  const entry = pickedChart === 'timeline' ? conditionsByTime.get(picked) : null;
  // An hour with no forecast behind it has no parts to show; rather than empty
  // the panel, the card keeps saying what it says when nothing is picked.
  const hour = entry && entry.subscores && Object.keys(entry.subscores).length ? entry : null;
  const source = hour || nowFsi;
  if (!source) return;

  renderSubscores(list, source.subscores);

  // Which hour these bars are about. Blank for "now": the heading above the
  // card already carries the time that one was measured at.
  const at = $('subscores-at');
  if (at) at.textContent = hour ? EFW_I18N.dateTime(hour.time, { weekday: 'short' }) : '';
}

/* ------------------------------------------- how much each part of it counts */

/* Geometry of the ring, in the units of its own 100x100 viewBox. The gap is
   the card showing through between segments: a drawn divider would have to
   pick a colour, and the card's colour changes with the score. */
const PIE = { size: 100, radius: 34, width: 19, gap: 1.6 };
const SVG_NS = 'http://www.w3.org/2000/svg';


function renderWeights(subscores) {
  const figure = $('weights');
  const host = $('weights-pie');
  const legend = $('weights-legend');
  if (!figure || !host || !legend) return; // cached markup from before this existed

  const parts = Object.keys(subscores)
    .map(function (key) {
      return { label: subscores[key].label, weight: subscores[key].weight || 0 };
    })
    .filter(function (part) {
      return part.weight > 0;
    })
    .sort(function (a, b) {
      return b.weight - a.weight;
    });

  const total = parts.reduce(function (sum, part) {
    return sum + part.weight;
  }, 0);

  figure.hidden = parts.length < 2 || total <= 0;
  if (figure.hidden) return;

  const half = PIE.size / 2;
  const circumference = 2 * Math.PI * PIE.radius;

  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${PIE.size} ${PIE.size}`);
  svg.setAttribute('role', 'img');

  const ring = document.createElementNS(SVG_NS, 'g');
  ring.setAttribute('transform', `rotate(-90 ${half} ${half})`);
  svg.append(ring);

  let start = 0; // distance along the ring the next segment begins at
  const spoken = [];
  legend.innerHTML = '';

  for (let index = 0; index < parts.length; index += 1) {
    const share = parts[index].weight / total;
    const arc = share * circumference;
    const drawn = Math.max(arc - PIE.gap, 0.5);
    const step = `s${Math.min(index + 1, 4)}`;

    const segment = document.createElementNS(SVG_NS, 'circle');
    segment.setAttribute('class', `seg ${step}`);
    segment.setAttribute('cx', half);
    segment.setAttribute('cy', half);
    segment.setAttribute('r', PIE.radius);
    segment.setAttribute('stroke-width', PIE.width);
    segment.setAttribute('stroke-dasharray', `${drawn} ${circumference - drawn}`);
    segment.setAttribute('stroke-dashoffset', -(start + PIE.gap / 2));
    ring.append(segment);
    start += arc;

    const percent = `${Math.round(share * 100)} %`;
    spoken.push(`${parts[index].label} ${percent}`);

    const row = document.createElement('li');
    const swatch = document.createElement('span');
    swatch.className = `swatch ${step}`;
    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = parts[index].label;
    const value = document.createElement('span');
    value.className = 'pct';
    value.textContent = percent;
    row.append(swatch, name, value);
    legend.append(row);
  }

  svg.setAttribute('aria-label', T('fsi.weightsAlt', { parts: spoken.join(', ') }));
  host.innerHTML = '';
  host.append(svg);
}

/* Purely cosmetic: one particular score earns a video. */
function renderEasterEgg(egg) {
  const panel = $('egg');
  if (!panel) return;
  const show = egg === 'ravi67';
  panel.hidden = !show;

  const video = $('egg-video');
  if (!video) return;
  if (show && video.paused) video.play().catch(() => {});
  if (!show) video.pause();
}

function renderLegend(host) {
  if (!host) return;
  host.innerHTML = '';
  for (const { color, label } of EFW.bands()) {
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.style.setProperty('--c', color);
    host.append(chip, document.createTextNode(` ${label} `));
  }
}

/* ------------------------------------------------------- current + daily */

/* Every explanation panel needs an id of its own for aria-controls, and the
   grids are rebuilt on every refresh, so the counter runs for the session. */
let noteSeq = 0;

function statGrid(host, items) {
  host.innerHTML = '';

  const note = document.createElement('p');
  note.className = 'stat-note';
  note.id = `stat-note-${(noteSeq += 1)}`;
  note.hidden = true;
  let open = null; // the button whose explanation is showing, if any

  for (const [key, value, infoKey] of items) {
    const item = document.createElement('div');
    item.className = 'item';
    const k = document.createElement('div');
    k.className = 'k';
    k.textContent = key;
    if (infoKey) k.append(document.createTextNode(' '), infoButton(key, infoKey));
    const v = document.createElement('div');
    v.className = 'v';
    v.textContent = value;
    item.append(k, v);
    host.append(item);
  }

  host.append(note);

  function infoButton(term, infoKey) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'info';
    button.textContent = 'i';
    button.setAttribute('aria-label', T('info.about', { term: term }));
    button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-controls', note.id);
    button.addEventListener('click', function () {
      const show = open !== button; // the same (i) again closes it
      if (open) open.setAttribute('aria-expanded', 'false');
      open = show ? button : null;
      button.setAttribute('aria-expanded', show ? 'true' : 'false');
      note.textContent = show ? T(infoKey) : '';
      note.hidden = !show;
    });
    return button;
  }
}

/* `place` rather than `location`: the name is taken, and shadowing it here would
   be a trap for whoever next reaches for the query string in this function. */
function renderNow(current, fsi, place) {
  /* Where these numbers were measured and when. The station is a few kilometres
     out of town, and the reading can be up to an hour old, so both belong in the
     heading rather than in small print underneath it: "Right now" is only true
     of somewhere, at some time.

     The station name is the operator's own IMGW SYNOP station. The word
     "measured" is dropped when the reading is not one: with the station report
     missing these come from the Open-Meteo forecast and the hour is a forecast.
     Same two words as the index panel, which is making the same distinction
     about the same reading. */
  const source = $('now-source');
  if (source) {
    const station = (place && place.station_name) || '';
    const when =
      current && current.time_local
        ? `${T(current.source === 'poi' ? 'fsi.measured' : 'fsi.hour')} ${EFW_I18N.time(current.time_local)}`
        : '';
    text(source, [station, when].filter(Boolean).map((part) => `· ${part}`).join(' '));
  }

  const host = $('now');
  host.innerHTML = '';
  if (!current) return;

  const items = [
    [T('now.conditions'), `${current.weather.icon || ''} ${current.weather.text || '–'}`.trim()],
    [T('now.temperature'), EFW_I18N.temp(current.temperature)],
    [T('now.wetbulb'), EFW_I18N.temp(fsi?.wetbulb), 'info.wetbulb'],
    [T('now.dewpoint'), EFW_I18N.temp(fsi?.dewpoint), 'info.dewpoint'],
    [T('now.humidity'), fmt(current.humidity, 0, ' %')],
    [
      T('now.wind'),
      `${EFW_I18N.wind(current.wind_speed_kmh)}${current.wind_direction_name ? ` ${current.wind_direction_name}` : ''}`,
    ],
    [T('now.gusts'), EFW_I18N.wind(current.wind_gust_kmh)],
    [T('now.rain1h'), fmt(current.precipitation, 1, ' mm')],
    [T('now.pressure'), fmt(current.pressure, 0, ' hPa')],
  ];

  statGrid(host, items);
}

/* ---- conditions behind single bar */


let picked = null; // ISO time of the selected hour
let pickedChart = null; // which chart it was picked in
let charts = []; // {key, strip, detail, series}, rebuilt on every render
let conditionsByTime = new Map(); // ISO time -> the enriched entry from fsi_series
let subLabels = {}; // sub-score key -> its name, published once per payload
let nowFsi = null; // the current score, which the card shows with nothing picked

const hourEntry = (entry) => ({ ...entry, ...(conditionsByTime.get(entry.time) || {}) });

function ensureDetail(id, strip) {
  const existing = $(id);
  if (existing) return existing;

  console.warn('EFW: markup has no #%s, building one (stale cached HTML?)', id);
  const host = document.createElement('div');
  host.id = id;
  host.className = 'hour-detail';
  host.hidden = true;
  const caption = strip.parentNode?.querySelector('.strip-caption');
  (caption || strip).insertAdjacentElement('afterend', host);
  return host;
}

function registerChart(key, strip, detail, series) {
  charts.push({ key, strip, detail, series });
  return {
    markNow: true,
    selected: pickedChart === key ? picked : null,
    onSelect: (entry) => pickHour(key, entry.time),
  };
}

function pickHour(key, time) {
  const same = pickedChart === key && picked === time; // clicking it again closes it
  picked = same ? null : time;
  pickedChart = same ? null : key;
  applySelection();
}

function clearPick() {
  picked = null;
  pickedChart = null;
  applySelection();
}

function applySelection() {
  const stillThere = charts.some(
    (chart) => chart.key === pickedChart && chart.series.some((e) => e.time === picked)
  );
  if (!stillThere) clearPickState(); // the hour dropped off the chart under it

  for (const { key, strip, detail, series } of charts) {
    if (!strip || !detail) continue; // degrade to one dead chart, never a dead page
    const time = key === pickedChart ? picked : null;
    for (const column of strip.querySelectorAll('.hour')) {
      column.classList.toggle('is-selected', Boolean(time) && column.dataset.time === time);
    }

    const entry = time ? series.find((item) => item.time === time) : null;
    if (entry) {
      // The index card breaks the picked hour down in its own bars, right
      // below this panel; anywhere else the panel has to carry them itself.
      renderHourDetail(detail, hourEntry(entry), key !== 'timeline');
    } else {
      detail.innerHTML = '';
      detail.hidden = true;
    }
  }

  syncCardBreakdown();
}

function clearPickState() {
  picked = null;
  pickedChart = null;
}

function renderHourDetail(host, entry, breakdown) {
  host.innerHTML = '';

  const color = entry.color || EFW.scoreColor(entry.score);
  host.style.setProperty('--c', color);
  host.style.setProperty('--c-ink', EFW.contrastText(color));

  const head = document.createElement('div');
  head.className = 'head';

  const when = document.createElement('span');
  when.className = 'when';
  when.textContent = EFW_I18N.dateTime(entry.time, { weekday: 'short' });
  head.append(when);

  if (entry.weather?.icon) {
    const icon = document.createElement('span');
    icon.className = 'icon';
    icon.textContent = entry.weather.icon;
    head.append(icon);
  }

  const conditions = document.createElement('span');
  conditions.className = 'cond';
  conditions.textContent = entry.weather?.text || '';
  head.append(conditions);

  const score = document.createElement('span');
  score.className = 'score';
  score.textContent = `${entry.score.toFixed(1)} · ${entry.label || EFW.scoreLabel(entry.score)}`;
  head.append(score);

  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'close';
  close.textContent = '×';
  close.setAttribute('aria-label', T('hour.close'));
  close.addEventListener('click', clearPick);
  head.append(close);

  const grid = document.createElement('div');
  grid.className = 'now';
  statGrid(grid, [
    [T('now.temperature'), EFW_I18N.temp(entry.temperature)],
    [T('now.wetbulb'), EFW_I18N.temp(entry.wetbulb), 'info.wetbulb'],
    [T('now.dewpoint'), EFW_I18N.temp(entry.dewpoint), 'info.dewpoint'],
    [T('now.humidity'), fmt(entry.humidity, 0, ' %')],
    [
      T('now.wind'),
      `${EFW_I18N.wind(entry.wind_speed_kmh)}${entry.wind_direction_name ? ` ${entry.wind_direction_name}` : ''}`,
    ],
    [T('now.gusts'), EFW_I18N.wind(entry.wind_gust_kmh)],
    [T('hour.rain'), fmt(entry.precipitation, 1, ' mm')],
    [T('hour.rainChance'), fmt(entry.precipitation_prob, 0, ' %')],
  ]);

  host.append(head, grid);

  // Where the hour's score came from, on the same bars the index card uses.
  // Only where the card's own set is out of sight -- in the card itself those
  // bars are a few lines below, and drawing a second copy here would ask the
  // reader which of two identical rows is the one they picked.
  const subscores = entry.subscores;
  if (breakdown && subscores && Object.keys(subscores).length) {
    const heading = document.createElement('p');
    heading.className = 'subscores-head';
    const word = document.createElement('span');
    word.textContent = T('fsi.scoreHeader');
    heading.append(word);

    const list = document.createElement('ul');
    list.className = 'subscores';
    renderSubscores(list, subscores);
    host.append(heading, list);
  }

  // Say so plainly, rather than letting a forecast for 09:00 read as advice.
  if (EFW.hourStart(entry.time) + 3600 * 1000 <= Date.now()) {
    const note = document.createElement('p');
    note.className = 'past-note';
    note.textContent = T('hour.past');
    host.append(note);
  }

  host.hidden = false;
}

/**
 * Every hour of one local day, in order, with the forecast dropped into place.
 *
 * The hours the forecast does not reach -- this morning, before the run that is
 * current began -- become empty slots rather than being left out. A day that
 * only half exists would otherwise draw half as many bars, twice as wide, and
 * the day cards could no longer be read against each other at a glance.
 *
 * Stepped by the hour in real time, so the 23- and 25-hour days either side of
 * a clock change come out the length they actually are.
 */
function fullDay(day) {
  const known = new Map(day.series.map((entry) => [EFW.hourStart(entry.time), entry]));
  const midnight = new Date(`${day.date}T00:00:00`); // no offset: local time
  const slots = [];

  for (let at = new Date(midnight); at.getDate() === midnight.getDate(); ) {
    const entry = known.get(at.getTime());
    slots.push(entry || { time: at.toISOString(), score: null });
    at = new Date(at.getTime() + 3600 * 1000);
  }
  return slots;
}

/** "Best 09:00–13:00", on the reader's chosen clock. Nothing at all if the day
    has no such stretch -- an empty label would only raise the question. */
function windowRow(className, label, window) {
  if (!window) return '';
  const span = `${EFW_I18N.time(window.start)}–${EFW_I18N.time(window.end)}`;
  // The score stays, but as the tooltip: the row is about when, not how much.
  return `<span class="${className}" title="${fmt(window.peak_score, 1)}">${label} <strong>${span}</strong></span>`;
}

function renderDays(days) {
  const host = $('days');
  host.innerHTML = '';
  const locale = EFW_I18N.locale();

  for (const day of days) {
    const row = document.createElement('article');
    row.className = 'day';

    const date = new Date(`${day.date}T00:00:00`); // no offset: local time, matches fullDay()
    const temps = day.partial
      ? `<span class="lo">${EFW_I18N.tempShort(day.temp_min)}–${EFW_I18N.tempShort(day.temp_max)}</span>`
      : `<strong>${EFW_I18N.tempShort(day.temp_max)}</strong> <span class="lo">${EFW_I18N.tempShort(day.temp_min)}</span>`;

    const direction = day.wind_direction_name ? ` ${day.wind_direction_name}` : '';

    const header = document.createElement('header');
    header.innerHTML = `
      <span class="when">
        <span class="name">${date.toLocaleDateString(locale, { weekday: 'long' })}</span>
        <span class="date">${date.toLocaleDateString(locale, { day: 'numeric', month: 'short' })}</span>
      </span>
      <span class="cond"><span class="icon">${day.weather.icon || ''}</span> ${day.weather.text || ''}</span>
      <span class="temps">${temps}</span>
      <span class="meta">💧 ${fmt(day.precipitation_prob, 0, '%')} · ☀️ ${fmt(
        day.sunshine_hours,
        0,
        ' h'
      )} · 💨 ${EFW_I18N.wind(day.wind_speed_kmh, { unit: false })}–${EFW_I18N.wind(
        day.wind_gust_kmh
      )}${direction}</span>
    `;

    const strip = document.createElement('div');
    strip.className = 'timeline day-strip';
    strip.setAttribute('role', 'group');
    strip.setAttribute(
      'aria-label',
      `${T('days.heading')} — ${date.toLocaleDateString(locale, { weekday: 'long' })}`
    );

    // Each day answers for its own bars, so the panel opens where you clicked
    // rather than somewhere off the top of the page.
    const detail = document.createElement('div');
    detail.className = 'hour-detail';
    detail.hidden = true;

    const hilo = document.createElement('div');
    hilo.className = 'hilo';
    hilo.innerHTML =
      day.hour_count < 3
        ? `<span>${T('days.partial', { n: day.hour_count })}</span>`
        : `
      ${windowRow('best', T('days.best'), day.fsi_best_window)}
      ${windowRow('worst', T('days.worst'), day.fsi_worst_window)}
      <span class="avg">${T('days.average')} ${fmt(day.fsi_avg, 1)} ${T('days.scoreUnit')}</span>
    `;

    row.append(header, strip, detail, hilo);
    host.append(row);
    const slots = fullDay(day);
    EFW.renderStrip(strip, slots, {
      labelEvery: 3,
      emptyLabel: T('days.noData'),
      ...registerChart(day.date, strip, detail, slots),
    });
  }
}

/* ---------------------------------------------------------------- embeds */

/* Cross-origin iframes (RainViewer, Windy) with dragging on claim every touch
   that lands on them, so on a phone the page cannot be scrolled past them at
   all: the finger pans the embed instead of the page. One-finger dragging is
   therefore off wherever the pointer is a finger, until the visitor
   deliberately taps once; two fingers still pan/zoom the embed, and a mouse
   is unaffected. */
const COARSE_POINTER =
  typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches;

/* A cross-origin iframe cannot be reached into, so "let two fingers pass
   through" has to work entirely from outside: keep the iframe non-interactive
   until the visitor deliberately taps it once. Shared by the radar and
   forecast-map embeds -- same trick, different iframe. */
function armTapToInteract(iframe, hint, hintText) {
  if (!COARSE_POINTER || !hint) return;
  iframe.style.pointerEvents = 'none';
  hint.hidden = false;
  hint.textContent = hintText;
  hint.addEventListener(
    'click',
    () => {
      iframe.style.pointerEvents = 'auto';
      hint.hidden = true;
    },
    { once: true }
  );
}

let radarEmbedInitialised = false;

/* RainViewer's own embed refreshes itself inside its iframe, so this only
   ever runs once per page load -- there is no polling to keep going the way
   the self-rendered overlay needed. */
function initRadarEmbed(data) {
  if (radarEmbedInitialised) return;
  radarEmbedInitialised = true;

  const iframe = $('radar-embed');
  if (!iframe) return;

  const { latitude, longitude } = data.location;
  const zoom = 8;
  iframe.src = `https://www.rainviewer.com/map.html?loc=${latitude},${longitude},${zoom}&layer=radar&sm=1&sn=1`;

  text($('radar-status'), T('radar.embedNotice'));
  armTapToInteract(iframe, $('radar-tap-hint'), T('radar.tapToInteract'));
}

let windyEmbedInitialised = false;

/* Windy's official embed (https://embed.windy.com/) -- a visualization layer
   only, not a data source this app treats as authoritative. Numerical point
   forecasts keep coming from IMGW COSMO / Open-Meteo via /api/summary; this
   iframe never feeds a number back into the page. Like the radar card it
   manages its own refresh and layer/model controls inside the iframe, so this
   runs once per page load rather than polling. */
function initWindyEmbed(data) {
  if (windyEmbedInitialised) return;
  windyEmbedInitialised = true;

  const iframe = $('windy-embed');
  if (!iframe) return;

  const { latitude, longitude } = data.location;
  const zoom = 8;
  const params = new URLSearchParams({
    lat: String(latitude),
    lon: String(longitude),
    detailLat: String(latitude),
    detailLon: String(longitude),
    zoom: String(zoom),
    level: 'surface',
    overlay: 'clouds', // most useful default for event planning / astronomy
    product: 'ecmwf',
    menu: '',
    message: 'true',
    marker: 'true',
    calendar: 'now',
    pressure: '',
    type: 'map',
    location: 'coordinates',
    detail: '',
    metricWind: 'm/s',
    metricTemp: '°C',
    radarRange: '-1',
  });
  iframe.src = `https://embed.windy.com/embed2.html?${params}`;
  // A network-level failure (DNS, connection refused) fires 'error' even on a
  // cross-origin iframe; an HTTP-level one inside Windy's own page will not,
  // but that is Windy's page to report, not this one's -- the rest of the
  // site never depends on this card either way.
  iframe.addEventListener('error', () => text($('windy-status'), T('map.unavailable')), {
    once: true,
  });

  text($('windy-status'), T('map.embedNotice'));
  armTapToInteract(iframe, $('windy-tap-hint'), T('map.tapToInteract'));
}

/* ------------------------------------------------- site load & the notices */

/* The server puts its load on every /api/ response, so knowing that the site is
   busy costs no request of its own. Held here so a language switch can redraw
   the notice without waiting for the next refresh. */
let siteLoad = 'normal';

function readSiteLoad(response) {
  const level = response.headers.get('X-Site-Load');
  // Absent when the operator turned counting off, or when a proxy strips it.
  siteLoad = level === 'busy' || level === 'crowded' ? level : 'normal';
  renderLoadNotice();
}

function renderLoadNotice() {
  const box = $('load-notice');
  if (!box) return;
  box.hidden = siteLoad === 'normal';
  box.classList.toggle('crowded', siteLoad === 'crowded');
  if (!box.hidden) text(box, T(`load.${siteLoad}`));
}

/* ------------------------------------------------------------- preferences */

function initPreferences() {
  for (const button of $('lang-switch').children) {
    button.addEventListener('click', () => {
      EFW_I18N.setLang(button.dataset.lang);
      syncToggles();
      load(); // generated text comes from the API, so refetch in the new language
    });
  }

  // Units, clock and wind are conversions of what we already hold, so they
  // redraw rather than refetch.
  for (const [id, apply] of [
    ['unit-switch', (button) => EFW_I18N.setUnit(button.dataset.unit)],
    ['clock-switch', (button) => EFW_I18N.setClock(button.dataset.clock)],
    ['wind-switch', (button) => EFW_I18N.setWind(button.dataset.wind)],
  ]) {
    for (const button of $(id).children) {
      button.addEventListener('click', () => {
        apply(button);
        syncToggles();
        if (latest) render(latest);
      });
    }
  }
  syncToggles();
}

function syncToggles() {
  for (const button of $('lang-switch').children) {
    button.classList.toggle('active', button.dataset.lang === EFW_I18N.getLang());
  }
  for (const button of $('unit-switch').children) {
    button.classList.toggle('active', button.dataset.unit === EFW_I18N.getUnit());
  }
  for (const button of $('clock-switch').children) {
    button.classList.toggle('active', button.dataset.clock === EFW_I18N.getClock());
  }
  for (const button of $('wind-switch').children) {
    button.classList.toggle('active', button.dataset.wind === EFW_I18N.getWind());
  }
  EFW_I18N.apply();
  renderLoadNotice(); // its text is set in JS, so apply() cannot reach it
}

/* ------------------------------------------------------------------- load */

function render(data) {
  if (data.bands) EFW.setBands(data.bands);

  charts = [];
  conditionsByTime = new Map((data.fsi_series || []).map((entry) => [entry.time, entry]));
  subLabels = labelsFor(data);

  renderWarnings(data.warnings);
  renderFSI(data);

  const timeline = $('timeline');
  const outlook = EFW.outlook(data.fsi_series, 24, PAST_CONTEXT_HOURS);
  EFW.renderStrip(timeline, outlook, {
    labelEvery: 2,
    emptyLabel: T('days.noData'),
    warnings: EFW.warningRanges(data.warnings),
    ...registerChart('timeline', timeline, ensureDetail('hour-detail', timeline), outlook),
  });

  renderLegend($('legend-days'));
  renderNow(data.current, data.fsi, data.location);
  renderDays(data.daily);
  applySelection(); // a panel left open survives the five-minute refresh

  // The short name, not the full one: "EF30" is what the header has room for,
  // and config.json already carries both. Falls back to the long name so an
  // event that never set a short one still gets a heading rather than a blank.
  const event = data.event || {};
  const shortName = event.short_name || event.name;
  if (shortName) text($('event-name'), shortName);
  text($('where'), data.location.name);
  if (data.site_name) document.title = `${data.site_name}: Main page`;

  // Wording, channel name and link all come from notifications.* in
  // config.json (see service.build_summary), so a fork can point this at its
  // own announcements channel without touching markup.
  const notifications = data.notifications;
  if (notifications) {
    text($('footer-disclaimer'), notifications.disclaimer);
    const link = $('footer-notifications');
    text(link, notifications.channel_name);
    link.href = notifications.telegram_url;
  }

  // The subtitle is for trouble only -- it used to carry "observation HH:MM",
  // which was wrong whenever the station report was missing and the hour came
  // from the forecast instead. The timestamp lives on the index panel, which
  // knows which of the two it is showing.
  //
  // What it says instead is *which* kind of "not live" this is. One line used to
  // cover all of them by asserting the server could not be reached, which was a
  // guess from the payload's age: a phone with a wrong clock, or a proxy holding
  // a response, got told it was offline when it was not, and a server that was
  // reached but had no upstream data behind it said nothing at all.
  const subtitle = $('subtitle');
  let notice = null;
  if (offlineCopyAt) {
    // The service worker handed us its saved copy, so this is certain rather
    // than inferred -- and the age is measured on one clock, this browser's.
    notice = T('app.offlineCopy', { when: EFW_I18N.time(offlineCopyAt) });
  } else if ((data.degraded || []).length) {
    // We reached the server; the server did not reach an upstream source, and
    // has no older copy of it to fall back on either. See _collect in service.py.
    notice = T('app.sourceDown');
  } else if (Date.now() - new Date(data.generated_at).getTime() > STALE_AFTER_MS) {
    notice = T('app.stale', {
      when: EFW_I18N.dateTime(data.generated_at, { day: 'numeric', month: 'short' }),
    });
  }
  subtitle.hidden = !notice;
  subtitle.classList.toggle('stale', Boolean(notice));
  if (notice) text(subtitle, notice);

  const stamp = { day: '2-digit', month: '2-digit', year: 'numeric' };
  const meta = [`${T('footer.updated')} ${EFW_I18N.dateTime(data.generated_at, stamp)}`];
  if (data.forecast_issued) {
    meta.push(`${T('footer.forecastRun')} ${EFW_I18N.dateTime(data.forecast_issued, stamp)}`);
  }
  if (data.degraded.length) meta.push(`⚠️ ${data.degraded.join(', ')}`);
  text($('footer-meta'), meta.join(' · '));
}

async function load() {
  lastLoadAt = Date.now(); // an attempt, so a failure throttles the retry too
  try {
    const response = await EFW_LOADING.track(
      fetch(`/api/summary?lang=${EFW_I18N.getLang()}`, { cache: 'no-store' })
    );
    const storedAt = response.headers.get('X-EFW-Stored-At');
    offlineCopyAt = storedAt ? Number(storedAt) : null;
    // Before the status check: a 429 under a rush is precisely when the visitor
    // most deserves to be told the site is busy rather than broken. A copy out
    // of the cache carries how busy the site was then, which is not news.
    if (!offlineCopyAt) readSiteLoad(response);
    if (!response.ok) throw new Error(`server ${response.status}`);
    const data = await response.json();

    latest = data;

    // Unhide first: the charts measure themselves as they draw -- how sparsely
    // to place the icons, which range labels still fit -- and inside a hidden
    // <main> every one of those measurements comes back zero.
    $('main').hidden = false;
    $('error').hidden = true;
    const skeleton = $('skeleton');
    if (skeleton) skeleton.hidden = true; // the real thing has arrived
    render(data);

    initRadarEmbed(data);
    initWindyEmbed(data);
  } catch (error) {
    console.error('EFW load failed', error);
    const box = $('error');
    box.hidden = false;
    text(box, `${T('app.error')}: ${error.message}. ${T('app.retry')}`);
    $('subtitle').hidden = false; // it only ever says something when it is bad news
    text($('subtitle'), T('app.offline'));
    // Keep the skeleton up: it says "still trying" where a blank page would
    // just look broken, and the error sits above it either way.
    const skeleton = $('skeleton');
    if (skeleton && !latest) skeleton.hidden = false;
  }
}

/* Bar widths, how sparsely the icons are drawn and which range labels still fit
   are all measured at render time, so a resize has to redraw rather than
   reflow. Debounced: a drag fires this continuously.
   Width only: a mobile browser's toolbar hiding/showing while the page is
   dragged fires resize with the width unchanged, and re-rendering then would
   blow away and rebuild the DOM mid-scroll, which the browser reads as the
   page having shrunk and jumps the scroll position to compensate. */
let resizeTimer = null;
let lastResizeWidth = window.innerWidth;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (window.innerWidth === lastResizeWidth) return;
    lastResizeWidth = window.innerWidth;
    latest && render(latest);
  }, 200);
});

/* The offline copy. Registered after load so it never competes with the first
   paint, and failure-tolerant: a private window, an older browser or a plain
   HTTP origin refuses service workers, and all that should cost is the offline
   copy rather than the page. See sw.js. */
function initOffline() {
  if (!('serviceWorker' in navigator)) return;
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch((error) => {
      console.warn('EFW: offline copy unavailable', error);
    });
  });
}

/* A tab nobody is looking at does not need a forecast, and a few thousand
   phones in pockets polling every five minutes is traffic -- ours and our
   upstreams' -- that buys nothing. It catches up the moment the tab comes back. */
function refreshIfDue() {
  // Against our own last attempt, not against the payload's timestamp: a device
  // clock that disagrees with the server's would otherwise decide the data is
  // from the future and never refresh again.
  if (document.hidden || Date.now() - lastLoadAt < REFRESH_MS) return;
  load();
}

EFW_I18N.apply();
initPreferences();
initOffline();
load();
setInterval(refreshIfDue, REFRESH_MS);
document.addEventListener('visibilitychange', refreshIfDue);
