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

git checkout -- . 2>/dev/null
git pull origin "$BRANCH" --quiet
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
