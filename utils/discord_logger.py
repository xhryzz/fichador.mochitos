# utils/discord_logger.py
import os, time, threading, json
from datetime import datetime, timezone
import requests
from flask import request, has_request_context
from flask_login import current_user

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

PALETTE = {
    "info":     0x3498DB,
    "success":  0x2ECC71,
    "warning":  0xF1C40F,
    "error":    0xE74C3C,
    "neutral":  0x95A5A6,
}

def _send_async(payload: dict):
    if not WEBHOOK_URL:
        print("[discord-logger] Falta DISCORD_WEBHOOK_URL; omito")
        return
    def _go():
        try:
            r = requests.post(WEBHOOK_URL, json=payload, timeout=6)
            if r.status_code == 429:
                retry = 1.0
                try: retry = float(r.json().get("retry_after", 1.0))
                except Exception: pass
                time.sleep(retry)
                requests.post(WEBHOOK_URL, json=payload, timeout=6)
            else:
                r.raise_for_status()
        except Exception as e:
            print(f"[discord-logger] Error webhook: {e}")
    threading.Thread(target=_go, daemon=True).start()

def _actor_name(u):
    try:
        return getattr(u, "email", None) or getattr(u, "name", None) or str(u)
    except Exception:
        return "desconocido"

def log_event(title: str, description: str = "", *, level="info",
              fields: dict | None = None, user=None, content: str | None = None,
              username: str | None = "Fichador · Auditoría", avatar_url: str | None = None,
              footer: str | None = None, color=None):
    color = color or PALETTE.get(level, PALETTE["neutral"])
    ip, endpoint, method = "server", "-", "-"
    if has_request_context():
        try:
            ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "server"
            endpoint = request.path
            method = request.method
        except Exception:
            pass
    embed = {
        "title": title,
        "description": description,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fields": [{"name": "👤 Usuario", "value": f"`{_actor_name(user)}`", "inline": True}],
        "footer": {"text": footer or f"{method} {endpoint} • {ip}"},
    }
    if fields:
        for k, v in fields.items():
            val = "—" if v is None else str(v)
            val = val if val.strip() else "—"
            embed["fields"].append({"name": str(k), "value": val, "inline": False})
    payload = {"content": content, "embeds": [embed]}
    if username: payload["username"] = username
    if avatar_url: payload["avatar_url"] = avatar_url
    _send_async(payload)

# Atajos específicos
def log_clock(action: str, record, *, user=None):
    title = "🟢 Fichaje: ENTRADA" if action == "in" else "🔴 Fichaje: SALIDA"
    try:
        fecha = record.date.strftime("%d/%m/%Y") if getattr(record, "date", None) else "—"
        ent = record.entry_time.astimezone(timezone.utc).strftime("%H:%M") if getattr(record, "entry_time", None) else "—"
        sal = record.exit_time.astimezone(timezone.utc).strftime("%H:%M") if getattr(record, "exit_time", None) else "—"
        dur = "—"
        if getattr(record, "entry_time", None) and getattr(record, "exit_time", None):
            secs = (record.exit_time - record.entry_time).total_seconds()
            mins = int(secs // 60)
            dur = f"{mins//60} h {mins%60} min"
        loc = getattr(record, "location", None) or "—"
        fields = {"🆔 Registro": getattr(record, "id", "—"),
                  "📅 Fecha": fecha, "⏰ Entrada(UTC)": ent, "⏱️ Salida(UTC)": sal,
                  "⌛ Duración": dur, "📍 Ubicación": loc}
    except Exception:
        fields = {"Detalle": "No se pudieron leer todos los campos del registro."}
    log_event(title, level="success", fields=fields, user=user)

def log_record(action: str, record, *, user=None, extra: dict | None = None):
    emojis = {"create": "📝", "update": "✏️", "delete": "🗑️"}
    levels = {"create": "success", "update": "info", "delete": "warning"}
    fields = {
        "🆔 Registro": getattr(record, "id", "—"),
        "📅 Fecha": getattr(record, "date", "—"),
        "Entrada(UTC)": getattr(record, "entry_time", "—"),
        "Salida(UTC)": getattr(record, "exit_time", "—"),
        "📍 Ubicación": getattr(record, "location", "—"),
    }
    if extra: fields.update(extra)
    log_event(f"{emojis.get(action,'ℹ️')} Registro: {action.upper()}",
              level=levels.get(action, "info"), fields=fields, user=user)

def log_schedule(action: str, schedule, *, user=None, extra: dict | None = None):
    emojis = {"create": "🧭", "update": "🧰", "delete": "🗑️"}
    levels = {"create": "success", "update": "info", "delete": "warning"}
    fields = {
        "🆔 Horario": getattr(schedule, "id", "—"),
        "Día": getattr(schedule, "day_of_week", "—"),
        "Tramo 1": f"{getattr(schedule,'start_time',None)}–{getattr(schedule,'end_time',None)}",
        "Tramo 2": f"{getattr(schedule,'start_time_2',None)}–{getattr(schedule,'end_time_2',None)}",
        "Activo": "Sí" if getattr(schedule, "is_active", False) else "No",
        "Horas requeridas": getattr(schedule, "hours_required", "—"),
    }
    if extra: fields.update(extra)
    log_event(f"{emojis.get(action,'📅')} Horario: {action.upper()}",
              level=levels.get(action, "info"), fields=fields, user=user)
