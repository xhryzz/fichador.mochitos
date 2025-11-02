/* /static/js/notifications.js
   Gestor completo de notificaciones (Web Push + recordatorios locales).
   ➤ Pásame la VAPID pública desde la plantilla:
      - Opción A (recomendada): <script>window.VAPID_PUBLIC_KEY = "{{ vapid_public }}";</script>
      - Opción B: <meta name="vapid-public" content="{{ vapid_public }}">
*/

(function () {
    "use strict";

    // ─────────────────────────────────────────────────────────────────────────────
    // Utilidades
    // ─────────────────────────────────────────────────────────────────────────────
    function base64UrlToUint8Array(base64UrlString) {
        // Acepta base64url o base64 normal
        const pad = "=".repeat((4 - (base64UrlString.length % 4)) % 4);
        const base64 = (base64UrlString.replace(/-/g, "+").replace(/_/g, "/") + pad);
        const raw = atob(base64);
        const output = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; ++i) output[i] = raw.charCodeAt(i);
        return output;
    }

    function todayKey() {
        const d = new Date();
        return d.toISOString().slice(0, 10); // YYYY-MM-DD
    }

    const LocalSeen = {
        get(key) {
            try {
                const all = JSON.parse(localStorage.getItem("nm_seen") || "{}");
                return all[key] || {};
            } catch {
                return {};
            }
        },
        set(key, obj) {
            try {
                const all = JSON.parse(localStorage.getItem("nm_seen") || "{}");
                all[key] = obj;
                localStorage.setItem("nm_seen", JSON.stringify(all));
            } catch {}
        },
    };

    // ─────────────────────────────────────────────────────────────────────────────
    // NotificationManager
    // ─────────────────────────────────────────────────────────────────────────────
    class NotificationManager {
        constructor() {
            this.swRegistration = null;
            this.checkIntervals = new Map();
            this.vapidPublicKey =
                window.VAPID_PUBLIC_KEY ||
                (document.querySelector('meta[name="vapid-public"]')?.content || "").trim();

            // Botones opcionales en la página de /notifications (si existen)
            document.addEventListener("click", (ev) => {
                const el = ev.target.closest("[data-nm-action]");
                if (!el) return;
                const action = el.getAttribute("data-nm-action");
                if (action === "enable") this.subscribePush().catch(console.error);
                if (action === "disable") this.unsubscribePush().catch(console.error);
                if (action === "test") this.sendTest().catch(console.error);
            });
        }

        // ── Init: registra SW, pide permiso de notificaciones si procede ──────────
        async init() {
            if (!("serviceWorker" in navigator)) {
                console.warn("[NM] Este navegador no soporta Service Workers.");
                return;
            }

            try {
                // Registra y espera a que esté listo (mejor para PushManager)
                await navigator.serviceWorker.register("/sw.js", { scope: "/" });
                this.swRegistration = await navigator.serviceWorker.ready;
                // iOS Web Push requiere PWA instalada + permiso explícito
                if ("Notification" in window && Notification.permission === "default") {
                    try {
                        await Notification.requestPermission();
                    } catch {}
                }
            } catch (e) {
                console.error("[NM] Error registrando SW:", e);
            }

            // Autoarranque del monitor local (no sustituye a Web Push; es un extra)
            try {
                const [schedulesRes, activeRes] = await Promise.all([
                    fetch("/api/schedules", { credentials: "same-origin" }),
                    fetch("/api/active_record", { credentials: "same-origin" }),
                ]);
                if (schedulesRes.ok && activeRes.ok) {
                    const schedules = await schedulesRes.json();
                    const activeInfo = await activeRes.json();
                    this.startScheduleMonitoring(schedules, !!activeInfo.has_active_record);
                }
            } catch (e) {
                // Silencioso: si no estamos logueados devolverá 302/HTML
            }
        }

        // ── Suscribirse a Web Push (VAPID) ────────────────────────────────────────
        async subscribePush() {
            if (!this.swRegistration) throw new Error("SW no inicializado.");
            if (!("PushManager" in window)) throw new Error("PushManager no soportado.");
            if (!this.vapidPublicKey) throw new Error("Falta VAPID_PUBLIC_KEY en la página.");

            // iOS: solo funciona si la app está instalada como PWA
            // (No forzamos nada aquí: el intento fallará si no está instalada)
            const perm = "Notification" in window ? Notification.permission : "denied";
            if (perm !== "granted") {
                const ask = await Notification.requestPermission();
                if (ask !== "granted") throw new Error("Permiso de notificaciones denegado.");
            }

            // ¿Ya suscrito?
            const existing = await this.swRegistration.pushManager.getSubscription();
            if (existing) {
                // Asegura que backend conoce esta suscripción (p.ej. si reinstalaste)
                await this._sendSubscribeToServer(existing);
                console.log("[NM] Ya estabas suscrito. Refrescado en servidor.");
                return existing;
            }

            // Nueva suscripción
            const appServerKey = base64UrlToUint8Array(this.vapidPublicKey);
            const sub = await this.swRegistration.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: appServerKey,
            });
            await this._sendSubscribeToServer(sub);
            console.log("[NM] Suscripción creada y registrada en backend.");
            return sub;
        }

        async _sendSubscribeToServer(sub) {
            const body = sub.toJSON();
            const res = await fetch("/api/push/subscribe", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                credentials: "same-origin",
                body: JSON.stringify(body),
            });
            if (!res.ok) {
                const txt = await res.text().catch(() => "");
                throw new Error("Falló /api/push/subscribe: " + res.status + " " + txt);
            }
        }

        // ── Desuscribirse de Web Push ─────────────────────────────────────────────
        async unsubscribePush() {
            if (!this.swRegistration) throw new Error("SW no inicializado.");
            const sub = await this.swRegistration.pushManager.getSubscription();
            if (!sub) {
                console.log("[NM] No hay suscripción activa.");
                return;
            }
            const endpoint = sub.endpoint;
            const ok = await sub.unsubscribe();
            if (!ok) console.warn("[NM] unsubscribe() devolvió false (continuamos).");

            await fetch("/api/push/unsubscribe", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                credentials: "same-origin",
                body: JSON.stringify({ endpoint }),
            }).catch(() => {});
            console.log("[NM] Suscripción eliminada.");
        }

        // ── Test desde backend ────────────────────────────────────────────────────
        async sendTest() {
            const res = await fetch("/api/push/test", {
                method: "POST",
                credentials: "same-origin",
            });
            if (!res.ok) {
                const txt = await res.text().catch(() => "");
                throw new Error("Falló /api/push/test: " + res.status + " " + txt);
            }
            const j = await res.json().catch(() => ({}));
            if (!j.ok) throw new Error("Backend devolvió ok=false en test.");
            console.log("[NM] Test enviado. Espera la notificación push.");
        }

        // ── Notificación local (fallback visual) ──────────────────────────────────
        async showNotification(title, body, tag = "default") {
            if (!("Notification" in window)) return;
            if (Notification.permission !== "granted") return;
            if (!this.swRegistration) return;
            try {
                await this.swRegistration.showNotification(title, {
                    body,
                    icon: "/static/icon-192x192.png",
                    badge: "/static/icon-128x128.png",
                    tag,
                    requireInteraction: false,
                    silent: false,
                    vibrate: [200, 100, 200],
                    data: { url: "/dashboard" },
                });
            } catch (e) {
                console.warn("[NM] showNotification falló:", e);
            }
        }

        // ── Monitor local de horarios (tolerante a min exacto y evita duplicados) ─
        startScheduleMonitoring(schedules, hasActiveRecord) {
            // Limpia anteriores
            this.stopScheduleMonitoring();

            if (!Array.isArray(schedules) || schedules.length === 0) return;

            const seenKey = "local-" + todayKey();
            const seen = LocalSeen.get(seenKey); // { "entry-08:00-early": true, ... }

            const tick = () => {
                const now = new Date();
                const dow = now.getDay(); // 0=Domingo
                const minutesNow = now.getHours() * 60 + now.getMinutes();

                schedules.forEach((s) => {
                    // Backend: 0=Lunes..6=Domingo ; JS: 0=Domingo..6=Sábado
                    const jsDay = (s.day_of_week + 1) % 7;
                    if (dow !== jsDay) return;

                    const [sh, sm] = s.start_time.split(":").map(Number);
                    const [eh, em] = s.end_time.split(":").map(Number);
                    const start = sh * 60 + sm;
                    const end = eh * 60 + em;

                    const checkAndNotify = (condMinute, tag, title, body) => {
                        // Tolerancia: dispara si estamos a ±1 minuto del objetivo
                        if (Math.abs(minutesNow - condMinute) <= 1) {
                            const key = `${tag}-${s.start_time}-${s.end_time}`;
                            if (!seen[key]) {
                                seen[key] = true;
                                LocalSeen.set(seenKey, seen);
                                this.showNotification(title, body, tag);
                            }
                        }
                    };

                    if (!hasActiveRecord) {
                        checkAndNotify(start - 5, "entry-early", "⏰ Fichar entrada", `En 5 min (${s.start_time})`);
                        checkAndNotify(start + 5, "entry-late", "⚠️ Recuerda fichar", `Pasaron 5 min (${s.start_time})`);
                    } else {
                        checkAndNotify(end - 5, "exit-early", "⏰ Fichar salida", `En 5 min (${s.end_time})`);
                        checkAndNotify(end + 5, "exit-late", "⚠️ Recuerda fichar salida", `Pasaron 5 min (${s.end_time})`);
                    }
                });
            };

            // Lanza ya y cada 60s
            tick();
            const id = setInterval(tick, 60 * 1000);
            this.checkIntervals.set("monitor", id);
        }

        stopScheduleMonitoring() {
            this.checkIntervals.forEach((i) => clearInterval(i));
            this.checkIntervals.clear();
        }
    }

    // Singleton global
    const notificationManager = new NotificationManager();
    window.notificationManager = notificationManager;

    // Auto-init al cargar
    document.addEventListener("DOMContentLoaded", () => {
        notificationManager.init().catch(console.error);
    });
})();
