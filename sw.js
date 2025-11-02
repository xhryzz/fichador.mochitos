// Minimal, safe service worker for PWA + Push
// Bump version to force update
const CACHE_NAME = 'fichador-cache-v2.0.0';

// Cache ONLY immutable/static assets that actually exist
const ASSETS = [
  '/static/css/style.css',
  '/static/css/style.min.css',
  '/static/js/notifications.js',
  '/static/icon-128x128.png',
  '/static/icon-144x144.png',
  '/static/icon-180x180.png',
  '/static/icon-192x192.png',
  '/manifest.json'
];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS).catch((err) => {
        // If any single asset fails, don't abort the entire install.
        console.error('[SW] Cache.addAll error (continuing):', err);
      });
    })
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(keys.map(k => (k !== CACHE_NAME ? caches.delete(k) : Promise.resolve())));
      await self.clients.claim();
    })()
  );
});

// Cache-first for static files; network-first for HTML navigations and API
self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Only handle GET
  if (req.method !== 'GET') return;

  // For same-origin static assets (css/js/images/fonts), use cache-first
  const dest = req.destination;
  const isStatic = ['style', 'script', 'image', 'font'].includes(dest);
  if (url.origin === location.origin && isStatic) {
    event.respondWith(
      caches.match(req).then((cached) => {
        return cached || fetch(req).then((resp) => {
          const respClone = resp.clone();
          caches.open(CACHE_NAME).then((c) => c.put(req, respClone));
          return resp;
        }).catch(() => cached);
      })
    );
    return;
  }

  // For navigations (HTML) and API calls, prefer network
  if (req.mode === 'navigate' || (req.headers.get('accept') || '').includes('text/html')) {
    event.respondWith(
      fetch(req).catch(() => caches.match('/'))
    );
    return;
  }
});

// Handle push events
self.addEventListener('push', (event) => {
  try {
    const data = event.data ? event.data.json() : {};
    const title = data.title || 'Notificación';
    const options = {
      body: data.body || '',
      icon: data.icon || '/static/icon-192x192.png',
      badge: data.badge || '/static/icon-128x128.png',
      data: data.data || {},
      actions: data.actions || []
    };
    event.waitUntil(self.registration.showNotification(title, options));
  } catch (e) {
    // Fallback if body is not JSON
    event.waitUntil(self.registration.showNotification('Notificación', { body: event.data && event.data.text() }));
  }
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = '/dashboard';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientsArr) => {
      const hadWindow = clientsArr.some((w) => {
        if (w.url.includes(targetUrl)) { w.focus(); return true; }
        return false;
      });
      if (!hadWindow) return self.clients.openWindow(targetUrl);
    })
  );
});