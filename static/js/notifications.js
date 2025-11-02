class NotificationManager {
    constructor() {
        this.swRegistration = null;
        this.checkIntervals = new Map();
    }

    async init() {
        if ('serviceWorker' in navigator) {
            try {
                this.swRegistration = await navigator.serviceWorker.register('/sw.js');
            } catch (e) {
                console.error('SW error:', e);
            }
        }
        if ('Notification' in window) {
            await Notification.requestPermission();
        }
    }

    async showNotification(title, body, tag = 'default') {
        if (Notification.permission === 'granted' && this.swRegistration) {
            await this.swRegistration.showNotification(title, {
                body,
                icon: '/static/icon-192x192.png',
                tag,
                requireInteraction: true,
                vibrate: [200, 100, 200]
            });
        }
    }

    startScheduleMonitoring(schedules, hasActiveRecord) {
        this.checkIntervals.forEach(i => clearInterval(i));
        this.checkIntervals.clear();

        if (!schedules || schedules.length === 0) return;

        const check = setInterval(() => {
            const now = new Date();
            const day = now.getDay();
            const time = now.getHours() * 60 + now.getMinutes();

            schedules.forEach(s => {
                const sDay = (s.day_of_week + 1) % 7;
                if (day !== sDay) return;

                const [sh, sm] = s.start_time.split(':').map(Number);
                const [eh, em] = s.end_time.split(':').map(Number);
                const start = sh * 60 + sm;
                const end = eh * 60 + em;

                if (!hasActiveRecord && time === start - 5) {
                    this.showNotification('⏰ Fichar entrada', `En 5 min (${s.start_time})`, 'entry');
                }
                if (!hasActiveRecord && time === start + 5) {
                    this.showNotification('⚠️ Recuerda fichar', `Pasaron 5 min (${s.start_time})`, 'entry-late');
                }
                if (hasActiveRecord && time === end - 5) {
                    this.showNotification('⏰ Fichar salida', `En 5 min (${s.end_time})`, 'exit');
                }
                if (hasActiveRecord && time === end + 5) {
                    this.showNotification('⚠️ Recuerda fichar salida', `Pasaron 5 min (${s.end_time})`, 'exit-late');
                }
            });
        }, 60000);

        this.checkIntervals.set('main', check);
    }

    stopScheduleMonitoring() {
        this.checkIntervals.forEach(i => clearInterval(i));
        this.checkIntervals.clear();
    }
}

const notificationManager = new NotificationManager();
notificationManager.init();