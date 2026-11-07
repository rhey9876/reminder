# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Reminder PWA - Progressive Web App for medication intake reminders with email OTP authentication. German language UI, designed for personal use.

**Key characteristics:**
- Single-file Flask backend serving both API and static files
- Vanilla JS frontend (no build step)
- SQLite for persistence, YAML for config
- Email OTP auth with 30-day sessions; trusted local networks bypass auth entirely
- Designed for Docker Swarm deployment with bind-mounted code

## Commands

### Local Development
```bash
cd backend
pip install -r requirements.txt
DATA_DIR=./data AUTH_ENABLED=false python reminder.py
# Backend at http://localhost:5001, serves frontend automatically
```

### Docker Deployment (Production)
```bash
# Build and push image (required when reminder.py or Dockerfile changes)
docker build -t registry.heydtmann.eu:5000/reminder:latest ./backend
docker push registry.heydtmann.eu:5000/reminder:latest

# Deploy stack (initial deployment or after stack file changes)
docker stack deploy -c /data/TheShare/prod-mgmt/reminder.stack reminder

# Restart service (frontend/config changes only — code is bind-mounted)
docker service update reminder_reminder --force

# Check status and logs
docker service ps reminder_reminder
docker service logs reminder_reminder --tail 50 --follow
```

### Test API Endpoints
```bash
# Check auth status (trusted_network: true when called from LAN)
curl https://reminder.heydtmann.eu/api/auth/check

# Test status endpoint (requires auth or trusted network)
curl https://reminder.heydtmann.eu/api/status

# From inside container
docker exec $(docker ps -q -f name=reminder_reminder) \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://localhost:5001/api/auth/check').read().decode())"
```

## Architecture

### Core Design Pattern
**Single responsibility per file:**
- `backend/reminder.py` - Complete backend (API + file serving)
- `frontend/app.js` - Main app logic
- `frontend/service-worker.js` - Offline caching + background checks
- `frontend/login.html` - Standalone OTP login flow
- `frontend/status.html` - Minimal desktop status indicator

### Backend (`backend/reminder.py`)
Flask app serving both API endpoints and static frontend files. No database migrations, no ORM.

**State management:**
- **Persistent**: SQLite (`intake_log.db`), YAML config (`reminder.yaml`)
- **In-memory**: OTP cache, session cache (30-day tokens), snooze cache (5min), rate limits
- **Config re-read**: Every request reads fresh YAML (intentional for easy editing)

**Critical functions:**
- `get_medication_status()` - Core business logic: calculates overdue/due/upcoming by comparing current time against scheduled times, checking taken status, and respecting snooze
- `is_scheduled_today()` - Weekday filtering supporting German/English day names
- `is_trusted_ip()` - Returns True for 127.x, 192.168.x, 172.16.x, 10.x — these bypass auth
- `require_auth` - Decorator: skips auth if `AUTH_ENABLED=false` OR client IP is trusted, otherwise validates session cookie/Bearer token
- `checkAuth()` in frontend - MUST complete before app initialization; retries up to 5× for VPN/DNS delays

**Auth flow (internet access):**
1. User requests OTP → rate-limited, sends email
2. User submits OTP → validates, creates session token
3. Server sets httponly cookie with 30-day expiry
4. All protected endpoints check cookie via `@require_auth`

**Auth flow (local network — home LAN or company LAN):**
- Client IP is checked against trusted ranges on every request
- Trusted IPs bypass OTP/cookie entirely — `auth_check` returns `{authenticated: true, trusted_network: true}`
- No session token needed, no login page shown

### Frontend Architecture

**Three separate HTML pages (no SPA router):**

1. **`index.html` + `app.js`** - Main medication management
   - Checks auth on load, redirects to login if needed
   - All API calls go through `apiFetch()` (see VPN section below)
   - Fetches `/api/status` every 60s
   - Renders medication cards in 3 sections: overdue/due/upcoming
   - Confirm = POST to `/api/confirm` → logs intake
   - Snooze = POST to `/api/snooze` → 5min delay
   - Shows version hash (`/api/version`) in footer after load

2. **`login.html`** - Email OTP flow
   - Step 1: Email input → POST `/api/auth/request`
   - Step 2: 6-digit code → POST `/api/auth/verify` → sets cookie → redirect to `/`
   - Already authenticated users auto-redirect to main app

3. **`status.html`** - Minimal always-on-top indicator
   - Designed for `--app` mode (borderless window)
   - Color indicates status: green (ok), orange (due), red flashing (overdue)
   - Click on time = snooze, click ✓ = confirm
   - Dynamic favicon changes with status
   - Sends desktop notifications on status change
   - Silently retries every 30s on error (intentional — handles transient VPN DNS failures)

**Service Worker (`service-worker.js`):**
- Caches static assets for offline use (allows page to load even when DNS is failing)
- **Version-based invalidation**: Checks `/api/version` (MD5 hash of source files), clears cache on change
- Background status checks every 5 minutes (only if authenticated)
- Must check auth BEFORE fetching `/api/status` to avoid 401 errors
- Cache name: `reminder-v8` — increment this to force immediate cache refresh on all clients

### VPN / DNS Handling

Corporate VPNs often route DNS through the tunnel where external domains are blocked. The app handles this at two levels:

**Backend — trusted network bypass:**
- Requests from `10.0.0.0/8` (company LAN / VPN tunnel IPs) bypass authentication entirely
- Same for `192.168.0.0/16` (home LAN), `172.16.0.0/12`, `127.0.0.0/8`
- Implemented in `is_trusted_ip()` + `require_auth` decorator + `auth_check` endpoint

**Frontend — connection retry:**
- All API calls use `apiFetch()` instead of `fetch()` directly
- `checkAuth()` retries up to 5 times with 4-second gaps; shows progress in the loading spinner text: *"Verbindung wird hergestellt… (2/5)"*
- On persistent network error, `apiFetch()` attempts Quad9 DNS-over-HTTPS (`dns.quad9.net`) to resolve the hostname, then retries using the resolved IP (works if VPN blocks DNS but allows HTTPS; may fail if TLS cert doesn't cover the IP)
- If all attempts fail, error banner shows the resolved IP and suggests enabling browser DoH

**Recommended fix for company VPN (no admin rights needed):**
- Chrome/Edge: Settings → Privacy → Security → "Use secure DNS" → Custom → `https://dns.quad9.net/dns-query`
- Firefox: Settings → Network Settings → Enable DNS over HTTPS → Quad9
- Once browser uses DoH, DNS is resolved over HTTPS (port 443) bypassing VPN DNS blocking, and the app connects normally

### API Design

**Auth endpoints (no auth required):**
- `GET /api/auth/check` - Returns `{authenticated, auth_enabled, email?, trusted_network?}`
- `POST /api/auth/request` - Sends OTP email (rate-limited: 5 req/5min per IP)
- `POST /api/auth/verify` - Validates OTP, creates session, sets cookie
- `POST /api/auth/logout` - Deletes session, clears cookie

**Protected endpoints (require auth cookie OR trusted network IP):**
- `GET /api/status` - Main endpoint: `{overdue: [], due: [], upcoming: []}`
- `POST /api/confirm {medication, time}` - Logs intake to DB
- `POST /api/snooze {medication, time}` - Sets 5min snooze in memory
- `GET /api/version` - MD5 hash of source files for cache invalidation
- `GET /api/config` - Returns full YAML config
- `POST /api/config` - Updates YAML config
- `GET /api/history?days=7` - Returns intake log

### Deployment Architecture (Production)

**Docker Swarm setup:**
- Service name: `reminder_reminder` (from stack name + service name)
- Node constraint: `pi6` (Raspberry Pi 6)
- Networks: `test-o` (reverse proxy), `ynet-o` (mail server)
- Bind mounts: Code is mounted read-only for hot-reload without rebuild
- Secret: `/run/secrets/noreply` contains `PROD.MAIL_PASSWORD` and `PROD.FLASK_SECRET_KEY`

**When to rebuild the image vs. just restart:**
- `reminder.py` changed → rebuild + push + `stack deploy`
- `Dockerfile` or `requirements.txt` changed → rebuild + push + `stack deploy`
- Frontend files (`*.html`, `*.js`) changed → `service update --force` only (bind-mounted)
- Stack file (`reminder.stack`) changed → `stack deploy` only

**Data persistence:**
- Volume: `/data/dockervolumes/reminder/`
  - `reminder.yaml` - medication schedules + auth whitelist
  - `intake_log.db` - SQLite database with intake history

**Stack file location:** `/data/TheShare/prod-mgmt/reminder.stack`

### Configuration (`reminder.yaml`)

```yaml
medications:
  - name: "Med Name"        # Required, shown in UI
    full: "Full Name"       # Optional, for detailed view
    times: ["08:00", "20:00"]  # Required, HH:MM format
    days: ["Mo", "Mi", "Fr"]   # Optional, omit for daily
    enabled: true           # Optional, default true

settings:
  reminder_window: 15  # Minutes before/after = "due" state
  timezone: "Europe/Berlin"

auth:
  allowed_emails:
    - "user@example.com"  # Whitelist for OTP login (internet access only)
```

**Weekday names:** Supports German (Mo, Di, Mi, Do, Fr, Sa, So) and English (Mon, Tue, Wed, Thu, Fri, Sat, Sun), case-insensitive.

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `AUTH_ENABLED` | `true` | Toggle authentication (set `false` for local dev) |
| `ALLOWED_ORIGINS` | `https://reminder.heydtmann.eu` | CORS origins (comma-separated) |
| `APP_HOST` | `reminder.heydtmann.eu` | Host for generated .vbs shortcut |
| `DATA_DIR` | `/data` | Path for SQLite DB and YAML config |
| `SMTP_HOST` | `postfix-mailcow` | SMTP server for OTP emails |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_SKIP_VERIFY` | `false` | Skip SSL verification (self-signed certs) |
| `MAIL_FROM` | `noreply@heydtmann.eu` | Sender address |
| `MAIL_USER` | `noreply@heydtmann.eu` | SMTP username |
| `MAIL_PASSWORD` | from secret | SMTP password (set via secret or env) |
| `FLASK_SECRET_KEY` | from secret | Session signing key (required in production) |

## Security Features

- **Rate limiting**: 5 OTP requests per 5 minutes per IP (in-memory)
- **Input validation**: Medication names ≤100 chars, time format HH:MM enforced
- **Session cookies**: httponly, secure, samesite=Strict, 30-day expiry
- **OTP**: 6 digits, 5-minute expiry, max 3 attempts before invalidation
- **CORS**: Restricted to configured origins
- **Auth whitelist**: Only emails in config can request OTP
- **Trusted networks**: 10.x, 192.168.x, 172.16.x, 127.x bypass auth — these are considered physically secure

## Common Issues

**Retry loop on startup ("Verbindung wird hergestellt… 1/5"):**
- Cause: DNS resolution failing, typically corporate VPN routing DNS through tunnel
- Fix: Enable browser DNS-over-HTTPS pointing to Quad9 (no admin rights needed)
  - Chrome/Edge: Settings → Privacy → Security → Secure DNS → `https://dns.quad9.net/dns-query`
  - Firefox: Settings → Network Settings → DNS over HTTPS → Quad9
- The app retries 5× with 4s gaps before showing a hard error

**Login page appears despite being on home/company LAN:**
- Cause: Request not reaching server with correct client IP (reverse proxy config)
- Check: `curl https://reminder.heydtmann.eu/api/auth/check` — should show `trusted_network: true`
- Fix: Ensure `X-Forwarded-For` header is passed by reverse proxy (Traefik does this by default)

**Service Worker not updating / old version shown:**
- Increment `CACHE_NAME` in `service-worker.js` (currently `reminder-v8`)
- Hard refresh browser (`Ctrl+Shift+R`)
- Or: Unregister in DevTools → Application → Service Workers
