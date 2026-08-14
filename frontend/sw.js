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

self.addEventListener('push', (event) => {
  let payload = { title: 'Halves', body: '', url: '/' };
  try {
    payload = { ...payload, ...event.data.json() };
  } catch (e) {
    /* malformed or missing payload -- fall back to the defaults above */
  }
  event.waitUntil(
    self.registration.showNotification(payload.title, {
      body: payload.body,
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      data: { url: payload.url },
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = event.notification.data && event.notification.data.url ? event.notification.data.url : '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ('focus' in client) return client.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});
