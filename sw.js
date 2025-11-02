// sw.js — Fichador Mochitos (PWA + Push fiable en iOS)
// Sube la versión para forzar actualización del SW tras un deploy
const CACHE_NAME = 'fichador-cache-v2.0.1';

// Cachea SOLO estáticos que existen de verdad
const ASSETS = [
    '/manifest.json',
    '/static/css/style.css',
    '/static/css/style.min.css',
    '/static/js/notifications.js',
    '/static/icon-72x72.png',
    '/static/icon-128x128.png',
    '/static/icon-144x144.png',
    '/static/icon-180x180.png',
    '/static/icon-192x192.png'
];

self.addEventListener('install', (event) => {
    self.skipWaiting();
    event.waitUntil(
        caches.open(CACHE_NAME).then(async (cache) => {
            try {
                await cache.addAll(ASSETS);
            } catch (err) {
                // Si algún asset falla, no tumbar la instalación del SW.
                // iOS es sensible a fallos en addAll.
                console.error('[SW] Cache.addAll warning:', err);
            }
        })
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        (async () => {
            const keys = await caches.keys();
            await Promise.all(
                keys.map(k => (k !== CACHE_NAME ? caches.delete(k) : Promise.resolve()))
            );
            await self.clients.claim();
        })()
    );
});

// Estrategia simple:
// - Estáticos (css/js/img/font) => cache-first
// - Navegaciones/API => network (sin tocar)
self.addEventListener('fetch', (event) => {
    const req = event.request;
    if (req.method !== 'GET') return;

    const url = new URL(req.url);
    const dest = req.destination;
    const isStatic = ['style', 'script', 'image', 'font'].includes(dest);

    if (url.origin === location.origin && isStatic) {
        event.respondWith(
            caches.match(req).then((cached) => {
                if (cached) return cached;
                return fetch(req).then((resp) => {
                    // Guarda en caché una copia si es 200
                    if (resp && resp.status === 200) {
                        const clone = resp.clone();
                        caches.open(CACHE_NAME).then((c) => c.put(req, clone));
                    }
                    return resp;
                }).catch(() => cached); // si falla red, devuelve caché si había
            })
        );
        return;
    }

    // Para HTML y APIs dejamos pasar (network-first natural).
    // No añadimos fallback aquí para no interferir con auth/login.
});

// Push: muestra notificación aunque el payload no sea JSON
self.addEventListener('push', (event) => {
    let data = {};
    try {
        if (event.data) {
            // Algunos servicios envían string JSON, otros objetos; cubrimos ambas
            const maybeText = event.data.text();
            try {
                data = JSON.parse(maybeText);
            } catch {
                // Si no es JSON, usamos el texto como body
                data = { title: 'Fichador', body: maybeText };
            }
        }
    } catch (e) {
        // iOS puede lanzar si event.data es null
        data = {};
    }

    const title = data.title || 'Fichador';
    const options = {
        body: data.body || '',
        icon: data.icon || '/static/icon-192x192.png',
        badge: data.badge || '/static/icon-72x72.png',
        data: data.data || {},
        actions: Array.isArray(data.actions) && data.actions.length
            ? data.actions
            : [{ action: 'open', title: 'Abrir' }]
    };

    event.waitUntil(self.registration.showNotification(title, options));
});

// Al pulsar la notificación, enfoca/abre el dashboard
self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const targetUrl = '/dashboard';

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clis) => {
            for (const client of clis) {
                // Si ya hay una pestaña de la app abierta, fócus
                if (client.url.includes(targetUrl) || client.url.endsWith('/') || client.url.includes('/dashboard')) {
                    return client.focus();
                }
            }
            // Si no hay, abrir nueva
            return clients.openWindow ? clients.openWindow(targetUrl) : undefined;
        })
    );
});

// (Opcional) Re-suscripción automática si cambiase el endpoint
self.addEventListener('pushsubscriptionchange', async () => {
    // Normalmente no hace falta implementar nada aquí con VAPID estable.
});
