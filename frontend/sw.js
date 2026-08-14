// Minimal service worker: caches the static app shell so it installs and
// loads instantly, but never touches API responses -- balance/expense data
// must always come from the network, never a stale cache.
const CACHE_NAME = 'halves-v1';
const STATIC_ASSETS = ['/', '/styles.css', '/app.js', '/icon-192.png', '/icon-512.png'];

const API_PREFIXES = ['/auth', '/users', '/households', '/expenses', '/balances', '/settlements', '/health'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (API_PREFIXES.some((p) => url.pathname.startsWith(p))) return;

  event.respondWith(caches.match(request).then((cached) => cached || fetch(request)));
});
