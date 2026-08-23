# CS2 Coach — Practice Server (Docker/Unraid)

Privater CS2 Practice-Server der aus deinen analysierten Demos lernt.

## Quick Start (Unraid)

### 1. Docker Compose installieren
In Unraid: **Apps** > Suche nach "Docker Compose Manager" > Installieren

### 2. Server starten
```bash
# Auf dem Unraid-Server:
cd /mnt/user/appdata/cs2-coach/docker
docker-compose up -d

# Erster Start dauert ~10-15 Min (CS2 Download ~35GB)
docker-compose logs -f cs2-practice
```

### 3. Practice Configs generieren
Im CS2-Coach Web-UI unter **Practice** > Configs generieren.
Die `.cfg` Dateien werden in `docker/cfg/` geschrieben.

### 4. Verbinden
```
# In CS2 Konsole:
connect 192.168.188.71:27015
```

### 5. Practice starten
```
# In CS2 Konsole auf dem Server:
exec coach/practice_mirage    // Prefire Practice Mirage
exec coach/practice_cache     // Prefire Practice Cache
exec coach/warmup             // Allgemeines Warmup

// Bots platzieren:
bot_add_t                     // Bot hinzufuegen
bot_place                     // Bot an Crosshair platzieren
bot_stop 1                    // Bots einfrieren
bot_stop 0                    // Bots laufen lassen
mp_restartgame 1              // Runde neustarten
```

## Konfiguration

### Ports
| Port | Protokoll | Funktion |
|------|-----------|----------|
| 27015 | TCP/UDP | Game Server |
| 27020 | TCP | RCON |

### Volumes
| Pfad | Beschreibung |
|------|-------------|
| `./cfg/` | Practice Configs (von cs2-coach generiert) |
| `cs2-data` | CS2 Server-Daten (persistent) |

### RCON
Standard-Passwort: `coach2024` (in docker-compose.yml aendern)

```
# In CS2 Konsole:
rcon_password coach2024
rcon changelevel de_mirage
rcon exec coach/practice_mirage
```

## Ressourcen
- CPU: ~2 Cores
- RAM: ~3-4 GB
- Disk: ~35 GB (CS2 Dedicated Server)
- Kein GPU noetig (Dedicated Server ist headless)
