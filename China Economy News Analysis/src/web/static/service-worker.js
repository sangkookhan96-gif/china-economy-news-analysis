'use strict';

const SHELL_CACHE  = 'cni-shell-v1';
const PAGES_CACHE  = 'cni-pages-v1';
const FONTS_CACHE  = 'cni-fonts-v1';

const SHELL_ASSETS = [
  '/static/style.css',
  '/static/main.js',
  '/static/manifest.json',
  '/static/favicon.ico',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/apple-touch-icon.png',
  '/offline',
];

// ── Install: pre-cache app shell ─────────────────────────────────────────────
self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(SHELL_CACHE).then(function(cache) {
      return cache.addAll(SHELL_ASSETS);
    }).then(function() {
      return self.skipWaiting();
    })
  );
});

// ── Activate: remove stale caches ────────────────────────────────────────────
self.addEventListener('activate', function(event) {
  var allowed = [SHELL_CACHE, PAGES_CACHE, FONTS_CACHE];
  event.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(
        keys.filter(function(k) { return !allowed.includes(k); })
            .map(function(k) { return caches.delete(k); })
      );
    }).then(function() {
      return self.clients.claim();
    })
  );
});

// ── Fetch ─────────────────────────────────────────────────────────────────────
self.addEventListener('fetch', function(event) {
  var req = event.request;
  var url = new URL(req.url);

  // Only handle GET requests
  if (req.method !== 'GET') return;

  // Google Fonts — stale-while-revalidate
  if (url.hostname === 'fonts.googleapis.com' ||
      url.hostname === 'fonts.gstatic.com') {
    event.respondWith(staleWhileRevalidate(req, FONTS_CACHE));
    return;
  }

  // Static assets — cache-first
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(cacheFirst(req, SHELL_CACHE));
    return;
  }

  // HTML pages — network-first with offline fallback
  if (req.headers.get('Accept') && req.headers.get('Accept').includes('text/html')) {
    event.respondWith(networkFirst(req, PAGES_CACHE));
    return;
  }
});

// ── Strategies ────────────────────────────────────────────────────────────────

function cacheFirst(req, cacheName) {
  return caches.open(cacheName).then(function(cache) {
    return cache.match(req).then(function(cached) {
      if (cached) return cached;
      return fetch(req).then(function(res) {
        if (res.ok) cache.put(req, res.clone());
        return res;
      });
    });
  });
}

function networkFirst(req, cacheName) {
  return fetch(req).then(function(res) {
    if (res.ok) {
      caches.open(cacheName).then(function(cache) {
        cache.put(req, res.clone());
      });
    }
    return res;
  }).catch(function() {
    return caches.open(cacheName).then(function(cache) {
      return cache.match(req).then(function(cached) {
        return cached || caches.match('/offline');
      });
    });
  });
}

function staleWhileRevalidate(req, cacheName) {
  return caches.open(cacheName).then(function(cache) {
    return cache.match(req).then(function(cached) {
      var fetchPromise = fetch(req).then(function(res) {
        if (res.ok) cache.put(req, res.clone());
        return res;
      });
      return cached || fetchPromise;
    });
  });
}
