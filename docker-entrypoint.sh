#!/bin/sh
set -e

CONFIG="${CS2COACH_CONFIG:-/data/config.yaml}"

# Create config from example if it doesn't exist
if [ ! -f "$CONFIG" ]; then
    echo "Erstelle config.yaml aus Vorlage..."
    cp /app/config.yaml.example "$CONFIG"

    # Apply Docker-specific defaults
    sed -i "s|^demo_folder:.*|demo_folder: /data/demos|" "$CONFIG"
    sed -i "s|^obsidian_vault_path:.*|obsidian_vault_path: /data/vault|" "$CONFIG"

    echo "Config erstellt: $CONFIG"
    echo "Passe die Einstellungen im Web-UI unter /settings an."
fi

# Ensure data directories exist
mkdir -p /data/demos /data/vault /data/cfg

# Set practice_cfg_path to shared volume
if ! grep -q "practice_cfg_path" "$CONFIG" 2>/dev/null; then
    echo "practice_cfg_path: /data/cfg" >> "$CONFIG"
fi

# Fehlende Python-Pakete nachinstallieren.
#
# Das Image installiert die Abhaengigkeiten nur beim Bauen. Kommt spaeter
# eine dazu, installiert docker-update.sh sie nur, wenn sich
# requirements.txt genau in diesem Update-Lauf geaendert hat. Auf dem
# Server fehlte dadurch qrcode (QR-Anmeldung), weil das Image vom August
# stammte und nie ein Update-Lauf die Aenderung gesehen hat. Ein
# Fehlschlag hier darf den Start nicht verhindern.
python - <<'PY' || echo "WARNUNG: Abhaengigkeiten konnten nicht geprueft werden."
import importlib.metadata as md, re, subprocess, sys
reqs = [l.strip() for l in open("/app/requirements.txt")
        if l.strip() and not l.startswith(("#", "-"))]
missing = []
for r in reqs:
    try:
        md.version(re.split(r"[<>=!~ \[]", r)[0])
    except md.PackageNotFoundError:
        missing.append(r)
if missing:
    print("Installiere fehlende Pakete:", ", ".join(missing), flush=True)
    rc = subprocess.call([sys.executable, "-m", "pip", "install",
                          "--no-cache-dir", "-q", *missing])
    print("Installation", "ok" if rc == 0 else f"FEHLGESCHLAGEN (Exit {rc})", flush=True)
PY

# Auto-Update (Standard: alle 6 Stunden, 0 = deaktiviert).
#
# Dieses Skript laeuft aus /app, damit Aenderungen per git pull ankommen.
# Frueher kopierte das Dockerfile es beim Bauen nach /usr/local/bin; der
# Container startete dann mit dem Stand vom Bau des Images. Auf dem
# Server war das ein Stand ohne diese Schleife - das Auto-Update ist dort
# nie gelaufen.
#
# Erste Pruefung 5 Minuten nach dem Start, nicht erst nach dem vollen
# Intervall: nach einem Neustart des Servers sonst bis zu 6 Stunden
# veraltet. docker-update.sh laedt Gunicorn per HUP an PID 1 neu und darf
# deshalb erst laufen, wenn exec unten Gunicorn zu PID 1 gemacht hat.
UPDATE_INTERVAL="${CS2COACH_UPDATE_INTERVAL:-21600}"
if [ "$UPDATE_INTERVAL" -gt 0 ] 2>/dev/null; then
    echo "Auto-Update aktiv: erste Pruefung in 300s, dann alle ${UPDATE_INTERVAL}s"
    (sleep 300
     while true; do
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Auto-Update Check..."
        /app/docker-update.sh || true
        sleep "$UPDATE_INTERVAL"
    done) &
fi

exec "$@"
