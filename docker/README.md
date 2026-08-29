# CS2 Coach — Docker / Unraid

Zwei Services:
- **cs2-coach** — Web-UI fuer Demo-Analyse, KI-Coaching, Practice-Config-Generator
- **cs2-practice** — CS2 Dedicated Server mit den generierten Practice-Configs (optional)

## Quick Start (Portainer auf Unraid)

### 1. Image bauen

Per SSH / Unraid Terminal:
```bash
cd /mnt/user/appdata/
git clone https://github.com/deej0t/cs2_coach.git cs2-coach
cd cs2-coach
docker build -t cs2-coach:latest .
```

### 2a. Portainer Stack (empfohlen)

1. Portainer oeffnen > **Stacks** > **Add stack**
2. Name: `cs2-coach`
3. **Web editor** > Inhalt von `docker/portainer-stack.yml` einfuegen
4. **Deploy the stack**
5. Oeffne http://UNRAID-IP:5000

Den CS2 Practice Server Block kann man auskommentieren wenn man nur die Web-UI braucht.

### 2b. Portainer — Einzelner Container (nur Web-UI)

1. Portainer > **Containers** > **Add container**
2. **Name:** `cs2-coach`
3. **Image:** `cs2-coach:latest`
4. **Port mapping:** Host `5000` → Container `5000`
5. **Volumes:** `/mnt/user/appdata/cs2-coach/data` → `/data`
6. **Env:**
   - `CS2COACH_CONFIG` = `/data/config.yaml`
   - `TZ` = `Europe/Berlin`
7. **Restart policy:** Unless stopped
8. **Deploy**

### 2c. Docker CLI (ohne Portainer)

```bash
# Nur Web-UI:
docker run -d \
  --name cs2-coach \
  --restart unless-stopped \
  -p 5000:5000 \
  -v /mnt/user/appdata/cs2-coach/data:/data \
  -e CS2COACH_CONFIG=/data/config.yaml \
  -e TZ=Europe/Berlin \
  cs2-coach:latest
```

### Image aktualisieren

```bash
cd /mnt/user/appdata/cs2-coach
git pull
docker build -t cs2-coach:latest .
# In Portainer: Container stoppen > Recreate
```

## Konfiguration

Beim ersten Start wird automatisch eine `config.yaml` in `/data/` erstellt.
Einstellungen koennen im Web-UI unter **Einstellungen** geaendert werden.

### Wichtige Einstellungen

| Einstellung | Beschreibung | Beispiel |
|-------------|-------------|---------|
| `demo_folder` | Pfad zu CS2 Demo-Dateien | `/data/demos` |
| `obsidian_vault_path` | Obsidian Vault fuer Exports | `/data/vault` |
| `player_name` | Dein Steam-Name | `deej0t` |
| `steam_id` | Deine SteamID64 | `76561198...` |
| `ai_provider` | KI-Backend | `gemini` oder `ollama` |
| `gemini_api_key` | Google Gemini API Key | `AIza...` |
| `ollama_url` | Ollama Server URL | `http://192.168.188.71:11434` |

### Demos einspielen

Demos muessen in `/data/demos` liegen. Optionen:

1. **Manuell kopieren:** Demos nach `/mnt/user/appdata/cs2-coach/demos/` kopieren
2. **Share mounten:** In docker-compose.yml den Demo-Ordner direkt mounten:
   ```yaml
   volumes:
     - /mnt/user/path/to/demos:/data/demos:ro
   ```
3. **Auto-Sync:** Steam-Credentials in den Einstellungen hinterlegen fuer automatischen Download

## Ports

| Port | Protokoll | Service | Funktion |
|------|-----------|---------|----------|
| 5000 | TCP | cs2-coach | Web-UI |
| 27015 | TCP/UDP | cs2-practice | CS2 Game Server |
| 27020 | TCP | cs2-practice | RCON |

## Volumes

| Volume | Pfad im Container | Beschreibung |
|--------|-------------------|-------------|
| cs2-coach-data | /data | Config, Demos, Vault, Uploads |
| cs2-coach-cfg | /data/cfg | Practice Configs (geteilt mit CS2 Server) |
| cs2-server-data | /home/steam/cs2-dedicated | CS2 Server-Daten (~35 GB) |

## CS2 Server verbinden

```
# In CS2 Konsole:
connect UNRAID-IP:27015

# Practice starten:
exec coach/practice           // Menue mit allen Modi
exec coach/practice_mirage    // Prefire Mirage
exec coach/retake_dust2       // Retake Dust2
exec coach/spray_inferno      // Spray-Transfer Inferno
exec coach/challenge_nuke     // Challenge Nuke
exec coach/utility            // Granaten-Training
exec coach/warmup             // Warmup
```

## RCON

Standard-Passwort: `coach2024` (in docker-compose.yml aendern!)

```
rcon_password coach2024
rcon changelevel de_mirage
rcon exec coach/practice_mirage
```

## Ressourcen

| Service | CPU | RAM | Disk |
|---------|-----|-----|------|
| cs2-coach | ~0.5 Cores | ~200 MB | ~100 MB |
| cs2-practice | ~2 Cores | ~4 GB | ~35 GB |

## Troubleshooting

### Container startet nicht
```bash
docker-compose logs cs2-coach
```

### Config zuruecksetzen
```bash
rm /mnt/user/appdata/cs2-coach/config.yaml
docker-compose restart cs2-coach
```

### CS2 Server Download-Schleife
Der CS2 Server braucht ~8 GB RAM beim ersten Download (SteamCMD verify).
Memory-Limit in docker-compose.yml auf mindestens 8G setzen.
