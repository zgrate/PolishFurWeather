/* Frontend strings and user preferences (language, temperature unit).
   Static markup carries data-i18n="key"; dynamic text calls T('key'). */

window.EFW_I18N = (function () {
  const STRINGS = {
    en: {
      'app.heading': 'Weather companion',
      'app.offline': 'Offline',
      'app.error': 'Could not load weather data',
      'app.retry': 'Retrying shortly…',
      /* Three different reasons the page is not live, kept apart on purpose --
         see the subtitle block in app.js. */
      'app.offlineCopy':
        'Unable to fetch services. Last update: {when}.',
      'app.sourceDown':
        'Weather services can\'t be reached right now. Please stay patient!',
      'app.stale': 'Not refreshed since {when}. The conditions may have moved on.',

      /* Says a crowd is here, never what the site runs on -- it is EF's server now. */
      'load.busy': 'Higher traffic than usual. Please stay patient.',
      'load.crowded':
        'Very high traffic on the site. Site might load longer. Please be patient!',

      'fsi.heading': 'Fursuiting Index',
      'fsi.hour': 'forecast hour',
      'fsi.measured': 'measured',
      'fsi.next24': 'Next 24 hours · tap a bar for details',
      'fsi.best': 'Best stretch',
      'fsi.worst': 'Worst stretch',
      'fsi.noBest': 'No good window in the next 24 hours.',
      'fsi.noWorst': 'Nothing to avoid in the next 24 hours.',
      'fsi.peaking': 'peaking at',
      'fsi.dropping': 'dropping to',
      'fsi.hours': 'h',
      'fsi.scoreHeader': 'Score',
      'fsi.explainSummary': 'How is this calculated?',
      'fsi.explainBody':
        'The Index ranks the ability to be in a fursuit outdoors, from 0 to 10. It weighs heat stress from the wet-bulb temperature plus sun load, as well as temperature and wind and forecasted rain. Wet-bulb is the temperature your body can get cool down by sweating, so it reads lower than the air temperature. And the closer the two are, the less sweating helps. Dew point is a separate figure and says how clammy the air feels: Usually above 15 °C is uncomfortable. Official warnings don\'t change the score, leaving you to judge.',
      'fsi.weightsHeading': 'How much each part counts',
      'fsi.weightsNote':
        'Beyond the weighting, heat and rain are also ceilings: the Index is never better than the temperature or rain score below it, so neither of the two can be averaged away by a good day otherwise.',
      'fsi.weightsAlt': 'Share of the score carried by each part: {parts}.',

      'band.excellent': 'Excellent',
      'band.good': 'Good',
      'band.fair': 'Fair',
      'band.poor': 'Poor',
      'band.bad': 'Bad',

      'now.heading': 'Right now',
      'now.conditions': 'Conditions',
      'now.temperature': 'Temperature',
      'now.wetbulb': 'Wet-bulb',
      'now.dewpoint': 'Dew point',
      'now.humidity': 'Humidity',
      'now.wind': 'Wind',
      'now.gusts': 'Gusts',
      'now.rain1h': 'Rain (last hour)',
      'now.pressure': 'Pressure',

      'info.about': 'What does {term} mean?',
      'info.wetbulb':
        'Wet-bulb temperature is the coolest your body can get by sweating. It is always lower than the air temperature, but the closer the two numbers are, the less sweating still helps you, and that is the part a fursuit has to cope with.',
      'info.dewpoint':
        'Dew point says how much moisture the air is carrying. Below about 12 °C the air feels dry, from around 15 °C it starts to feel uncomfortable.',

      'hour.rain': 'Rain (this hour)',
      'hour.rainChance': 'Chance of rain',
      'hour.close': 'Close',
      'hour.past': 'This hour has already passed.',
      'hour.now': 'Right now',
      'hour.at': 'At {time}',
      'hour.backToNow': 'tap the bar again for now',

      'days.heading': 'Next days',
      'days.best': 'Best',
      'days.worst': 'Worst',
      'days.average': 'Daytime average',
      'days.scoreUnit': 'FSI score',
      'days.partial': 'Only {n} h of forecast available.',
      'days.noData': 'No data',

      'warnings.more': '+{n} more',
      'warnings.advance': 'ADVANCE NOTICE',
      'warnings.advanceNote': 'Advance notice: possible severe weather, not yet a warning in force.',
      'radar.heading': 'Rain radar',
      'radar.embedNotice': 'Radar: RainViewer',
      'radar.tapToInteract': 'Tap to interact',

      'map.heading': 'Interactive forecast map',
      'map.embedNotice': 'Interactive forecast map: Windy',
      'map.tapToInteract': 'Tap to interact',
      'map.unavailable': 'Interactive forecast map is currently unavailable.',

      /* Species names and levels for the point pollen reading on the ConOps
         board (display.js) -- CAMS, via Open-Meteo. No hazel key: CAMS has no
         hazel product at all, so the site never asks for one. */
      'pollen.alder': 'Alder',
      'pollen.birch': 'Birch',
      'pollen.grasses': 'Grasses',
      'pollen.ragweed': 'Ragweed',
      'pollen.level.low': 'Low',
      'pollen.level.moderate': 'Moderate',
      'pollen.level.high': 'High',
      'pollen.level.very_high': 'Very high',

      'footer.updated': 'Updated',
      'footer.forecastRun': 'Forecast run',
      'footer.data': 'Weather data',
      'footer.mapData': 'Map data',
      'footer.display': 'ConOps display',
      'footer.api': 'Weather API',
      'footer.builtBy': 'Built by',
      'footer.source': 'github',
      'footer.polishVersion': 'Polish version by',
      'footer.units': 'Units',
      'footer.clock': 'Time',
      'footer.wind': 'Wind',
      'footer.privacy': 'Privacy',
      'footer.feedback': 'Feedback',
      'lang.label': 'Language',
      'display.allClear': 'No active warnings',
      'display.warnings': 'Active warnings',
      'display.next18': 'Next 18 hours',
    },

    de: {
      'app.heading': 'Wetterbegleiter',
      'app.offline': 'Offline',
      'app.error': 'Wetterdaten konnten nicht geladen werden',
      'app.retry': 'Neuer Versuch in Kürze…',
      'app.offlineCopy':
        'Die Services können gerade nicht erreicht werden. Du siehst die Vorhersage von {when}.',
      'app.sourceDown':
        'Gerade sind die Wetter-Services nicht erreichbar. Bitte geduldig bleiben!',
      'app.stale': 'Seit {when} nicht aktualisiert. Die Lage kann sich geändert haben.',

      'load.busy': 'Gerade sind viele Leute auf der Seite, das Laden kann etwas dauern.',
      'load.crowded':
        'Hohe Auslastung der Seite. Die Seite kann länger laden als gewohnt. Bitte bleib geduldig!',

      'fsi.heading': 'Fursuiting Index',
      'fsi.hour': 'Vorhersagestunde',
      'fsi.measured': 'gemessen',
      'fsi.next24': 'Nächste 24 Stunden · Balken antippen für Details',
      'fsi.best': 'Bester Zeitraum',
      'fsi.worst': 'Ungünstigster Zeitraum',
      'fsi.noBest': 'Kein guter Zeitraum in den nächsten 24 Stunden.',
      'fsi.noWorst': 'Nichts zu vermeiden in den nächsten 24 Stunden.',
      'fsi.peaking': 'Bestwert',
      'fsi.dropping': 'Tiefstwert',
      'fsi.hours': 'Std.',
      'fsi.scoreHeader': 'Punkte',
      'fsi.explainSummary': 'Wie wird der Index berechnet?',
      'fsi.explainBody':
        'Der Index bewertet von 0 bis 10, wie angenehm und sicher es im Fursuit draußen ist. Er gewichtet die Hitzebelastung aus der Feuchtkugeltemperatur und der Sonneneinstrahlung, zusammen mit Wind und Niederschlagsvorhersage. Die Feuchtkugeltemperatur ist eine Temperatur, den dein Körper durchs Schwitzen  minimal erreichen kann. Sie liegt unter der echten Lufttemperatur, und je näher beide beieinander liegen, desto weniger bringt das Schwitzen. Der Taupunkt ist eine eigene Größe und sagt, wie schwül sich die Luft anfühlt: ab etwa 15 °C wird es klamm. Amtliche Warnungen verändern den Wert nicht: Sie sind über den betroffenen Stunden an den Balken markiert, damit du sie selbst einschätzen kannst.',
      'fsi.weightsHeading': 'Wie sehen die Gewichtungen aus?',
      'fsi.weightsNote':
        'Über die Gewichtung hinaus sind Hitze und Regen auch Obergrenzen: Der Index ist nie besser als der Wert für Temperatur oder Regen darunter, keiner von beiden lässt sich also von einem sonst guten Tag wegmitteln.',
      'fsi.weightsAlt': 'Anteil der einzelnen Faktoren am Gesamtwert: {parts}.',

      'band.excellent': 'Ausgezeichnet',
      'band.good': 'Gut',
      'band.fair': 'Okay',
      'band.poor': 'Vorsicht',
      'band.bad': 'Kritisch',

      'now.heading': 'Jetzt gerade',
      'now.conditions': 'Wetterlage',
      'now.temperature': 'Temperatur',
      'now.wetbulb': 'Feuchtkugel\u00ADtemperatur',
      'now.dewpoint': 'Taupunkt',
      'now.humidity': 'Luftfeuchte',
      'now.wind': 'Wind',
      'now.gusts': 'Böen',
      'now.rain1h': 'Regen (letzte Stunde)',
      'now.pressure': 'Luftdruck',

      'info.about': 'Was bedeutet {term}?',
      'info.wetbulb':
        'Die Feuchtkugeltemperatur ist eine Temperatur, den dein Körper durchs Schwitzen minimal erreichen kann. Sie liegt unter der echten Lufttemperatur, und je näher beide beieinander liegen, desto weniger bringt das Schwitzen',
      'info.dewpoint':
        'Der Taupunkt ist eine eigene Größe und sagt, wie schwül sich die Luft anfühlt: ab etwa 15 °C wird es klamm.',

      'hour.rain': 'Regen (diese Stunde)',
      'hour.rainChance': 'Regenwahrscheinlichkeit',
      'hour.close': 'Schließen',
      'hour.past': 'Diese Stunde ist bereits vorbei.',
      'hour.now': 'Jetzt gerade',
      'hour.at': 'Um {time}',
      'hour.backToNow': 'Balken erneut antippen für jetzt',

      'days.heading': 'Nächste Tage',
      'days.best': 'Beste Zeit',
      'days.worst': 'Schlechteste Zeit',
      'days.average': 'Tagesdurchschnitt',
      'days.scoreUnit': 'FSI-Punkte',
      'days.partial': 'Erst {n} Std. Vorhersage verfügbar.',
      'days.noData': 'Keine Daten',

      'warnings.more': '+{n} weitere',
      'warnings.advance': 'VORABINFORMATION',
      'warnings.advanceNote': 'Vorabinformation: mögliches Unwetter, noch keine amtliche Warnung.',
      'radar.heading': 'Regenradar',
      'radar.embedNotice': 'Radar: RainViewer',
      'radar.tapToInteract': 'Zum Interagieren tippen',

      'map.heading': 'Interaktive Vorhersagekarte',
      'map.embedNotice': 'Interaktive Vorhersagekarte: Windy',
      'map.tapToInteract': 'Zum Interagieren tippen',
      'map.unavailable': 'Die interaktive Vorhersagekarte ist derzeit nicht verfügbar.',

      'pollen.alder': 'Erle',
      'pollen.birch': 'Birke',
      'pollen.grasses': 'Gräser',
      'pollen.ragweed': 'Ambrosia',
      'pollen.level.low': 'Gering',
      'pollen.level.moderate': 'Mäßig',
      'pollen.level.high': 'Hoch',
      'pollen.level.very_high': 'Sehr hoch',

      'footer.updated': 'Aktualisiert',
      'footer.forecastRun': 'Vorhersagelauf',
      'footer.data': 'Wetterdaten',
      'footer.mapData': 'Kartendaten',
      'footer.display': 'ConOps-Anzeige',
      'footer.api': 'Wetter-API',
      'footer.builtBy': 'Gebaut von',
      'footer.source': 'github',
      'footer.polishVersion': 'Polnische Version von',
      'footer.units': 'Einheiten',
      'footer.clock': 'Uhrzeit',
      'footer.wind': 'Wind',
      'footer.privacy': 'Datenschutz',
      'footer.feedback': 'Feedback',
      'lang.label': 'Sprache',
      'display.allClear': 'Keine aktiven Warnungen',
      'display.warnings': 'Aktive Warnungen',
      'display.next18': 'Nächste 18 Stunden',
    },

    pl: {
      'app.heading': 'Aplikacja pogodowa dla Fursuitingu',
      'app.offline': 'Offline',
      'app.error': 'Nie udało się wczytać danych pogodowych',
      'app.retry': 'Ponowna próba wkrótce…',
      'app.offlineCopy':
        'Nie można połączyć się z serwisami. Ostatnia aktualizacja: {when}.',
      'app.sourceDown':
        'Serwisy pogodowe są obecnie niedostępne. Prosimy o cierpliwość!',
      'app.stale': 'Brak aktualizacji od {when}. Warunki mogły się zmienić.',

      'load.busy': 'Większy ruch niż zwykle. Prosimy o cierpliwość.',
      'load.crowded':
        'Bardzo duży ruch na stronie. Wczytywanie może potrwać dłużej. Prosimy o cierpliwość!',

      'fsi.heading': 'Wskaźnik Fursuitingu',
      'fsi.hour': 'godzina prognozy',
      'fsi.measured': 'zmierzone',
      'fsi.next24': 'Najbliższe 24 godziny · dotknij słupek, by zobaczyć szczegóły',
      'fsi.best': 'Najlepszy przedział',
      'fsi.worst': 'Najgorszy przedział',
      'fsi.noBest': 'Brak dobrego przedziału w najbliższych 24 godzinach.',
      'fsi.noWorst': 'Nie ma czego unikać w najbliższych 24 godzinach.',
      'fsi.peaking': 'szczyt',
      'fsi.dropping': 'spadek do',
      'fsi.hours': 'godz.',
      'fsi.scoreHeader': 'Wynik',
      'fsi.explainSummary': 'Jak jest to obliczane?',
      'fsi.explainBody':
        'Wskaźnik ocenia od 0 do 10, na ile komfortowe i bezpieczne jest przebywanie na zewnątrz w fursuicie. Uwzględnia obciążenie cieplne wynikające z temperatury termometru zwilżonego oraz nasłonecznienia, a także temperaturę, wiatr i prognozowany deszcz. Temperatura termometru zwilżonego to najniższa temperatura, do jakiej organizm może się ochłodzić poprzez pocenie, więc jest niższa od temperatury powietrza. Im bliżej siebie są te dwie wartości, tym mniej pomaga pocenie. Punkt rosy to osobna wartość, która mówi, jak duszne jest powietrze: zwykle powyżej 15°C robi się nieprzyjemnie. Oficjalne ostrzeżenia nie zmieniają wyniku, zostawiając ocenę tobie.',
      'fsi.weightsHeading': 'Jak liczy się każda część',
      'fsi.weightsNote':
        'Poza wagami istnieją inne ograniczenia, które korygują wskaźnik. Na przykład niebezpieczny upał (>36°C) ustawia wskaźnik na 0.',
      'fsi.weightsAlt': 'Udział poszczególnych czynników w wyniku: {parts}.',

      'band.excellent': 'Doskonałe',
      'band.good': 'Dobre',
      'band.fair': 'Znośne',
      'band.poor': 'Słabe',
      'band.bad': 'Złe',

      'now.heading': 'Teraz',
      'now.conditions': 'Warunki',
      'now.temperature': 'Temperatura',
      'now.wetbulb': 'Termometr zwilżony',
      'now.dewpoint': 'Punkt rosy',
      'now.humidity': 'Wilgotność',
      'now.wind': 'Wiatr',
      'now.gusts': 'Porywy',
      'now.rain1h': 'Deszcz (ostatnia godzina)',
      'now.pressure': 'Ciśnienie',

      'info.about': 'Co oznacza {term}?',
      'info.wetbulb':
        'Temperatura termometru zwilżonego to najniższa temperatura, do jakiej organizm może się ochłodzić poprzez pocenie. Jest zawsze niższa od temperatury powietrza, ale im bliżej siebie są te wartości, tym mniej pomaga pocenie — i z tym musi radzić sobie fursuit.',
      'info.dewpoint':
        'Punkt rosy mówi, ile wilgoci niesie powietrze. Poniżej około 12°C powietrze wydaje się suche, od około 15°C zaczyna być nieprzyjemnie.',

      'hour.rain': 'Deszcz (ta godzina)',
      'hour.rainChance': 'Szansa na deszcz',
      'hour.close': 'Zamknij',
      'hour.past': 'Ta godzina już minęła.',
      'hour.now': 'Teraz',
      'hour.at': 'O {time}',
      'hour.backToNow': 'dotknij słupek ponownie, aby wrócić do teraz',

      'days.heading': 'Kolejne dni',
      'days.best': 'Najlepszy',
      'days.worst': 'Najgorszy',
      'days.average': 'Średnia dzienna',
      'days.scoreUnit': 'Wynik FSI',
      'days.partial': 'Dostępna prognoza tylko na {n} godz.',
      'days.noData': 'Brak danych',

      'warnings.more': '+{n} więcej',
      'warnings.advance': 'INFORMACJA WSTĘPNA',
      'warnings.advanceNote': 'Informacja wstępna: możliwa groźna pogoda, jeszcze nie ostrzeżenie.',
      'radar.heading': 'Radar opadów',
      'radar.embedNotice': 'Radar: RainViewer',
      'radar.tapToInteract': 'Dotknij, aby wejść w interakcję',

      'map.heading': 'Interaktywna mapa prognozy',
      'map.embedNotice': 'Interaktywna mapa prognozy: Windy',
      'map.tapToInteract': 'Dotknij, aby wejść w interakcję',
      'map.unavailable': 'Interaktywna mapa prognozy jest obecnie niedostępna.',

      'pollen.alder': 'Olcha',
      'pollen.birch': 'Brzoza',
      'pollen.grasses': 'Trawy',
      'pollen.ragweed': 'Ambrozja',
      'pollen.level.low': 'Niskie',
      'pollen.level.moderate': 'Umiarkowane',
      'pollen.level.high': 'Wysokie',
      'pollen.level.very_high': 'Bardzo wysokie',

      'footer.updated': 'Zaktualizowano',
      'footer.forecastRun': 'Przebieg prognozy',
      'footer.data': 'Dane pogodowe',
      'footer.mapData': 'Dane mapy',
      'footer.display': 'Wyświetlacz ConOps',
      'footer.api': 'API pogodowe',
      'footer.builtBy': 'Stworzone przez',
      'footer.source': 'github',
      'footer.polishVersion': 'Polska wersja przez',
      'footer.units': 'Jednostki',
      'footer.clock': 'Czas',
      'footer.wind': 'Wiatr',
      'footer.privacy': 'Prywatność',
      'footer.feedback': 'Opinia',
      'lang.label': 'Język',
      'display.allClear': 'Brak aktywnych ostrzeżeń',
      'display.warnings': 'Aktywne ostrzeżenia',
      'display.next18': 'Najbliższe 18 godzin',
    },
  };

  const LANG_KEY = 'efw.lang';
  const UNIT_KEY = 'efw.unit';
  const CLOCK_KEY = 'efw.clock';
  const WIND_KEY = 'efw.wind';

  /* Wind arrives in km/h and stays that way in the payload; this is only how it
     is written down. mph for the Americans, knots because a fair number of
     people read wind that way and neither of the other two means anything to
     them. The label is the unit's own name in both languages -- "km/h" is not
     translated anywhere it is spoken. */
  const WIND_UNITS = {
    kmh: { factor: 1, label: 'km/h' },
    mph: { factor: 0.621371, label: 'mph' },
    kn: { factor: 0.539957, label: 'kn' },
  };

  const params = new URLSearchParams(location.search);

  let forced = null;

  function force(lang) {
    if (STRINGS[lang]) forced = lang;
  }

  function getLang() {
    if (forced) return forced;
    const asked = params.get('lang');
    if (asked && STRINGS[asked]) return asked;
    const stored = localStorage.getItem(LANG_KEY);
    if (stored && STRINGS[stored]) return stored;
    const browser = (navigator.language || 'en').toLowerCase();
    if (browser.startsWith('de')) return 'de';
    if (browser.startsWith('pl')) return 'pl';
    return 'en';
  }

  function setLang(lang) {
    if (STRINGS[lang]) localStorage.setItem(LANG_KEY, lang);
  }

  function getUnit() {
    const asked = (params.get('units') || '').toUpperCase();
    if (asked === 'F' || asked === 'C') return asked;
    return localStorage.getItem(UNIT_KEY) === 'F' ? 'F' : 'C';
  }

  function setUnit(unit) {
    localStorage.setItem(UNIT_KEY, unit === 'F' ? 'F' : 'C');
  }

  function getWind() {
    const asked = (params.get('wind') || '').toLowerCase();
    if (WIND_UNITS[asked]) return asked;
    const stored = localStorage.getItem(WIND_KEY);
    return WIND_UNITS[stored] ? stored : 'kmh';
  }

  function setWind(unit) {
    localStorage.setItem(WIND_KEY, WIND_UNITS[unit] ? unit : 'kmh');
  }

  function getClock() {
    const asked = params.get('clock');
    if (asked === '12' || asked === '24') return asked;
    return localStorage.getItem(CLOCK_KEY) === '12' ? '12' : '24';
  }

  function setClock(clock) {
    localStorage.setItem(CLOCK_KEY, clock === '12' ? '12' : '24');
  }

  /** Time-of-day options honouring the clock preference, plus anything extra. */
  function clockOptions(extra) {
    const half = getClock() === '12';
    return {
      // "09:00 PM" reads as a mistake; the 12-hour clock drops the padding.
      hour: half ? 'numeric' : '2-digit',
      minute: '2-digit',
      hour12: half,
      ...extra,
    };
  }

  /** A time of day, e.g. "21:00" or "9:00 pm". */
  function time(value, extra) {
    return new Date(value).toLocaleTimeString(locale(), clockOptions(extra));
  }

  /** A date and a time, for the footer and the model card. */
  function dateTime(value, extra) {
    return new Date(value).toLocaleString(locale(), clockOptions(extra));
  }

  /** A calendar date with no time of day.
      For a figure that covers a whole day, an hour on the label would be an
      invention, and "2 Aug, 02:00" reads as a measurement taken at two in the
      morning. */
  function dateOnly(value, extra) {
    return new Date(value).toLocaleDateString(locale(), {
      day: 'numeric',
      month: 'short',
      ...extra,
    });
  }

  /** Parse a bare YYYY-MM-DD as local noon.
      `new Date('2026-08-02')` is UTC midnight, which is still 1 August for any
      reader west of Greenwich -- the whole label would name the wrong day.
      Noon is far enough from either edge that no timezone or DST jump can move
      the date. */
  function dayStart(iso) {
    return typeof iso === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(iso)
      ? new Date(`${iso}T12:00:00`)
      : new Date(iso);
  }

  function T(key, vars) {
    const table = STRINGS[getLang()] || STRINGS.en;
    let value = table[key] ?? STRINGS.en[key] ?? key;
    if (vars) for (const [k, v] of Object.entries(vars)) value = value.replace(`{${k}}`, v);
    return value;
  }

  /** Convert a Celsius value for display, honouring the unit preference. */
  function temp(celsius, digits = 1) {
    if (celsius === null || celsius === undefined) return '-';
    const value = getUnit() === 'F' ? celsius * 1.8 + 32 : celsius;
    return `${value.toFixed(digits)} °${getUnit()}`;
  }

  /** Convert a km/h value for display, honouring the wind unit preference.
   *
   * Whole numbers throughout: the forecast does not know the wind to a tenth of
   * a mile an hour, and writing it that way would claim it did.
   */
  function wind(kmh, { unit = true } = {}) {
    if (kmh === null || kmh === undefined) return '-';
    const chosen = WIND_UNITS[getWind()] || WIND_UNITS.kmh;
    const value = Math.round(kmh * chosen.factor);
    return unit ? `${value} ${chosen.label}` : `${value}`;
  }

  /** The unit's own name, for a row that writes several numbers before it. */
  const windUnit = () => (WIND_UNITS[getWind()] || WIND_UNITS.kmh).label;

  /** Same, but without a space. for compact places like the day rows. */
  function tempShort(celsius) {
    if (celsius === null || celsius === undefined) return '-';
    const value = getUnit() === 'F' ? celsius * 1.8 + 32 : celsius;
    return `${Math.round(value)}°`;
  }

  /** Replace the text of every element carrying data-i18n. */
  function apply(root = document) {
    for (const el of root.querySelectorAll('[data-i18n]')) {
      el.textContent = T(el.dataset.i18n);
    }
    document.documentElement.lang = getLang();
  }

  const LOCALES = { de: 'de-DE', pl: 'pl-PL' };
  const locale = () => LOCALES[getLang()] || 'en-GB';

  return {
    T,
    apply,
    force,
    getLang,
    setLang,
    getUnit,
    setUnit,
    getClock,
    setClock,
    getWind,
    setWind,
    temp,
    wind,
    windUnit,
    tempShort,
    time,
    dateOnly,
    dayStart,
    dateTime,
    locale,
  };
})();
