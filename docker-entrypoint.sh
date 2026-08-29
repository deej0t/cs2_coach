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

exec "$@"
