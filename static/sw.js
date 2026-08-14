/* Offline copy of the app shell and the last weather payload.

   Why this exists: the site is read on phones inside a convention centre where
   a few thousand people share the same cell towers, and it is served from one
   machine on a home uplink. A cached shell opens the page without waiting for
   either of those, and a cached summary opens it with real numbers instead of
   an error box. Every load served from here is also one that never reaches the
   uplink, which is the same problem app/presence.py exists to apologise for.

   Network-first throughout, deliberately. The pages and their scripts are
   versioned together -- a cached app.js beside a fresh index.html is exactly the
   breakage the no-cache headers in app/main.py were added to prevent -- so the
   network always gets first refusal and the cache is a fallback, never a
   shortcut past a deploy. */

/* Bumped when the stored format changes: v2 stamps each copy with the time it
   was kept, and a v1 entry without that stamp would be read as live data. */
const CACHE = 'efw-v2';

/* Enough to draw the page and say something true. Radar frames, model images
   and the video are left out on purpose: they are large, they change
   constantly, and a stale radar picture is worse than a missing one. */
const SHELL = [
  '/',
  '/style.css',
  '/app.js',
  '/chart.js',
  '/i18n.js',
  '/loading.js',
  '/vendor/leaflet.js',
  '/vendor/leaflet.css',
];

/* Paths never worth keeping a copy of.

   /api/load is the live "is the site busy" signal and is served no-store on
   purpose -- a stale "all quiet" is worse than no answer. The image endpoints
   and the media directory are heavy and short-lived. */
const SKIP = [/^\/api\/load$/, /^\/api\/(radar|model|pollen)\.png$/, /^\/media\//];

/* How long to wait for the network before reaching for the cache. Long enough
   that a merely slow connection still wins and the reader gets fresh data,
   short enough that a dead one does not hold the page hostage. */
const NETWORK_TIMEOUT_MS = 3500;

self.addEventListener('install', (event) => {
  // addAll is all-or-nothing, so a half-downloaded shell never becomes the
  // fallback: either this generation cached cleanly or the previous one stands.
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  // Someone else's origin is someone else's to cache: the basemap tiles come
  // back opaque, so we could not tell a good one from an error page, and they
  // would swamp the store.
  if (url.origin !== self.location.origin) return;
  if (SKIP.some((pattern) => pattern.test(url.pathname))) return;

  event.respondWith(networkFirst(request));
});

/* Stamped onto every copy as it is stored, so the page can tell a cached
   response from a live one and say how old it is. The value is this browser's
   own clock at the moment it was kept, which is the same clock the page later
   compares it against -- a device whose clock is wrong is then still right
   about the age. */
const STORED_AT = 'X-EFW-Stored-At';

async function keep(cache, request, response) {
  const headers = new Headers(response.headers);
  headers.set(STORED_AT, String(Date.now()));
  const body = await response.blob();
  await cache.put(
    request,
    new Response(body, { status: response.status, statusText: response.statusText, headers })
  );
}

/** Give the network first refusal, then fall back to the last good copy. */
async function networkFirst(request) {
  const cache = await caches.open(CACHE);

  const network = fetch(request).then((response) => {
    // Keep whatever arrives even if the race below has already given up on it,
    // so a slow connection still leaves the next load better off than this one.
    // Errors are not worth keeping: a cached 502 would outlive the outage.
    if (response.ok) keep(cache, request, response.clone());
    return response;
  });
  // Claimed below on both paths, but not always in time to count as handled.
  network.catch(() => {});

  try {
    return await withTimeout(network, NETWORK_TIMEOUT_MS);
  } catch (error) {
    const cached = await cache.match(request);
    // Nothing kept: hand back the real network failure rather than one of ours,
    // because the browser's own message says more than "timeout" would.
    return cached || network;
  }
}

function withTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('network timeout')), ms);
    promise.then(resolve, reject).finally(() => clearTimeout(timer));
  });
}
