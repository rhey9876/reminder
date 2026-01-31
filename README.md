# Medikamenten-Erinnerung PWA

Eine Progressive Web App zur Erinnerung an die Medikamenteneinnahme.

## Features

- Mobile-first Design mit responsivem Layout
- Offline-fähig dank Service Worker
- Push-Benachrichtigungen für überfällige Medikamente
- Einfache Bestätigung der Einnahme
- Persistente Speicherung in SQLite
- YAML-basierte Konfiguration

## Projektstruktur

```
medication-reminder/
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app.py
│   └── medications.yaml.example
├── frontend/
│   ├── index.html
│   ├── app.js
│   ├── service-worker.js
│   └── manifest.json
├── docker-compose.yml
├── .gitignore
└── README.md
```

## Deployment (Docker Swarm)

### Voraussetzungen

- Docker Swarm initialisiert
- Externes Overlay-Netzwerk `test-o` vorhanden
- Node mit Hostname `pi6` im Swarm

### 1. Repository klonen

```bash
git clone <repository-url>
cd medication-reminder
```

### 2. Konfiguration erstellen

```bash
# Beispielkonfiguration kopieren und anpassen
cp backend/medications.yaml.example backend/medications.yaml

# Optional: Konfiguration bearbeiten
nano backend/medications.yaml
```

### 3. Image bauen

```bash
docker build -t medication-reminder:latest ./backend
```

### 4. Stack deployen

```bash
docker stack deploy -c docker-compose.yml medication
```

### 5. Status prüfen

```bash
docker service ls
docker service logs medication_medication-reminder
```

## Konfiguration

Die Medikamentenkonfiguration erfolgt über `/data/medications.yaml`:

```yaml
medications:
  - name: "Blutdrucktabletten"
    times: ["08:00", "20:00"]
    enabled: true
  - name: "Vitamin D"
    times: ["12:00"]
    enabled: true

settings:
  reminder_window: 30  # Minuten vor/nach der geplanten Zeit
  timezone: "Europe/Berlin"
```

## API Endpunkte

| Methode | Endpunkt | Beschreibung |
|---------|----------|--------------|
| GET | `/api/status` | Aktueller Medikamentenstatus |
| POST | `/api/confirm` | Einnahme bestätigen |
| GET | `/api/config` | Konfiguration abrufen |
| POST | `/api/config` | Konfiguration aktualisieren |
| GET | `/api/history` | Einnahme-Historie |

### Beispiel: Status abrufen

```bash
curl http://localhost:5000/api/status
```

Antwort:
```json
{
  "overdue": [...],
  "due": [...],
  "upcoming": [...],
  "timestamp": "2024-01-15T14:30:00",
  "settings": {...}
}
```

### Beispiel: Einnahme bestätigen

```bash
curl -X POST http://localhost:5000/api/confirm \
  -H "Content-Type: application/json" \
  -d '{"medication": "Blutdrucktabletten", "time": "08:00"}'
```

## Reverse Proxy (Nginx Proxy Manager)

Um die App über HTTPS erreichbar zu machen:

1. In NPM einen neuen Proxy Host erstellen
2. Domain eintragen (z.B. `medikamente.example.com`)
3. Forward zu: `medication_medication-reminder:5000`
4. SSL aktivieren

Alternativ Port in docker-compose.yml freigeben:
```yaml
ports:
  - "5000:5000"
```

## Lokale Entwicklung

```bash
# Backend starten
cd backend
pip install -r requirements.txt
DATA_DIR=./data python app.py

# Frontend wird automatisch unter http://localhost:5000 ausgeliefert
```

## Updates

```bash
# Neues Image bauen
docker build -t medication-reminder:latest ./backend

# Service aktualisieren
docker service update --force medication_medication-reminder
```

## Backup

Die persistenten Daten befinden sich im Docker Volume `medication_medication-data`:

```bash
# Backup erstellen
docker run --rm -v medication_medication-data:/data -v $(pwd):/backup alpine tar czf /backup/medication-backup.tar.gz /data

# Backup wiederherstellen
docker run --rm -v medication_medication-data:/data -v $(pwd):/backup alpine tar xzf /backup/medication-backup.tar.gz -C /
```

## Lizenz

MIT
