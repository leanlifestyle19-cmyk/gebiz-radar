const CACHE = 'gebizradar-20260715a';  // M1: BUMP on EVERY index.html deploy

// M16: list every external API host here. None yet — data is same-origin.
const BYPASS = [];

self.addEventListener('install', e => {
  // M4: individual cache.put, never addAll()
  e.waitUntil(
    caches.open(CACHE)
      .then(c => fetch('./index.html').then(res => c.put('./index.html', res)))
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (BYPASS.some(h => url.hostname.includes(h))) return;

  // M5: app shell by request MODE, not URL path
  if (e.request.mode === 'navigate') {
    e.respondWith(caches.match('./index.html').then(r => r || fetch(e.request)));
    return;
  }

  // awards.json is NETWORK-FIRST — cache-first would serve stale tender data
  // forever. Fresh copy goes into the cache as offline fallback.
  if (url.pathname.endsWith('awards.json')) {
    e.respondWith(
      fetch(e.request).then(res => {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy));
        return res;
      }).catch(() => caches.match(e.request))
    );
    return;
  }

  // everything else: cache-first, network fallback, cache the result
  e.respondWith(
    caches.match(e.request).then(r => r || fetch(e.request).then(res => {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(e.request, copy));
      return res;
    }))
  );
});