// sw.js — Fichador Mochitos (PWA + Web Push fiable)
// Sube la versión para forzar actualización tras cada deploy
const CACHE_NAME = 'fichador-cache-v2.0.2';

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

// ===== Install =====
self.addEventListener('install', (event) => {
    self.skipWaiting();
    event.waitUntil(
        caches.open(CACHE_NAME).then(async (cache) => {
            try {
                await cache.addAll(ASSETS);
            } catch (err) {
                // Si algún asset falla, no tumbar la instalación del SW (iOS es sensible a esto)
                console.error('[SW] Cache.addAll warning:', err);
            }
        })
    );
});

// ===== Activate =====
self.addEventListener('activate', (event) => {
    event.waitUntil(
        (async () => {
            const keys = await caches.keys();
            await Promise.all(keys.map(k => (k !== CACHE_NAME ? caches.delete(k) : Promise.resolve())));
            await self.clients.claim();
        })()
    );
});

// ===== Fetch =====
// - Estáticos (style/script/image/font) => cache-first
// - HTML y APIs => dejamos pasar (network-first natural) para no interferir con auth/login
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
                    if (resp && resp.status === 200) {
                        const clone = resp.clone();
                        caches.open(CACHE_NAME).then((c) => c.put(req, clone));
                    }
                    return resp;
                }).catch(() => cached); // si falla red, devuelve caché si había
            })
        );
    }
});

// ===== Push =====
// Muestra notificación aunque el payload no sea JSON.
// Añadimos timestamp y tag únicos para evitar que el SO agrupe/reemplace notificaciones.
self.addEventListener('push', (event) => {
    let data = {};
    try {
        data = event.data ? event.data.json() : {};
    } catch (e) {
        try {
            const t = event.data ? event.data.text() : '';
            data = t ? { title: 'Fichador', body: t } : {};
        } catch {
            data = {};
        }
    }

    // Defaults sensatos
    const title = data.title || 'Fichador';
    const payloadData = data.data || {};
    if (!payloadData.url) payloadData.url = '/dashboard';

    const options = {
        body: data.body || '',
        icon: data.icon || '/static/icon-192x192.png',
        badge: data.badge || '/static/icon-72x72.png',
        data: payloadData,
        actions: Array.isArray(data.actions) && data.actions.length
            ? data.actions
            : [{ action: 'open', title: 'Abrir fichador' }],
        // Evita coalescing/agrupaciones del sistema
        timestamp: Date.now(),
        tag: `fichador-${payloadData.nid || Date.now()}`
    };

    event.waitUntil(self.registration.showNotification(title, options));
});

// ===== Notification Click =====
// Enfoca una pestaña existente si la hay; si no, abre nueva.
// Respeta data.url si viene del server, fallback a /dashboard.
self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const targetUrl = (event.notification.data && event.notification.data.url) || '/dashboard';

    event.waitUntil((async () => {
        const all = await clients.matchAll({ type: 'window', includeUncontrolled: true });
        const hit = all.find(c =>
            c.url.includes(targetUrl) || c.url.endsWith('/') || c.url.includes('/dashboard')
        );
        if (hit) return hit.focus();
        if (clients.openWindow) return clients.openWindow(targetUrl);
    })());
});

// ===== (Opcional) Cambios de suscripción =====
// Normalmente no hace falta implementar nada con VAPID estable.
self.addEventListener('pushsubscriptionchange', async () => {
    // noop
});
