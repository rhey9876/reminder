"""
Medication Reminder API - Flask Backend
"""
import os
import ssl
import secrets
import smtplib
import sqlite3
import hashlib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory, make_response
from flask_cors import CORS
import yaml


def compute_app_version():
    """Compute version hash from source files for automatic cache invalidation."""
    files_to_hash = [
        Path(__file__),  # app.py
        Path(__file__).parent.parent / 'frontend' / 'app.js',
        Path(__file__).parent.parent / 'frontend' / 'index.html',
        Path(__file__).parent.parent / 'frontend' / 'service-worker.js',
    ]
    hasher = hashlib.md5()
    for file_path in files_to_hash:
        if file_path.exists():
            hasher.update(file_path.read_bytes())
    return hasher.hexdigest()[:8]


APP_VERSION = compute_app_version()

app = Flask(__name__, static_folder=None)

# CORS: Only allow specific origins (production domain)
ALLOWED_ORIGINS = os.environ.get('ALLOWED_ORIGINS', 'https://reminder.heydtmann.eu').split(',')
CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=True)

# Snooze storage: {(medication, time): snooze_until_datetime}
snooze_cache = {}

# Auth storage: {email: {otp, expires, attempts}}
otp_cache = {}
# Session storage: {token: {email, expires}}
session_cache = {}
# Rate limiting: {ip: {count, reset_time}}
rate_limit_cache = {}

# Rate limit settings
RATE_LIMIT_REQUESTS = 5  # max requests
RATE_LIMIT_WINDOW = 300  # per 5 minutes


def check_rate_limit(ip):
    """Check if IP is rate limited. Returns True if allowed, False if blocked."""
    now = datetime.now()

    if ip not in rate_limit_cache:
        rate_limit_cache[ip] = {'count': 1, 'reset_time': now + timedelta(seconds=RATE_LIMIT_WINDOW)}
        return True

    entry = rate_limit_cache[ip]

    # Reset if window expired
    if now > entry['reset_time']:
        rate_limit_cache[ip] = {'count': 1, 'reset_time': now + timedelta(seconds=RATE_LIMIT_WINDOW)}
        return True

    # Increment and check
    entry['count'] += 1
    if entry['count'] > RATE_LIMIT_REQUESTS:
        return False

    return True


def get_client_ip():
    """Get client IP, respecting X-Forwarded-For for reverse proxy."""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr

# SMTP Configuration
SMTP_HOST = os.environ.get('SMTP_HOST', 'postfix-mailcow')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
MAIL_FROM = os.environ.get('MAIL_FROM', 'noreply@heydtmann.eu')
MAIL_USER = os.environ.get('MAIL_USER', 'noreply@heydtmann.eu')
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')

# Try to read from Docker secret if env var not set
FLASK_SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', '')
if not MAIL_PASSWORD or not FLASK_SECRET_KEY:
    secret_path = Path('/run/secrets/noreply')
    if secret_path.exists():
        try:
            for line in secret_path.read_text().strip().split('\n'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip().upper()
                    if key == 'PROD.MAIL_PASSWORD' and not MAIL_PASSWORD:
                        MAIL_PASSWORD = value.strip()
                    elif key == 'PROD.FLASK_SECRET_KEY' and not FLASK_SECRET_KEY:
                        FLASK_SECRET_KEY = value.strip()
        except Exception:
            pass

# Auth settings
OTP_EXPIRY_MINUTES = 5
SESSION_EXPIRY_DAYS = 30
AUTH_ENABLED = os.environ.get('AUTH_ENABLED', 'true').lower() == 'true'

# Set Flask secret key - MUST be set in production
if not FLASK_SECRET_KEY:
    if AUTH_ENABLED:
        raise RuntimeError("FLASK_SECRET_KEY must be set when AUTH_ENABLED=true")
    FLASK_SECRET_KEY = 'dev-only-auth-disabled'
app.secret_key = FLASK_SECRET_KEY

# Weekday mapping (German and English)
WEEKDAY_MAP = {
    'mo': 0, 'mon': 0, 'monday': 0, 'montag': 0,
    'di': 1, 'tue': 1, 'tuesday': 1, 'dienstag': 1,
    'mi': 2, 'wed': 2, 'wednesday': 2, 'mittwoch': 2,
    'do': 3, 'thu': 3, 'thursday': 3, 'donnerstag': 3,
    'fr': 4, 'fri': 4, 'friday': 4, 'freitag': 4,
    'sa': 5, 'sat': 5, 'saturday': 5, 'samstag': 5,
    'so': 6, 'sun': 6, 'sunday': 6, 'sonntag': 6,
}

# Configuration
DATA_DIR = Path(os.environ.get('DATA_DIR', '/data'))
DB_PATH = DATA_DIR / 'intake_log.db'
CONFIG_PATH = DATA_DIR / 'reminder.yaml'
FRONTEND_DIR = Path(__file__).parent.parent / 'frontend'

# Ensure data directory exists
DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_db():
    """Get database connection."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize database tables."""
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS intake_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            medication TEXT NOT NULL,
            scheduled_time TEXT NOT NULL,
            actual_time TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'taken',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()


def load_config():
    """Load medications configuration from YAML."""
    if not CONFIG_PATH.exists():
        # Create default config if not exists
        default_config = {
            'medications': [
                {'name': 'Beispiel Medikament', 'times': ['08:00', '20:00'], 'enabled': True}
            ],
            'settings': {
                'reminder_window': 30,
                'timezone': 'Europe/Berlin'
            }
        }
        save_config(default_config)
        return default_config

    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def save_config(config):
    """Save medications configuration to YAML."""
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)


def is_scheduled_today(days_config):
    """Check if medication is scheduled for today based on days config."""
    if not days_config:
        return True  # No days specified = every day

    today_weekday = datetime.now().weekday()  # 0=Monday, 6=Sunday

    for day in days_config:
        day_lower = day.lower().strip()
        if day_lower in WEEKDAY_MAP and WEEKDAY_MAP[day_lower] == today_weekday:
            return True

    return False


def was_taken_today(medication, scheduled_time):
    """Check if medication was already taken today for the given scheduled time."""
    conn = get_db()
    today = datetime.now().strftime('%Y-%m-%d')

    cursor = conn.execute('''
        SELECT COUNT(*) FROM intake_log
        WHERE medication = ?
        AND scheduled_time = ?
        AND DATE(created_at) = ?
        AND status = 'taken'
    ''', (medication, scheduled_time, today))

    count = cursor.fetchone()[0]
    conn.close()
    return count > 0


def is_snoozed(medication, scheduled_time):
    """Check if medication is currently snoozed."""
    key = (medication, scheduled_time)
    if key not in snooze_cache:
        return False

    snooze_until = snooze_cache[key]
    now = datetime.now()

    if now < snooze_until:
        return True
    else:
        # Clean up expired snooze
        del snooze_cache[key]
        return False


def get_email_whitelist():
    """Get list of allowed emails from config."""
    config = load_config()
    return config.get('auth', {}).get('allowed_emails', [])


def validate_medication_input(medication, scheduled_time):
    """Validate medication and time input."""
    import re

    if not medication or not isinstance(medication, str):
        return False, "Invalid medication name"

    if len(medication) > 100:
        return False, "Medication name too long"

    if not scheduled_time or not isinstance(scheduled_time, str):
        return False, "Invalid time"

    # Time must be HH:MM format
    if not re.match(r'^([01]?[0-9]|2[0-3]):[0-5][0-9]$', scheduled_time):
        return False, "Invalid time format (use HH:MM)"

    return True, None


def is_email_allowed(email):
    """Check if email is in whitelist."""
    whitelist = get_email_whitelist()
    return email.lower() in [e.lower() for e in whitelist]


def generate_otp():
    """Generate 6-digit OTP."""
    return ''.join([str(secrets.randbelow(10)) for _ in range(6)])


def send_otp_email(to_email, otp):
    """Send OTP via email."""
    if not MAIL_PASSWORD:
        # No password = can't send email, fail secure
        print(f"[ERROR] MAIL_PASSWORD not set, cannot send OTP")
        return False

    try:
        subject = "Reminder"
        body = f"Ihr Login-Code lautet: {otp}\n\nDer Code ist {OTP_EXPIRY_MINUTES} Minuten gültig."

        msg = MIMEText(body, _charset="utf-8")
        msg["Subject"] = subject
        msg["From"] = MAIL_FROM
        msg["To"] = to_email

        # SSL context - verify certificates unless explicitly disabled for internal networks
        context = ssl.create_default_context()
        if os.environ.get('SMTP_SKIP_VERIFY', 'false').lower() == 'true':
            # Only for internal/self-signed certs - logs warning
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls(context=context)
            server.login(MAIL_USER, MAIL_PASSWORD)
            server.send_message(msg)

        return True
    except Exception as e:
        print(f"[ERROR] Failed to send email: {e}")
        return False


def create_session(email):
    """Create a new session token."""
    token = secrets.token_hex(32)
    expires = datetime.now() + timedelta(days=SESSION_EXPIRY_DAYS)
    session_cache[token] = {'email': email, 'expires': expires}
    return token


def validate_session(token):
    """Validate session token."""
    if not token or token not in session_cache:
        return None

    session = session_cache[token]
    if datetime.now() > session['expires']:
        del session_cache[token]
        return None

    return session['email']


def get_auth_token():
    """Get auth token from request (cookie or header)."""
    # Check cookie first
    token = request.cookies.get('mrem_token')
    if token:
        return token

    # Check Authorization header
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header[7:]

    return None


def require_auth(f):
    """Decorator to require authentication."""
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if not AUTH_ENABLED:
            return f(*args, **kwargs)

        token = get_auth_token()
        email = validate_session(token)

        if not email:
            return jsonify({'error': 'Unauthorized', 'auth_required': True}), 401

        return f(*args, **kwargs)

    return decorated


def get_medication_status():
    """Calculate status for all medications."""
    config = load_config()
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    window_minutes = config.get('settings', {}).get('reminder_window', 30)

    overdue = []
    due = []
    upcoming = []

    for med in config.get('medications', []):
        if not med.get('enabled', True):
            continue

        # Check if scheduled for today (weekday check)
        if not is_scheduled_today(med.get('days')):
            continue

        for time_str in med.get('times', []):
            # Parse scheduled time for today
            scheduled = datetime.strptime(f"{today_str} {time_str}", '%Y-%m-%d %H:%M')

            # Check if already taken
            if was_taken_today(med['name'], time_str):
                continue

            # Check if snoozed
            if is_snoozed(med['name'], time_str):
                continue

            # Calculate time difference in minutes
            diff_minutes = (now - scheduled).total_seconds() / 60

            med_info = {
                'medication': med['name'],
                'time': time_str,
                'scheduled': scheduled.isoformat(),
                'minutes_diff': int(abs(diff_minutes))
            }

            if diff_minutes > window_minutes:
                # Overdue: past the reminder window
                med_info['minutes_late'] = int(diff_minutes)
                overdue.append(med_info)
            elif diff_minutes >= -window_minutes:
                # Due: within the reminder window (before or after)
                if diff_minutes > 0:
                    med_info['minutes_late'] = int(diff_minutes)
                else:
                    med_info['minutes_until'] = int(abs(diff_minutes))
                due.append(med_info)
            elif diff_minutes < -window_minutes:
                # Upcoming: not yet in the window
                med_info['minutes_until'] = int(abs(diff_minutes))
                upcoming.append(med_info)

    # Sort by time
    overdue.sort(key=lambda x: x['time'])
    due.sort(key=lambda x: x['time'])
    upcoming.sort(key=lambda x: x['time'])

    return {
        'overdue': overdue,
        'due': due,
        'upcoming': upcoming,
        'timestamp': now.isoformat(),
        'settings': config.get('settings', {})
    }


# Auth Routes
@app.route('/api/auth/check', methods=['GET'])
def auth_check():
    """Check if user is authenticated."""
    if not AUTH_ENABLED:
        return jsonify({'authenticated': True, 'auth_enabled': False})

    token = get_auth_token()
    email = validate_session(token)

    return jsonify({
        'authenticated': email is not None,
        'auth_enabled': True,
        'email': email
    })


@app.route('/api/auth/request', methods=['POST'])
def auth_request():
    """Request OTP for email."""
    if not AUTH_ENABLED:
        return jsonify({'error': 'Auth not enabled'}), 400

    # Rate limiting
    client_ip = get_client_ip()
    if not check_rate_limit(client_ip):
        return jsonify({'error': 'Too many requests. Please wait 5 minutes.'}), 429

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    email = data.get('email', '').strip().lower()

    if not email:
        return jsonify({'error': 'Email required'}), 400

    if not is_email_allowed(email):
        # Don't reveal if email exists or not
        return jsonify({'success': True, 'message': 'If email is registered, OTP was sent'})

    # Generate and store OTP
    otp = generate_otp()
    otp_cache[email] = {
        'otp': otp,
        'expires': datetime.now() + timedelta(minutes=OTP_EXPIRY_MINUTES),
        'attempts': 0
    }

    # Send email
    if send_otp_email(email, otp):
        return jsonify({'success': True, 'message': 'OTP sent'})
    else:
        return jsonify({'error': 'Failed to send email'}), 500


@app.route('/api/auth/verify', methods=['POST'])
def auth_verify():
    """Verify OTP and create session."""
    if not AUTH_ENABLED:
        return jsonify({'error': 'Auth not enabled'}), 400

    data = request.get_json()
    email = data.get('email', '').strip().lower()
    otp = data.get('otp', '').strip()

    if not email or not otp:
        return jsonify({'error': 'Email and OTP required'}), 400

    if email not in otp_cache:
        return jsonify({'error': 'Invalid or expired OTP'}), 401

    stored = otp_cache[email]

    # Check expiry
    if datetime.now() > stored['expires']:
        del otp_cache[email]
        return jsonify({'error': 'OTP expired'}), 401

    # Check attempts
    stored['attempts'] += 1
    if stored['attempts'] > 3:
        del otp_cache[email]
        return jsonify({'error': 'Too many attempts'}), 429

    # Verify OTP
    if otp != stored['otp']:
        return jsonify({'error': 'Invalid OTP'}), 401

    # Success - create session
    del otp_cache[email]
    token = create_session(email)

    response = make_response(jsonify({'success': True, 'email': email}))
    response.set_cookie(
        'mrem_token',
        token,
        max_age=SESSION_EXPIRY_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=True,
        samesite='Strict'
    )
    return response


@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    """Logout and invalidate session."""
    token = get_auth_token()
    if token and token in session_cache:
        del session_cache[token]

    response = make_response(jsonify({'success': True}))
    response.delete_cookie('mrem_token')
    return response


# API Routes
@app.route('/api/status', methods=['GET'])
@require_auth
def get_status():
    """Get current medication status."""
    try:
        status = get_medication_status()
        return jsonify(status)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/version', methods=['GET'])
def get_version():
    """Get current app version."""
    return jsonify({'version': APP_VERSION})


@app.route('/api/snooze', methods=['POST'])
@require_auth
def snooze_medication():
    """Snooze a medication for 5 minutes."""
    try:
        data = request.get_json()

        if not data:
            return jsonify({'error': 'No data provided'}), 400

        medication = data.get('medication')
        scheduled_time = data.get('time')

        valid, error = validate_medication_input(medication, scheduled_time)
        if not valid:
            return jsonify({'error': error}), 400

        # Set snooze for 5 minutes
        snooze_until = datetime.now() + timedelta(minutes=5)
        snooze_cache[(medication, scheduled_time)] = snooze_until

        return jsonify({
            'success': True,
            'snooze_until': snooze_until.strftime('%H:%M')
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/confirm', methods=['POST'])
@require_auth
def confirm_intake():
    """Confirm medication intake."""
    try:
        data = request.get_json()

        if not data:
            return jsonify({'error': 'No data provided'}), 400

        medication = data.get('medication')
        scheduled_time = data.get('time')

        valid, error = validate_medication_input(medication, scheduled_time)
        if not valid:
            return jsonify({'error': error}), 400

        # Check if already taken today
        if was_taken_today(medication, scheduled_time):
            return jsonify({'error': 'Already taken today', 'duplicate': True}), 409

        # Log the intake
        conn = get_db()
        actual_time = datetime.now().strftime('%H:%M:%S')

        conn.execute('''
            INSERT INTO intake_log (medication, scheduled_time, actual_time, status)
            VALUES (?, ?, ?, 'taken')
        ''', (medication, scheduled_time, actual_time))

        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'medication': medication,
            'scheduled_time': scheduled_time,
            'actual_time': actual_time
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/config', methods=['GET'])
@require_auth
def get_config():
    """Get current configuration."""
    try:
        config = load_config()
        return jsonify(config)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/config', methods=['POST'])
@require_auth
def update_config():
    """Update configuration."""
    try:
        data = request.get_json()

        if not data:
            return jsonify({'error': 'No data provided'}), 400

        # Validate structure
        if 'medications' not in data or 'settings' not in data:
            return jsonify({'error': 'Invalid config structure'}), 400

        save_config(data)
        return jsonify({'success': True, 'config': data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/history', methods=['GET'])
@require_auth
def get_history():
    """Get intake history."""
    try:
        days = request.args.get('days', 7, type=int)
        # Validate days parameter
        if days < 1 or days > 365:
            days = 7
        conn = get_db()

        cursor = conn.execute('''
            SELECT * FROM intake_log
            WHERE created_at >= date('now', ?)
            ORDER BY created_at DESC
        ''', (f'-{days} days',))

        rows = cursor.fetchall()
        conn.close()

        history = [dict(row) for row in rows]
        return jsonify({'history': history, 'days': days})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Desktop shortcut download
APP_HOST = os.environ.get('APP_HOST', 'reminder.heydtmann.eu')


@app.route('/api/shortcut.vbs')
def download_shortcut():
    """Download VBScript to launch status popup in --app mode."""
    # Use configured host to prevent Host header injection
    url = f"https://{APP_HOST}/status.html?popup=1"

    vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.Run """" & WshShell.ExpandEnvironmentStrings("%ProgramFiles(x86)%") & "\\Microsoft\\Edge\\Application\\msedge.exe"" --app={url}", 0, False
'''

    response = make_response(vbs_content)
    response.headers['Content-Type'] = 'application/octet-stream'
    response.headers['Content-Disposition'] = 'attachment; filename="Reminder-Popup.vbs"'
    return response


# Frontend serving
@app.route('/')
def serve_index():
    """Serve main HTML file."""
    response = send_from_directory(FRONTEND_DIR, 'index.html')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


@app.route('/<path:filename>')
def serve_static(filename):
    """Serve static files."""
    response = send_from_directory(FRONTEND_DIR, filename)
    # No cache for JS and service worker
    if filename.endswith(('.js', '.json', '.html')):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


if __name__ == '__main__':
    init_db()
    print(f"Database path: {DB_PATH}")
    print(f"Config path: {CONFIG_PATH}")
    print(f"Frontend directory: {FRONTEND_DIR}")
    app.run(host='0.0.0.0', port=5001, debug=os.environ.get('DEBUG', 'false').lower() == 'true')
