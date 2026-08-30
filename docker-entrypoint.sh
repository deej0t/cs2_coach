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

# Auto-update checker (default: alle 6 Stunden, 0 = deaktiviert)
UPDATE_INTERVAL="${CS2COACH_UPDATE_INTERVAL:-21600}"
if [ "$UPDATE_INTERVAL" -gt 0 ] 2>/dev/null; then
    echo "Auto-Update aktiv: prüfe alle ${UPDATE_INTERVAL}s"
    (while true; do
        sleep "$UPDATE_INTERVAL"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Auto-Update Check..."
        docker-update.sh || true
    done) &
fi

exec "$@"
