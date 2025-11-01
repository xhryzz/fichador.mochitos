const CACHE_NAME = 'fichador-v1';
const urlsToCache = [
  '/',
  '/static/css/style.css',
  '/static/js/app.js',
  '/dashboard',
  '/schedule',
  '/records'
];

// Instalación del Service Worker
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(urlsToCache))
  );
});

// Activación y limpieza de cachés antiguas
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheName !== CACHE_NAME) {
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
});

// Estrategia de caché: Network First, falling back to cache
self.addEventListener('fetch', event => {
  event.respondWith(
    fetch(event.request)
      .then(response => {
        // Clonar la respuesta
        const responseToCache = response.clone();
        caches.open(CACHE_NAME)
          .then(cache => {
            cache.put(event.request, responseToCache);
          });
        return response;
      })
      .catch(() => {
        return caches.match(event.request);
      })
  );
});

// Escuchar mensajes para programar notificaciones
self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SCHEDULE_NOTIFICATION') {
    const { title, body, delay, tag } = event.data;
    
    setTimeout(() => {
      self.registration.showNotification(title, {
        body: body,
        icon: '/static/icon-192x192.png',
        badge: '/static/badge-72x72.png',
        tag: tag,
        requireInteraction: true,
        vibrate: [200, 100, 200],
        actions: [
          { action: 'fichar', title: 'Fichar ahora' },
          { action: 'close', title: 'Cerrar' }
        ]
      });
    }, delay);
  }
});

// Manejar clicks en las notificaciones
self.addEventListener('notificationclick', event => {
  event.notification.close();
  
  if (event.action === 'fichar') {
    event.waitUntil(
      clients.openWindow('/dashboard')
    );
  }
});