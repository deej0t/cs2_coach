#!/bin/bash
set -e

BRANCH="${CS2COACH_BRANCH:-main}"
APP_DIR="/app"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

cd "$APP_DIR"

log "Prüfe auf Updates (Branch: $BRANCH)..."
git fetch origin "$BRANCH" --quiet

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")

if [ "$LOCAL" = "$REMOTE" ]; then
    log "Bereits aktuell ($(git rev-parse --short HEAD))."
    exit 0
fi

COMMITS=$(git log --oneline "$LOCAL".."$REMOTE" | wc -l)
log "Update: $COMMITS neue Commits"
git log --oneline "$LOCAL".."$REMOTE"

# Hart auf den Remote-Stand setzen statt zu mergen. /app ist ein
# Auslieferungsziel, kein Arbeitsverzeichnis - lokale Aenderungen sind
# dort immer unerwuenscht.
#
# Vorher standen hier "git checkout -- ." und "git pull". Das ist an einer
# blossen Modusaenderung haengengeblieben: das chmod unten setzt das
# Ausfuehrbar-Bit, git verfolgt den Dateimodus, und jeder weitere pull
# brach mit "Your local changes would be overwritten" ab - bei null
# Inhaltsaenderung. Der Container stand dadurch vier Wochen auf demselben
# Commit, ohne dass es auffiel: die Meldung landete nur im Container-Log.
if ! git reset --hard -q "origin/$BRANCH"; then
    log "FEHLER: Konnte nicht auf origin/$BRANCH zuruecksetzen."
    exit 1
fi
chmod +x /app/docker-update.sh /app/docker-entrypoint.sh 2>/dev/null
log "Code aktualisiert: $(git log --oneline -1)"

# Reinstall dependencies if requirements changed
if git diff "$LOCAL" "$REMOTE" --name-only | grep -q "requirements"; then
    log "requirements geändert — installiere Abhängigkeiten..."
    pip install -r requirements.txt --quiet
fi

# Reload gunicorn (PID 1 via exec in entrypoint, graceful restart)
if kill -HUP 1 2>/dev/null; then
    log "Gunicorn neu geladen (PID 1)."
else
    log "HINWEIS: Gunicorn konnte nicht neu geladen werden — Container-Neustart nötig."
fi

log "Fertig. Version: $(git rev-parse --short HEAD)"
