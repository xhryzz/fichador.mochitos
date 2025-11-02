from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, timezone
from itsdangerous import URLSafeTimedSerializer
import json
import csv
import io
import os
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
import resend
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from pywebpush import webpush, WebPushException
from zoneinfo import ZoneInfo
from functools import wraps

# ==========================
# App & Config
# ==========================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'tu-clave-secreta-aqui-cambiar-en-produccion')

# DB: PostgreSQL (Render) o SQLite local
if os.environ.get('DATABASE_URL'):
    database_url = os.environ.get('DATABASE_URL')
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fichador.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])

# ==========================
# Zona horaria
# ==========================
TZ = os.environ.get('TIMEZONE', 'Europe/Madrid')
ZONE = ZoneInfo(TZ)

def utcnow():
    """Ahora en UTC (aware)."""
    return datetime.now(timezone.utc)

def now_local():
    """Ahora en hora local configurada (aware)."""
    return datetime.now(ZONE)

def to_local(dt):
    """Convierte un datetime (UTC o naive) a hora local."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Asumimos que lo almacenado/recibido naive está en UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZONE)

def to_utc_from_local_date_time(date_obj, time_obj):
    """Combina una fecha+hora LOCAL y la convierte a UTC aware."""
    dt_local = datetime.combine(date_obj, time_obj, tzinfo=ZONE)
    return dt_local.astimezone(timezone.utc)

def fmt_local(dt, fmt="%H:%M"):
    d = to_local(dt)
    return d.strftime(fmt) if d else ""

# ============================================
# Formato de horas -> "X h Y min" para toda la app
# ============================================
def format_hours_to_hm(hours_value):
    if hours_value is None:
        return "-"
    try:
        val = float(hours_value)
    except Exception:
        return str(hours_value)
    sign = "-" if val < 0 else ""
    total_minutes = int(round(abs(val) * 60))
    h = total_minutes // 60
    m = total_minutes % 60
    return f"{sign}{h} h {m} min"

def format_seconds_to_hm(total_seconds):
    if total_seconds is None:
        return "-"
    try:
        secs = int(round(float(total_seconds)))
    except Exception:
        return str(total_seconds)
    sign = "-" if secs < 0 else ""
    secs = abs(secs)
    total_minutes = secs // 60
    h = total_minutes // 60
    m = total_minutes % 60
    return f"{sign}{h} h {m} min"

# Filtros Jinja
@app.template_filter('hm')
def jinja_hm_filter(hours_value):
    return format_hours_to_hm(hours_value)

@app.template_filter('hm_seconds')
def jinja_hm_seconds_filter(seconds_value):
    return format_seconds_to_hm(seconds_value)

@app.template_filter('localdt')
def jinja_localdt_filter(value, fmt="%d/%m/%Y %H:%M"):
    dt = to_local(value)
    return dt.strftime(fmt) if dt else ""

# ==========================
# Auth helpers y settings
# ==========================
VAPID_PUBLIC_KEY = os.environ.get('VAPID_PUBLIC_KEY')
VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY')
VAPID_EMAIL = os.environ.get('VAPID_EMAIL', 'admin@example.com')
TASKS_TOKEN = os.environ.get('TASKS_TOKEN')

def require_tasks_token(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not TASKS_TOKEN or request.headers.get('X-TASKS-TOKEN') != TASKS_TOKEN:
            return jsonify({"error": "forbidden"}), 403
        return fn(*args, **kwargs)
    return wrapper

# ==========================
# Helpers de negocio
# ==========================
def today_schedule_for(user, dtu):
    day = dtu.weekday()  # 0=Lunes
    return Schedule.query.filter_by(user_id=user.id, day_of_week=day, is_active=True).first()

def active_record_for_today(user):
    today = now_local().date()
    return TimeRecord.query.filter_by(user_id=user.id, date=today, exit_time=None).first()

def hours_worked_total(user):
    recs = TimeRecord.query.filter(TimeRecord.user_id==user.id, TimeRecord.exit_time.isnot(None)).all()
    total = 0.0
    for r in recs:
        total += (as_utc_naive(r.exit_time) - as_utc_naive(r.entry_time)).total_seconds()/3600
    return total

# ==========================
# Modelos
# ==========================
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=True)
    name = db.Column(db.String(100), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_first_login = db.Column(db.Boolean, default=True)
    total_hours_required = db.Column(db.Float, default=150.0)
    schedules = db.relationship('Schedule', backref='user', lazy=True)
    time_records = db.relationship('TimeRecord', backref='user', lazy=True)

class Schedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    day_of_week = db.Column(db.Integer, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    start_time_2 = db.Column(db.Time, nullable=True)
    end_time_2 = db.Column(db.Time, nullable=True)
    hours_required = db.Column(db.Float, nullable=False)
    is_active = db.Column(db.Boolean, default=True)

class TimeRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)  # fecha LOCAL del fichaje
    entry_time = db.Column(db.DateTime(timezone=True), nullable=False)  # guardado en UTC
    exit_time = db.Column(db.DateTime(timezone=True), nullable=True)    # guardado en UTC
    location = db.Column(db.String(200), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    notes = db.Column(db.String(500), nullable=True)
    is_active = db.Column(db.Boolean, default=True)

class NotificationSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)

    push_enabled = db.Column(db.Boolean, default=False)
    minutes_after_start_no_entry = db.Column(db.Integer, default=5)
    open_record_minutes = db.Column(db.Integer, default=15)
    end_passed_minutes = db.Column(db.Integer, default=5)
    weekly_summary_day = db.Column(db.Integer, default=6)  # 0=Lunes ... 6=Domingo
    weekly_summary_time = db.Column(db.Time, default=datetime.strptime('18:00', '%H:%M').time)
    last_missed_entry_sent = db.Column(db.Date, nullable=True)
    last_open_record_sent = db.Column(db.Date, nullable=True)
    last_weekly_sent = db.Column(db.Date, nullable=True)
    last_missed_entry_sent_1 = db.Column(db.Date, nullable=True)  # turno 1
    last_missed_entry_sent_2 = db.Column(db.Date, nullable=True)  # turno 2

class PushSubscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    endpoint = db.Column(db.String(500), unique=True, nullable=False)
    p256dh = db.Column(db.String(255), nullable=False)
    auth = db.Column(db.String(255), nullable=False)
    user_agent = db.Column(db.String(255), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ============================================
# Tokens para correo
# ============================================
def generate_token(user_id):
    return serializer.dumps(user_id, salt='password-setup-salt')

def verify_token(token, expiration=86400):
    try:
        user_id = serializer.loads(token, salt='password-setup-salt', max_age=expiration)
        return user_id
    except:
        return None

# ============================================
# Email con SendGrid
# ============================================
def send_setup_password_email(user):
    try:
        if not user or not user.email:
            print("❌ No se pudo enviar el correo: usuario o email inválido")
            return False

        api_key = os.environ.get("SENDGRID_API_KEY")
        from_email = os.environ.get("FROM_EMAIL")
        if not api_key:
            print("❌ Falta SENDGRID_API_KEY en env vars")
            return False
        if not from_email:
            print("❌ Falta FROM_EMAIL en env vars (verificado en SendGrid)")
            return False

        print(f"🚀 Enviando correo con SendGrid a: {user.email}")

        token = generate_token(user.id)
        setup_url = url_for('set_first_password_token', token=token, _external=True)

        text = f"""Fichador - Configura tu contraseña

Hola {user.name},

Se ha creado una cuenta para ti en Fichador.

Configura tu contraseña aquí:
{setup_url}

Este enlace es válido por 24 horas.

Tus datos:
- Email: {user.email}
- Horas requeridas: {user.total_hours_required} horas

Si no solicitaste esta cuenta, ignora este mensaje.

--
Equipo Fichador
"""

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Configura tu contraseña</title>
<style>
body {{ font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; line-height:1.6; color:#333; max-width:600px; margin:0 auto; padding:20px; }}
.container {{ background:#fff; border-radius:12px; padding:40px; margin:20px 0; border:1px solid #e1e5e9; }}
.header {{ text-align:center; margin-bottom:30px; border-bottom:1px solid #e1e5e9; padding-bottom:20px; }}
.logo {{ font-size:24px; font-weight:700; color:#007AFF; margin-bottom:8px; }}
.button {{ display:inline-block; background:#007AFF; color:#fff; padding:14px 32px; text-decoration:none; border-radius:8px; font-weight:600; font-size:16px; margin:20px 0; }}
.alert {{ background:#eff6ff; border:1px solid #3b82f6; border-radius:8px; padding:16px; margin:20px 0; }}
</style></head>
<body>
  <div class="container">
    <div class="header">
      <div class="logo">Fichador</div>
      <div style="font-size:20px; color:#1f2937;">Bienvenido, {user.name}</div>
    </div>
    <p>Se ha creado una cuenta para ti en Fichador. Para comenzar, configura tu contraseña.</p>
    <div style="text-align:center;">
      <a href="{setup_url}" class="button">Configurar Contraseña</a>
    </div>
    <div class="alert"><strong>⚠️ Importante:</strong> Este enlace es válido por 24 horas.</div>
    <div style="background:#f8fafc; padding:20px; border-radius:8px;">
      <div><strong>📧 Email:</strong> {user.email}</div>
      <div><strong>⏰ Horas requeridas:</strong> {user.total_hours_required} horas</div>
    </div>
    <div style="text-align:center; margin-top:30px; color:#6b7280;">Equipo Fichador</div>
  </div>
</body></html>"""

        message = Mail(
            from_email=from_email,
            to_emails=user.email,
            subject="Configura tu contraseña - Fichador",
            plain_text_content=text,
            html_content=html
        )

        sg = SendGridAPIClient(api_key)
        resp = sg.send(message)
        print(f"✅ SendGrid status={resp.status_code} (202=aceptado)")
        return resp.status_code in (200, 202)
    except Exception as e:
        print(f"❌ ERROR_SENDGRID: {str(e)}")
        import traceback
        print(f"🔍 Traceback: {traceback.format_exc()}")
        return False

# ============================================
# Push
# ============================================
def send_push_to_user(user, title, body, data=None, actions=None):
    if not (VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY and VAPID_EMAIL):
        print("❌ VAPID keys/email no configurados.")
        return False

    payload = {
        "title": title,
        "body": body,
        "icon": "/static/icon-192x192.png",
        "badge": "/static/icon-72x72.png",
        "data": data or {},
        "actions": actions or [{"action": "fichar", "title": "Abrir fichador"}],
    }

    subs = PushSubscription.query.filter_by(user_id=user.id, is_active=True).all()
    ok_any = False
    for s in subs:
        try:
            webpush(
                subscription_info={"endpoint": s.endpoint, "keys": {"p256dh": s.p256dh, "auth": s.auth}},
                data=json.dumps(payload),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": f"mailto:{VAPID_EMAIL}"},
            )
            ok_any = True
        except WebPushException as e:
            s.is_active = False
            db.session.commit()
            print("❌ WebPush error:", e)
    return ok_any

# Pequeñas migraciones automáticas (SQLite)
def upgrade_db():
    from sqlalchemy import text
    with app.app_context():
        cols = db.session.execute(text("PRAGMA table_info(schedule);")).fetchall()
        names = {c[1] for c in cols}
        if "start_time_2" not in names:
            db.session.execute(text("ALTER TABLE schedule ADD COLUMN start_time_2 TIME NULL"))
        if "end_time_2" not in names:
            db.session.execute(text("ALTER TABLE schedule ADD COLUMN end_time_2 TIME NULL"))

        cols = db.session.execute(text("PRAGMA table_info(notification_settings);")).fetchall()
        names = {c[1] for c in cols}
        if "last_missed_entry_sent_1" not in names:
            db.session.execute(text("ALTER TABLE notification_settings ADD COLUMN last_missed_entry_sent_1 DATE NULL"))
        if "last_missed_entry_sent_2" not in names:
            db.session.execute(text("ALTER TABLE notification_settings ADD COLUMN last_missed_entry_sent_2 DATE NULL"))

        db.session.commit()

# ==========================
# Contexto Jinja
# ==========================
@app.context_processor
def inject_now():
    return {'now': now_local()}

# ==========================
# Rutas principales
# ==========================
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()

        if user:
            if user.is_first_login and not user.password:
                flash('⚠️ Debes configurar tu contraseña primero. Revisa tu correo electrónico', 'warning')
                return redirect(url_for('login'))
            if user.password and check_password_hash(user.password, password):
                login_user(user)
                return redirect(url_for('dashboard'))
            else:
                flash('Credenciales incorrectas', 'error')
        else:
            flash('Email no encontrado', 'error')
    return render_template('login.html')

@app.route('/setup-password/<token>', methods=['GET', 'POST'])
def set_first_password_token(token):
    user_id = verify_token(token)
    if not user_id:
        flash('❌ El enlace ha expirado o no es válido. Solicita uno nuevo al administrador', 'error')
        return redirect(url_for('login'))

    user = User.query.get_or_404(user_id)

    if not user.is_first_login or user.password:
        flash('Esta cuenta ya tiene contraseña configurada', 'error')
        return redirect(url_for('login'))

    if request.method == 'POST':
        password = request.form.get('password')
        password_confirm = request.form.get('password_confirm')

        if not password or len(password) < 6:
            flash('La contraseña debe tener al menos 6 caracteres', 'error')
            return render_template('set_first_password.html', user=user, token=token)

        if password != password_confirm:
            flash('Las contraseñas no coinciden', 'error')
            return render_template('set_first_password.html', user=user, token=token)

        user.password = generate_password_hash(password)
        user.is_first_login = False
        db.session.commit()
        flash('✅ Contraseña configurada correctamente. Ya puedes iniciar sesión', 'success')
        return redirect(url_for('login'))

    return render_template('set_first_password.html', user=user, token=token)

@app.route('/set_first_password/<int:user_id>', methods=['GET', 'POST'])
def set_first_password(user_id):
    user = User.query.get_or_404(user_id)
    if not user.is_first_login or user.password:
        flash('Esta cuenta ya tiene contraseña configurada', 'error')
        return redirect(url_for('login'))

    if request.method == 'POST':
        password = request.form.get('password')
        password_confirm = request.form.get('password_confirm')

        if not password or len(password) < 6:
            flash('La contraseña debe tener al menos 6 caracteres', 'error')
            return render_template('set_first_password.html', user=user)

        if password != password_confirm:
            flash('Las contraseñas no coinciden', 'error')
            return render_template('set_first_password.html', user=user)

        user.password = generate_password_hash(password)
        user.is_first_login = False
        db.session.commit()
        flash('Contraseña configurada correctamente. Ya puedes iniciar sesión', 'success')
        return redirect(url_for('login'))

    return render_template('set_first_password.html', user=user)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        name = request.form.get('name')

        if User.query.filter_by(email=email).first():
            flash('El email ya está registrado', 'error')
            return redirect(url_for('register'))

        new_user = User(
            email=email,
            password=generate_password_hash(password),
            name=name,
            is_admin=False,
            is_first_login=False
        )
        db.session.add(new_user)
        db.session.commit()
        flash('Cuenta creada exitosamente', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/dashboard')
@login_required
def dashboard():
    today = now_local().date()

    today_records = (TimeRecord.query
                     .filter_by(user_id=current_user.id, date=today)
                     .order_by(TimeRecord.entry_time.desc())
                     .all())

    active_record = next((record for record in today_records if not record.exit_time), None)

    week_start = today - timedelta(days=today.weekday())
    week_records = (TimeRecord.query
                    .filter(TimeRecord.user_id == current_user.id,
                            TimeRecord.date >= week_start,
                            TimeRecord.exit_time.isnot(None))
                    .all())

    weekly_hours = sum((r.exit_time - r.entry_time).total_seconds() / 3600 for r in week_records)
    today_hours = sum((r.exit_time - r.entry_time).total_seconds() / 3600
                      for r in today_records if r.exit_time)

    all_records = (TimeRecord.query
                   .filter(TimeRecord.user_id == current_user.id,
                           TimeRecord.exit_time.isnot(None))
                   .all())
    total_hours_worked = sum((r.exit_time - r.entry_time).total_seconds() / 3600 for r in all_records)

    schedules = Schedule.query.filter_by(user_id=current_user.id, is_active=True).all()
    weekly_required_hours = 0
    for i in range(7):
        day = week_start + timedelta(days=i)
        day_of_week = day.weekday()
        day_schedule = next((s for s in schedules if s.day_of_week == day_of_week), None)
        if day_schedule:
            weekly_required_hours += day_schedule.hours_required

    total_hours_required = current_user.total_hours_required

    return render_template('dashboard.html',
                           active_record=active_record,
                           today_records=today_records,
                           weekly_hours=weekly_hours,
                           today_hours=today_hours,
                           weekly_required_hours=weekly_required_hours,
                           total_hours_worked=total_hours_worked,
                           total_hours_required=total_hours_required)

@app.route('/clock_in', methods=['POST'])
@login_required
def clock_in():
    location = request.form.get('location', 'Córdoba Ecuestre')
    latitude = request.form.get('latitude', type=float, default=37.8766614)
    longitude = request.form.get('longitude', type=float, default=-4.7831533)
    now_loc = now_local()
    today = now_loc.date()

    existing_active = TimeRecord.query.filter_by(user_id=current_user.id, date=today, exit_time=None).first()
    if existing_active:
        flash('Ya tienes una sesión activa. Debes cerrarla antes de iniciar otra.', 'error')
        return redirect(url_for('dashboard'))

    new_record = TimeRecord(
        user_id=current_user.id,
        date=today,  # fecha local
        entry_time=now_loc.astimezone(timezone.utc),  # guardado en UTC
        location=location,
        latitude=latitude,
        longitude=longitude,
        is_active=True
    )
    db.session.add(new_record)
    db.session.commit()
    flash('Entrada registrada correctamente', 'success')
    return redirect(url_for('dashboard'))

@app.route('/clock_out', methods=['POST'])
@login_required
def clock_out():
    now_loc = now_local()
    today = now_loc.date()
    active_record = TimeRecord.query.filter_by(user_id=current_user.id, date=today, exit_time=None).first()

    if not active_record:
        flash('No hay una sesión activa para cerrar', 'error')
        return redirect(url_for('dashboard'))

    location = request.form.get('location', 'Córdoba Ecuestre')
    if location != active_record.location:
        active_record.location = f"{active_record.location} | Salida: {location}"

    active_record.exit_time = now_loc.astimezone(timezone.utc)  # UTC
    active_record.is_active = False
    db.session.commit()

    session_hours = (active_record.exit_time - active_record.entry_time).total_seconds() / 3600
    flash(f'Salida registrada correctamente. Sesión: {session_hours:.2f} horas', 'success')
    return redirect(url_for('dashboard'))

@app.route('/schedule')
@login_required
def schedule():
    schedules = Schedule.query.filter_by(user_id=current_user.id).order_by(Schedule.day_of_week).all()
    schedules_by_day = {s.day_of_week: s for s in schedules}
    days_of_week = [(0, 'Lunes'), (1, 'Martes'), (2, 'Miércoles'), (3, 'Jueves'), (4, 'Viernes'), (5, 'Sábado'), (6, 'Domingo')]
    return render_template('schedule.html', schedules_by_day=schedules_by_day, days_of_week=days_of_week)

@app.route('/schedule/add', methods=['POST'])
@login_required
def add_schedule():
    day_of_week = int(request.form.get('day_of_week'))
    start_time = datetime.strptime(request.form.get('start_time'), '%H:%M').time()
    end_time = datetime.strptime(request.form.get('end_time'), '%H:%M').time()

    # Turno opcional 2
    st2_raw = (request.form.get('start_time_2') or '').strip()
    et2_raw = (request.form.get('end_time_2') or '').strip()
    start_time_2 = datetime.strptime(st2_raw, '%H:%M').time() if st2_raw else None
    end_time_2 = datetime.strptime(et2_raw, '%H:%M').time() if et2_raw else None

    is_active = request.form.get('is_active') == 'on'

    hours_required = (datetime.combine(datetime.min, end_time) - datetime.combine(datetime.min, start_time)).seconds / 3600
    if start_time_2 and end_time_2:
        hours_required += (datetime.combine(datetime.min, end_time_2) - datetime.combine(datetime.min, start_time_2)).seconds / 3600

    existing = Schedule.query.filter_by(user_id=current_user.id, day_of_week=day_of_week).first()
    if existing:
        existing.start_time = start_time
        existing.end_time = end_time
        existing.start_time_2 = start_time_2
        existing.end_time_2 = end_time_2
        existing.hours_required = hours_required
        existing.is_active = is_active
        flash('Horario actualizado correctamente', 'success')
    else:
        schedule = Schedule(
            user_id=current_user.id, day_of_week=day_of_week,
            start_time=start_time, end_time=end_time,
            start_time_2=start_time_2, end_time_2=end_time_2,
            hours_required=hours_required, is_active=is_active
        )
        db.session.add(schedule)
        flash('Horario añadido correctamente', 'success')

    db.session.commit()
    return redirect(url_for('schedule'))

@app.route('/schedule/toggle/<int:id>')
@login_required
def toggle_schedule(id):
    schedule = Schedule.query.get_or_404(id)
    if schedule.user_id != current_user.id:
        flash('No tienes permisos para modificar este horario', 'error')
        return redirect(url_for('schedule'))
    schedule.is_active = not schedule.is_active
    db.session.commit()
    flash(f'Horario {"activado" if schedule.is_active else "desactivado"} correctamente', 'success')
    return redirect(url_for('schedule'))

@app.route('/schedule/delete/<int:id>')
@login_required
def delete_schedule(id):
    schedule = Schedule.query.get_or_404(id)
    if schedule.user_id != current_user.id:
        flash('No tienes permisos para eliminar este horario', 'error')
        return redirect(url_for('schedule'))
    db.session.delete(schedule)
    db.session.commit()
    flash('Horario eliminado correctamente', 'success')
    return redirect(url_for('schedule'))

@app.route('/schedule/copy_week', methods=['POST'])
@login_required
def copy_week():
    source_day = int(request.form.get('source_day'))
    target_days = request.form.getlist('target_days[]')
    source_schedule = Schedule.query.filter_by(user_id=current_user.id, day_of_week=source_day).first()

    if not source_schedule:
        flash('No hay horario configurado para el día seleccionado', 'error')
        return redirect(url_for('schedule'))

    for day in target_days:
        day = int(day)
        if day == source_day:
            continue
        existing = Schedule.query.filter_by(user_id=current_user.id, day_of_week=day).first()
        if existing:
            existing.start_time = source_schedule.start_time
            existing.end_time = source_schedule.end_time
            existing.start_time_2 = source_schedule.start_time_2
            existing.end_time_2 = source_schedule.end_time_2
            existing.hours_required = source_schedule.hours_required
            existing.is_active = source_schedule.is_active
        else:
            new_schedule = Schedule(
                user_id=current_user.id, day_of_week=day,
                start_time=source_schedule.start_time, end_time=source_schedule.end_time,
                start_time_2=source_schedule.start_time_2, end_time_2=source_schedule.end_time_2,
                hours_required=source_schedule.hours_required, is_active=source_schedule.is_active
            )
            db.session.add(new_schedule)

    db.session.commit()
    flash('Horarios copiados correctamente', 'success')
    return redirect(url_for('schedule'))

@app.route('/schedule/update_total_hours', methods=['POST'])
@login_required
def update_total_hours():
    total_hours = float(request.form.get('total_hours', 150))
    current_user.total_hours_required = total_hours
    db.session.commit()
    flash('Horas totales actualizadas correctamente', 'success')
    return redirect(url_for('schedule'))

# =============================
# CRUD de fichajes para usuario
# =============================
@app.route('/records')
@login_required
def records():
    page = request.args.get('page', 1, type=int)
    records = (TimeRecord.query
               .filter_by(user_id=current_user.id)
               .order_by(TimeRecord.date.desc(), TimeRecord.entry_time.desc())
               .paginate(page=page, per_page=10))
    return render_template('records.html', records=records)

@app.route('/records/new', methods=['GET', 'POST'])
@login_required
def user_add_record():
    if request.method == 'POST':
        try:
            date_val = datetime.strptime(request.form.get('date'), '%Y-%m-%d').date()
            entry_time_t = datetime.strptime(request.form.get('entry_time'), '%H:%M').time()
            exit_time_str = request.form.get('exit_time')
            exit_time_t = datetime.strptime(exit_time_str, '%H:%M').time() if exit_time_str else None

            # Validación: salida >= entrada (comparando en local)
            if exit_time_t and datetime.combine(date_val, exit_time_t) < datetime.combine(date_val, entry_time_t):
                flash('La salida no puede ser anterior a la entrada', 'error')
                return redirect(url_for('user_add_record'))

            latitude = float(request.form.get('latitude')) if request.form.get('latitude') else None
            longitude = float(request.form.get('longitude')) if request.form.get('longitude') else None

            record = TimeRecord(
                user_id=current_user.id,
                date=date_val,  # fecha local proporcionada
                entry_time=to_utc_from_local_date_time(date_val, entry_time_t),
                exit_time=to_utc_from_local_date_time(date_val, exit_time_t) if exit_time_t else None,
                location=request.form.get('location', ''),
                latitude=latitude,
                longitude=longitude,
                notes=request.form.get('notes', '')
            )
            db.session.add(record)
            db.session.commit()
            flash('Fichaje creado correctamente', 'success')
            return redirect(url_for('records'))
        except Exception as e:
            db.session.rollback()
            print('Error al crear fichaje:', e)
            flash('Error al crear el fichaje', 'error')
            return redirect(url_for('user_add_record'))
    return render_template('user_new_record.html')

@app.route('/records/edit/<int:record_id>', methods=['GET', 'POST'])
@login_required
def user_edit_record(record_id):
    record = TimeRecord.query.get_or_404(record_id)
    if record.user_id != current_user.id:
        flash('No tienes permisos para editar este fichaje', 'error')
        return redirect(url_for('records'))

    if request.method == 'POST':
        try:
            entry_time_str = request.form.get('entry_time')
            exit_time_str = request.form.get('exit_time')

            if entry_time_str:
                t = datetime.strptime(entry_time_str, '%H:%M').time()
                record.entry_time = to_utc_from_local_date_time(record.date, t)

            if exit_time_str:
                t2 = datetime.strptime(exit_time_str, '%H:%M').time()
                record.exit_time = to_utc_from_local_date_time(record.date, t2)
            else:
                record.exit_time = None

            # Validación: salida >= entrada (en UTC vale igual)
            if record.exit_time and record.exit_time < record.entry_time:
                flash('La salida no puede ser anterior a la entrada', 'error')
                return redirect(url_for('user_edit_record', record_id=record.id))

            if request.form.get('latitude'):
                record.latitude = float(request.form.get('latitude'))
            if request.form.get('longitude'):
                record.longitude = float(request.form.get('longitude'))
            record.location = request.form.get('location', '')
            record.notes = request.form.get('notes', '')
            db.session.commit()
            flash('Fichaje actualizado correctamente', 'success')
            return redirect(url_for('records'))
        except Exception as e:
            db.session.rollback()
            print('Error al actualizar fichaje:', e)
            flash('Error al actualizar el fichaje', 'error')
            return redirect(url_for('user_edit_record', record_id=record.id))

    entry_prefill = fmt_local(record.entry_time, '%H:%M') if record.entry_time else ''
    exit_prefill = fmt_local(record.exit_time, '%H:%M') if record.exit_time else ''
    return render_template('user_edit_record.html', record=record, entry_prefill=entry_prefill, exit_prefill=exit_prefill)

@app.route('/records/delete/<int:record_id>', methods=['POST'])
@login_required
def user_delete_record(record_id):
    record = TimeRecord.query.get_or_404(record_id)
    if record.user_id != current_user.id:
        flash('No tienes permisos para eliminar este fichaje', 'error')
        return redirect(url_for('records'))
    try:
        db.session.delete(record)
        db.session.commit()
        flash('Fichaje eliminado correctamente', 'success')
    except Exception as e:
        db.session.rollback()
        print('Error al eliminar fichaje:', e)
        flash('Error al eliminar el fichaje', 'error')
    return redirect(url_for('records'))

# ==========================
# Reports
# ==========================
@app.route('/reports')
@login_required
def reports():
    return render_template('reports.html')

@app.route('/generate_report', methods=['POST'])
@login_required
def generate_report():
    start_date = datetime.strptime(request.form.get('start_date'), '%Y-%m-%d').date()
    end_date = datetime.strptime(request.form.get('end_date'), '%Y-%m-%d').date()
    report_type = request.form.get('report_type')

    records = (TimeRecord.query
               .filter(TimeRecord.user_id == current_user.id,
                       TimeRecord.date >= start_date,
                       TimeRecord.date <= end_date)
               .order_by(TimeRecord.date, TimeRecord.entry_time)
               .all())

    if report_type == 'csv':
        return generate_csv_report(records, start_date, end_date)
    elif report_type == 'pdf':
        return generate_pdf_report(records, start_date, end_date)
    else:
        flash('Tipo de reporte no válido', 'error')
        return redirect(url_for('reports'))

def generate_csv_report(records, start_date, end_date):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Fecha', 'Entrada', 'Salida', 'Duración', 'Ubicación', 'Coordenadas'])
    for r in records:
        dur = format_seconds_to_hm((r.exit_time - r.entry_time).total_seconds()) if r.exit_time else ''
        coords = f"{r.latitude:.6f}, {r.longitude:.6f}" if r.latitude is not None and r.longitude is not None else ''
        writer.writerow([
            r.date.strftime('%d/%m/%Y'),
            fmt_local(r.entry_time, '%H:%M'),
            fmt_local(r.exit_time, '%H:%M') if r.exit_time else 'En curso',
            dur,
            r.location or '',
            coords
        ])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode('utf-8')),
                     mimetype='text/csv',
                     as_attachment=True,
                     download_name=f'reporte_{start_date}_{end_date}.csv')

def generate_pdf_report(records, start_date, end_date):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2.2*cm, bottomMargin=2*cm
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="TitleBig", parent=styles["Heading1"], fontName="Helvetica-Bold",
                              fontSize=18, textColor=colors.HexColor("#111827"), spaceAfter=8))
    styles.add(ParagraphStyle(name="Meta", parent=styles["Normal"], fontSize=10,
                              textColor=colors.HexColor("#4B5563"), leading=14, spaceAfter=3))
    styles.add(ParagraphStyle(name="Cell", parent=styles["Normal"], fontSize=9, leading=12))
    styles.add(ParagraphStyle(name="CellBold", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=9, leading=12))

    elements = []
    title_row = []
    logo_path = os.path.join(app.root_path, "static", "icon-144x144.png")
    if os.path.exists(logo_path):
        title_row.append(Image(logo_path, width=1.2*cm, height=1.2*cm))
    else:
        title_row.append(Spacer(1, 1.2*cm))
    title_row.append(Paragraph("Reporte de fichajes", styles["TitleBig"]))
    header_tbl = Table([title_row], colWidths=[1.4*cm, doc.width - 1.4*cm])
    header_tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    elements.append(header_tbl)

    periodo = f"{start_date.strftime('%d/%m/%Y')} a {end_date.strftime('%d/%m/%Y')}"
    elements.append(Paragraph(f"<b>Usuario:</b> {current_user.name}", styles["Meta"]))
    elements.append(Paragraph(f"<b>Período:</b> {periodo}", styles["Meta"]))
    elements.append(Paragraph(f"<b>Email:</b> {current_user.email}", styles["Meta"]))
    elements.append(Spacer(1, 6))

    data = [[Paragraph("Fecha", styles["CellBold"]),
             Paragraph("Entrada", styles["CellBold"]),
             Paragraph("Salida", styles["CellBold"]),
             Paragraph("Duración", styles["CellBold"]),
             Paragraph("Ubicación", styles["CellBold"]),
             Paragraph("Coordenadas", styles["CellBold"])]]

    total_seconds = 0
    open_sessions = 0
    unique_days = set()

    for r in records:
        unique_days.add(r.date)
        fecha = r.date.strftime("%d/%m/%Y")
        entrada = fmt_local(r.entry_time, "%H:%M") if r.entry_time else "—"
        if r.exit_time:
            salida = fmt_local(r.exit_time, "%H:%M")
            dur_secs = (r.exit_time - r.entry_time).total_seconds()
            dur = format_seconds_to_hm(dur_secs)
            total_seconds += int(dur_secs)
        else:
            salida = "En curso"
            dur = "En curso"
            open_sessions += 1

        ubic = Paragraph((r.location or "—"), styles["Cell"])
        coords = "—"
        if r.latitude is not None and r.longitude is not None:
            try:
                coords = f"{float(r.latitude):.5f}, {float(r.longitude):.5f}"
            except Exception:
                coords = f"{r.latitude}, {r.longitude}"

        data.append([Paragraph(fecha, styles["Cell"]),
                     Paragraph(entrada, styles["Cell"]),
                     Paragraph(salida, styles["Cell"]),
                     Paragraph(dur, styles["Cell"]),
                     ubic,
                     Paragraph(coords, styles["Cell"])])

    fixed_widths = [2.2*cm, 1.8*cm, 1.8*cm, 2.5*cm, 3.0*cm]
    auto_width = doc.width - sum(fixed_widths)
    col_widths = [2.2*cm, 1.8*cm, 1.8*cm, 2.5*cm, auto_width, 3.0*cm]
    if auto_width < 5*cm:
        col_widths = [2.0*cm, 1.6*cm, 1.6*cm, 2.2*cm,
                      doc.width - (2.0*cm + 1.6*cm + 1.6*cm + 2.2*cm + 2.6*cm),
                      2.6*cm]

    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563EB")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.HexColor("#F3F4F6")]),
        ("ALIGN", (0, 1), (0, -1), "CENTER"),
        ("ALIGN", (1, 1), (3, -1), "CENTER"),
        ("VALIGN", (0, 1), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E5E7EB")),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 10))

    avg_per_day = int(total_seconds / max(len(unique_days), 1))
    resumen = [
        [Paragraph("<b>Sesiones cerradas</b>", styles["Cell"]), Paragraph(str(len([r for r in records if r.exit_time])), styles["Cell"])],
        [Paragraph("<b>Sesiones abiertas</b>", styles["Cell"]), Paragraph(str(open_sessions), styles["Cell"])],
        [Paragraph("<b>Total trabajado</b>", styles["Cell"]), Paragraph(format_seconds_to_hm(total_seconds), styles["Cell"])],
        [Paragraph("<b>Promedio por día</b>", styles["Cell"]), Paragraph(format_seconds_to_hm(avg_per_day), styles["Cell"])],
    ]
    resumen_tbl = Table(resumen, colWidths=[5.5*cm, doc.width - 5.5*cm])
    resumen_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFF6FF")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E5E7EB")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(Paragraph("Resumen", styles["CellBold"]))
    elements.append(Spacer(1, 4))
    elements.append(resumen_tbl)
    elements.append(Spacer(1, 12))

    footer_tbl = Table([[Paragraph('Este Fichador ha sido realizado por @chriismartinezz', styles["Cell"])]],
                       colWidths=[doc.width])
    footer_tbl.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LINEABOVE", (0, 0), (-1, 0), 0.7, colors.HexColor("#9CA3AF")),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(footer_tbl)

    doc.build(elements, onFirstPage=_pdf_footer, onLaterPages=_pdf_footer)

    buffer.seek(0)
    fname = f"reporte_{start_date.strftime('%Y-%m-%d')}_{end_date.strftime('%Y-%m-%d')}.pdf"
    return send_file(buffer, mimetype="application/pdf", as_attachment=True, download_name=fname)

def _pdf_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 9)
    canvas.setFillColor(colors.HexColor("#6B7280"))
    canvas.drawString(doc.leftMargin, 1.2 * cm, f"Generado el {datetime.now(ZONE).strftime('%d/%m/%Y %H:%M')}")
    canvas.drawRightString(doc.rightMargin + doc.width, 1.2 * cm, f"Página {doc.page}")
    canvas.restoreState()

# ==========================
# Stats
# ==========================
@app.route('/stats')
@login_required
def stats():
    today = now_local().date()
    week_start = today - timedelta(days=today.weekday())
    daily_hours = []
    for i in range(7):
        day = week_start + timedelta(days=i)
        records = TimeRecord.query.filter_by(user_id=current_user.id, date=day).all()
        day_hours = sum((r.exit_time - r.entry_time).total_seconds() / 3600 for r in records if r.exit_time)
        daily_hours.append(round(day_hours, 2))
    return render_template('stats.html', daily_hours=daily_hours)

# ==========================
# Admin
# ==========================
@app.route('/admin')
@login_required
def admin():
    if not current_user.is_admin:
        flash('No tienes permisos de administrador', 'error')
        return redirect(url_for('dashboard'))
    users = User.query.all()
    return render_template('admin.html', users=users)

@app.route('/admin/create_user', methods=['POST'])
@login_required
def admin_create_user():
    if not current_user.is_admin:
        flash('No tienes permisos de administrador', 'error')
        return redirect(url_for('dashboard'))

    email = request.form.get('email')
    name = request.form.get('name')
    total_hours = float(request.form.get('total_hours', 150))

    if User.query.filter_by(email=email).first():
        flash('Ya existe un usuario con ese email', 'error')
        return redirect(url_for('admin'))

    new_user = User(
        email=email,
        name=name,
        password=None,
        is_admin=False,
        is_first_login=True,
        total_hours_required=total_hours
    )
    db.session.add(new_user)
    db.session.commit()

    print(f"👤 Usuario creado: {name} ({email})")

    if send_setup_password_email(new_user):
        flash(f'✅ Usuario {name} creado correctamente. Se ha enviado el correo a {email}', 'success')
    else:
        flash(f'⚠️ Usuario {name} creado, pero hubo un error al enviar el correo.', 'warning')

    return redirect(url_for('admin'))

@app.route('/admin/resend_email/<int:user_id>')
@login_required
def admin_resend_email(user_id):
    if not current_user.is_admin:
        flash('No tienes permisos de administrador', 'error')
        return redirect(url_for('dashboard'))

    user = User.query.get_or_404(user_id)

    if not user.is_first_login or user.password:
        flash('Este usuario ya ha configurado su contraseña', 'info')
        return redirect(url_for('admin'))

    print(f"🔄 Reenviando correo a {user.email}...")
    if send_setup_password_email(user):
        flash(f'✅ Correo REENVIADO exitosamente a {user.email}', 'success')
    else:
        flash(f'❌ Error al reenviar el correo', 'error')

    return redirect(url_for('admin'))

@app.route('/admin/delete_user/<int:user_id>')
@login_required
def admin_delete_user(user_id):
    if not current_user.is_admin:
        flash('No tienes permisos de administrador', 'error')
        return redirect(url_for('dashboard'))
    if user_id == current_user.id:
        flash('No puedes eliminar tu propia cuenta', 'error')
        return redirect(url_for('admin'))

    user = User.query.get_or_404(user_id)
    TimeRecord.query.filter_by(user_id=user_id).delete()
    Schedule.query.filter_by(user_id=user_id).delete()
    db.session.delete(user)
    db.session.commit()
    flash(f'Usuario {user.name} eliminado correctamente', 'success')
    return redirect(url_for('admin'))

@app.route('/admin/user_records/<int:user_id>')
@login_required
def admin_user_records(user_id):
    if not current_user.is_admin:
        flash('No tienes permisos de administrador', 'error')
        return redirect(url_for('dashboard'))
    user = User.query.get_or_404(user_id)
    page = request.args.get('page', 1, type=int)
    records = (TimeRecord.query
               .filter_by(user_id=user_id)
               .order_by(TimeRecord.date.desc(), TimeRecord.entry_time.desc())
               .paginate(page=page, per_page=10))
    return render_template('admin_records.html', user=user, records=records)

@app.route('/admin/edit_record/<int:record_id>', methods=['GET', 'POST'])
@login_required
def admin_edit_record(record_id):
    if not current_user.is_admin:
        flash('No tienes permisos de administrador', 'error')
        return redirect(url_for('dashboard'))
    record = TimeRecord.query.get_or_404(record_id)
    if request.method == 'POST':
        entry_time_str = request.form.get('entry_time')
        exit_time_str = request.form.get('exit_time')

        if entry_time_str:
            t = datetime.strptime(entry_time_str, '%H:%M').time()
            record.entry_time = to_utc_from_local_date_time(record.date, t)
        if exit_time_str:
            t2 = datetime.strptime(exit_time_str, '%H:%M').time()
            record.exit_time = to_utc_from_local_date_time(record.date, t2)
        else:
            record.exit_time = None

        if request.form.get('latitude'):
            record.latitude = float(request.form.get('latitude'))
        if request.form.get('longitude'):
            record.longitude = float(request.form.get('longitude'))
        record.location = request.form.get('location', '')
        record.notes = request.form.get('notes', '')
        db.session.commit()
        flash('Registro actualizado correctamente', 'success')
        return redirect(url_for('admin_user_records', user_id=record.user_id))
    return render_template('edit_record.html', record=record)

@app.route('/admin/add_record', methods=['POST'])
@login_required
def admin_add_record():
    if not current_user.is_admin:
        flash('No tienes permisos de administrador', 'error')
        return redirect(url_for('dashboard'))
    user_id = request.form.get('user_id')
    date_val = datetime.strptime(request.form.get('date'), '%Y-%m-%d').date()
    entry_time_t = datetime.strptime(request.form.get('entry_time'), '%H:%M').time()
    exit_time_str = request.form.get('exit_time')
    exit_time_t = datetime.strptime(exit_time_str, '%H:%M').time() if exit_time_str else None
    latitude = float(request.form.get('latitude')) if request.form.get('latitude') else None
    longitude = float(request.form.get('longitude')) if request.form.get('longitude') else None

    new_record = TimeRecord(
        user_id=user_id,
        date=date_val,
        entry_time=to_utc_from_local_date_time(date_val, entry_time_t),
        exit_time=to_utc_from_local_date_time(date_val, exit_time_t) if exit_time_t else None,
        location=request.form.get('location', ''),
        latitude=latitude,
        longitude=longitude,
        notes=request.form.get('notes', '')
    )
    db.session.add(new_record)
    db.session.commit()
    flash('Registro añadido correctamente', 'success')
    return redirect(url_for('admin_user_records', user_id=user_id))

@app.route('/admin/delete_record/<int:record_id>')
@login_required
def admin_delete_record(record_id):
    if not current_user.is_admin:
        flash('No tienes permisos de administrador', 'error')
        return redirect(url_for('dashboard'))
    record = TimeRecord.query.get_or_404(record_id)
    user_id = record.user_id
    db.session.delete(record)
    db.session.commit()
    flash('Fichaje eliminado correctamente', 'success')
    return redirect(url_for('admin_user_records', user_id=user_id))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

# ==========================
# API
# ==========================
@app.route('/manifest.json')
def manifest():
    return send_file('static/manifest.json', mimetype='application/json')

@app.route('/sw.js')
def service_worker():
    return send_file('sw.js', mimetype='application/javascript')

@app.route('/api/schedules')
@login_required
def api_schedules():
    schedules = Schedule.query.filter_by(user_id=current_user.id, is_active=True).all()
    return jsonify([{
        'id': s.id,
        'day_of_week': s.day_of_week,
        'start_time': s.start_time.strftime('%H:%M'),
        'end_time': s.end_time.strftime('%H:%M'),
        'hours_required': s.hours_required
    } for s in schedules])

@app.route('/api/active_record')
@login_required
def api_active_record():
    today = now_local().date()
    active_record = TimeRecord.query.filter_by(user_id=current_user.id, date=today, exit_time=None).first()
    return jsonify({
        'has_active_record': active_record is not None,
        'entry_time': active_record.entry_time.isoformat() if active_record else None  # UTC ISO
    })

# ==========================
# Errores
# ==========================
@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('500.html'), 500

# ==========================
# Init DB
# ==========================
def init_db():
    with app.app_context():
        db.create_all()
        upgrade_db()
        if not User.query.filter_by(email='christianconhr@gmail.com').first():
            admin = User(
                email='christianconhr@gmail.com',
                password=generate_password_hash('Lionelmesi10'),
                name='Christian',
                is_admin=True,
                is_first_login=False,
                total_hours_required=150.0
            )
            db.session.add(admin)
            db.session.commit()
            print("✓ Usuario Admin Christian creado")
        print("✅ Base de datos inicializada correctamente")

# ==========================
# Jobs (notificaciones)
# ==========================
def _job_notify_due_clockin(now=None):
    now = now or now_local()
    users = User.query.all()
    sent = 0
    for u in users:
        s = NotificationSettings.query.filter_by(user_id=u.id).first()
        if not (s and s.push_enabled):
            continue
        sch = today_schedule_for(u, now)
        if not sch:
            continue

        # Turno 1
        planned_dt1 = datetime.combine(now.date(), sch.start_time, tzinfo=ZONE)
        if now >= planned_dt1 + timedelta(minutes=s.minutes_after_start_no_entry):
            has_entry1 = TimeRecord.query.filter_by(user_id=u.id, date=now.date()).first()
            if not has_entry1 and getattr(s, 'last_missed_entry_sent_1', None) != now.date():
                ok = send_push_to_user(u, "🔔 No has fichado y ya es hora", "Recuerda fichar la ENTRADA (turno 1).")
                if ok:
                    s.last_missed_entry_sent_1 = now.date()
                    db.session.commit()
                    sent += 1

        # Turno 2 (opcional)
        if getattr(sch, 'start_time_2', None):
            planned_dt2 = datetime.combine(now.date(), sch.start_time_2, tzinfo=ZONE)
            if now >= planned_dt2 + timedelta(minutes=s.minutes_after_start_no_entry):
                boundary2_local = datetime.combine(now.date(), sch.start_time_2)
                has_entry2 = TimeRecord.query.filter(
                    TimeRecord.user_id == u.id,
                    TimeRecord.date == now.date(),
                    TimeRecord.entry_time >= datetime.combine(now.date(), sch.start_time_2, tzinfo=ZONE).astimezone(timezone.utc)
                ).first()
                if not has_entry2 and getattr(s, 'last_missed_entry_sent_2', None) != now.date():
                    ok = send_push_to_user(u, "🔔 No has fichado y ya es hora", "Recuerda fichar la ENTRADA (turno 2).")
                    if ok:
                        s.last_missed_entry_sent_2 = now.date()
                        db.session.commit()
                        sent += 1
    return sent

def as_utc_naive(dt):
    """Devuelve dt en UTC sin tzinfo (naive).
    Si ya viene naive, asumimos que está en UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)



def _job_notify_open_record(now=None):
    now = now or now_local()
    users = User.query.all()
    sent = 0
    for u in users:
        s = NotificationSettings.query.filter_by(user_id=u.id).first()
        if not (s and s.push_enabled):
            continue

        rec = active_record_for_today(u)
        if not rec:
            continue

        should = False

        # 1) Fichaje abierto más de X minutos (normalizamos a UTC naive)
        now_utc_naive = datetime.utcnow()
        entry_utc_naive = as_utc_naive(rec.entry_time)
        diff_secs = (now_utc_naive - entry_utc_naive).total_seconds()
        if diff_secs >= s.open_record_minutes * 60:
            should = True

        # 2) Pasó hora fin + X (en local)
        sch = today_schedule_for(u, now)
        if sch:
            end_dt = datetime.combine(now.date(), sch.end_time, tzinfo=ZONE) + timedelta(minutes=s.end_passed_minutes)
            if now >= end_dt:
                should = True

        if should and s.last_open_record_sent != now.date():
            ok = send_push_to_user(
                u,
                "🕒 Tienes un fichaje abierto",
                "¿Se te ha pasado cerrar? Revísalo cuando puedas."
            )
            if ok:
                s.last_open_record_sent = now.date()
                db.session.commit()
                sent += 1
    return sent

def _job_weekly_summary(now=None):
    now = now or now_local()
    users = User.query.all()
    sent = 0
    for u in users:
        s = NotificationSettings.query.filter_by(user_id=u.id).first()
        if not (s and s.push_enabled):
            continue
        if now.weekday() == s.weekly_summary_day and now.time() >= s.weekly_summary_time and s.last_weekly_sent != now.date():
            total_required = u.total_hours_required or 150.0
            done = hours_worked_total(u)
            remaining = max(total_required - done, 0)
            msg = f"Te quedan {remaining:.1f} h para finalizar las prácticas (hechas {done:.1f}/{total_required:.1f})."
            if send_push_to_user(u, "📊 Resumen semanal", msg):
                s.last_weekly_sent = now.date()
                db.session.commit()
                sent += 1
    return sent

@app.post('/admin/test-mail')
@login_required
def admin_test_mail():
    if not current_user.is_admin:
        flash('No tienes permisos', 'error')
        return redirect(url_for('dashboard'))
    ok = send_setup_password_email(current_user)
    if ok:
        flash('✅ Correo de prueba enviado con SendGrid', 'success')
    else:
        flash('❌ Error al enviar el correo de prueba (revisa Logs)', 'error')
    return redirect(url_for('admin'))

@app.route('/notifications', methods=['GET', 'POST'])
@login_required
def notifications():
    settings = NotificationSettings.query.filter_by(user_id=current_user.id).first()
    if not settings:
        settings = NotificationSettings(user_id=current_user.id)
        db.session.add(settings)
        db.session.commit()

    if request.method == 'POST':
        settings.push_enabled = bool(request.form.get('push_enabled'))
        settings.minutes_after_start_no_entry = int(request.form.get('minutes_after_start_no_entry', 5))
        settings.open_record_minutes = int(request.form.get('open_record_minutes', 15))
        settings.end_passed_minutes = int(request.form.get('end_passed_minutes', 5))
        settings.weekly_summary_day = int(request.form.get('weekly_summary_day', 6))
        settings.weekly_summary_time = datetime.strptime(request.form.get('weekly_summary_time', '18:00'), '%H:%M').time()
        db.session.commit()
        flash('Ajustes de notificaciones guardados ✅', 'success')
        return redirect(url_for('notifications'))

    return render_template('notifications.html', settings=settings, vapid_public=VAPID_PUBLIC_KEY)

@app.post('/api/push/subscribe')
@login_required
def api_push_subscribe():
    data = request.get_json(force=True)
    endpoint = data.get('endpoint')
    keys = (data.get('keys') or {})
    p256dh = keys.get('p256dh')
    authk = keys.get('auth')
    ua = request.headers.get('User-Agent', '')

    if not (endpoint and p256dh and authk):
        return jsonify({"ok": False, "error": "Bad subscription"}), 400

    sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if sub:
        sub.p256dh = p256dh
        sub.auth = authk
        sub.is_active = True
        sub.user_id = current_user.id
    else:
        sub = PushSubscription(
            user_id=current_user.id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=authk,
            user_agent=ua
        )
        db.session.add(sub)
    db.session.commit()
    return jsonify({"ok": True})

@app.post('/api/push/unsubscribe')
@login_required
def api_push_unsubscribe():
    data = request.get_json(force=True)
    endpoint = data.get('endpoint')
    if endpoint:
        sub = PushSubscription.query.filter_by(endpoint=endpoint, user_id=current_user.id).first()
        if sub:
            sub.is_active = False
            db.session.commit()
    return jsonify({"ok": True})

@app.post('/api/push/test')
@login_required
def api_push_test():
    ok = send_push_to_user(current_user, "🔔 Prueba de notificaciones", "Si ves esto, las push ya funcionan.")
    return jsonify({"ok": ok})

@app.post('/tasks/notify_due_clockin')
@require_tasks_token
def task_notify_due_clockin():
    count = _job_notify_due_clockin()
    return jsonify({"sent": count})

@app.post('/tasks/notify_open_record')
@require_tasks_token
def task_notify_open_record():
    count = _job_notify_open_record()
    return jsonify({"sent": count})

@app.post('/tasks/weekly_summary')
@require_tasks_token
def task_weekly_summary():
    count = _job_weekly_summary()
    return jsonify({"sent": count})

# ======== CRON GET (con token por query) ========
def _check_cron_token():
    token_req = request.args.get('token') or request.headers.get('X-CRON-TOKEN')
    if not TASKS_TOKEN or token_req != TASKS_TOKEN:
        abort(403)

@app.get('/tasks/run-tick')
def tasks_run_tick():
    _check_cron_token()
    try:
        due = _job_notify_due_clockin()
        open_ = _job_notify_open_record()
        return jsonify(ok=True, due_clockin=due, open_record=open_)
    except Exception as e:
        app.logger.exception("run-tick failed")
        return jsonify(ok=False, error=str(e)), 500

@app.get('/tasks/run-weekly')
def tasks_run_weekly():
    _check_cron_token()
    try:
        weekly = _job_weekly_summary()
        return jsonify(ok=True, weekly=weekly)
    except Exception as e:
        app.logger.exception("run-weekly failed")
        return jsonify(ok=False, error=str(e)), 500

# ==========================
# Main / Render
# ==========================
if __name__ == '__main__':
    init_db()
    app.run(debug=True)
else:
    init_db()
