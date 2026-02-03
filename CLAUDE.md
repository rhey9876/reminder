# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Reminder PWA - A Progressive Web App for medication intake reminders with email-based authentication. German language UI.

## Commands

### Local Development
```bash
cd backend
pip install -r requirements.txt
DATA_DIR=./data AUTH_ENABLED=false python app.py
# App runs at http://localhost:5001, serves frontend automatically
```

### Docker Deployment
```bash
# Build and push image
docker build -t registry.heydtmann.eu:5000/reminder:latest ./backend
docker push registry.heydtmann.eu:5000/reminder:latest

# Deploy to swarm
docker stack deploy -c /data/TheShare/prod-mgmt/reminder.stack reminder

# Restart service after code changes (code is bind-mounted)
docker service update reminder_reminder --force

# View logs
docker service logs reminder_reminder --tail 50
```

### Test API
```bash
docker exec $(docker ps -q -f name=reminder_reminder) python3 -c "
import urllib.request
print(urllib.request.urlopen('http://localhost:5001/api/auth/check').read().decode())
"
```

## Architecture

### Backend (`backend/app.py`)
Single Flask application serving both API and static frontend files.

- **Data storage**: SQLite database at `DATA_DIR/intake_log.db`
- **Config**: YAML file at `DATA_DIR/reminder.yaml` (re-read on each request)
- **Snooze**: In-memory cache `snooze_cache` - lost on restart (intentional, only 5min snoozes)
- **Auth**: Email OTP authentication with in-memory session cache (30 day sessions)
- **Secrets**: Reads `PROD.MAIL_PASSWORD` and `PROD.FLASK_SECRET_KEY` from `/run/secrets/noreply`
- **Version**: Auto-computed MD5 hash from source files for cache invalidation

Key functions:
- `get_medication_status()`: Core logic - calculates overdue/due/upcoming based on current time, scheduled times, taken status, and snooze status
- `is_scheduled_today()`: Weekday filtering (supports German/English day names)
- `require_auth`: Decorator to protect API endpoints

### Frontend (`frontend/`)
Vanilla JavaScript PWA, no build step required.

- `app.js`: Main application logic, renders medication cards, handles confirm/snooze
- `service-worker.js`: Caching, background status checks, version-based cache invalidation
- `index.html`: Main app page with embedded CSS
- `login.html`: Email OTP login page
- `status.html`: Minimal status popup for desktop use

### Status Popup (`frontend/status.html`)
Minimal desktop indicator designed for Chromium/Edge --app mode:
- **Green**: All OK, shows next scheduled time
- **Orange**: Due (within reminder_window)
- **Red flashing**: Overdue (past reminder_window)
- Click on time (orange/red) = ACK (snooze 5min)
- Click elsewhere = open main app
- Dynamic favicon changes color with status
- Sends notifications on status change

Click actions (orange/red state):
- Click on **time** = Snooze (5 min)
- Click on **✓** = Confirm intake

Desktop shortcuts:
```bash
# Linux
chromium --app="https://reminder.heydtmann.eu/status.html?popup=1" --window-size=50,50

# Windows (Edge)
msedge --app=https://reminder.heydtmann.eu/status.html?popup=1
```

### API Endpoints

**Auth (no authentication required):**
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/auth/check` | Check if authenticated |
| POST | `/api/auth/request` | Request OTP for email |
| POST | `/api/auth/verify` | Verify OTP, create session |
| POST | `/api/auth/logout` | Logout, invalidate session |

**Protected (require authentication):**
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/status` | Current medication status (overdue/due/upcoming) |
| POST | `/api/confirm` | Confirm medication intake |
| POST | `/api/snooze` | Snooze medication for 5 minutes |
| GET | `/api/version` | App version hash for cache invalidation |
| GET | `/api/config` | Get medications config |
| POST | `/api/config` | Update medications config |
| GET | `/api/history` | Intake history |

## File Locations (Production)

- Stack file: `/data/TheShare/prod-mgmt/reminder.stack`
- Data volume: `/data/dockervolumes/reminder/`
  - `reminder.yaml` - medication and auth configuration
  - `intake_log.db` - SQLite database
- Source code: `/home/rdpuser/projects/reminder/` (bind-mounted)
- Docker secret: `noreply` (contains PROD.MAIL_PASSWORD, PROD.FLASK_SECRET_KEY)

## Config Format (`reminder.yaml`)

```yaml
medications:
  - name: "Short Name"      # Displayed in app
    full: "Full description" # Optional
    times: ["08:00", "20:00"]
    days: ["Mo", "Mi", "Fr"]  # Optional, omit for daily
    enabled: true

settings:
  reminder_window: 15  # Minutes before/after scheduled time
  timezone: "Europe/Berlin"

auth:
  allowed_emails:
    - "user@example.com"
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTH_ENABLED` | `true` | Enable email OTP authentication |
| `ALLOWED_ORIGINS` | `https://reminder.heydtmann.eu` | CORS allowed origins (comma-separated) |
| `APP_HOST` | `reminder.heydtmann.eu` | Host for generated shortcuts |
| `SMTP_HOST` | `postfix-mailcow` | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP server port |
| `SMTP_SKIP_VERIFY` | `false` | Skip SSL cert verification (for self-signed) |
| `MAIL_FROM` | `noreply@heydtmann.eu` | Email sender address |
| `MAIL_USER` | `noreply@heydtmann.eu` | SMTP username |

## Security

- **Rate Limiting**: `/api/auth/request` limited to 5 requests per 5 minutes per IP
- **Input Validation**: Medication names max 100 chars, time format HH:MM enforced
- **Session Cookies**: httponly, secure, samesite=Strict
- **OTP**: 6-digit, 5-minute expiry, max 3 attempts

## Networks

Service requires two overlay networks:
- `test-o`: Main application network (reverse proxy access)
- `ynet-o`: Mail server access (postfix-mailcow)
