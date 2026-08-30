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

git pull origin "$BRANCH" --quiet
log "Code aktualisiert: $(git log --oneline -1)"

# Reinstall dependencies if requirements changed
if git diff "$LOCAL" "$REMOTE" --name-only | grep -q "requirements"; then
    log "requirements geändert — installiere Abhängigkeiten..."
    pip install -r requirements.txt --quiet
fi

# Reload gunicorn workers (graceful restart, no downtime)
# /proc is always available — no need for ps or pkill
GUNICORN_PID=""
for pid_dir in /proc/[0-9]*; do
    pid="${pid_dir##*/}"
    if grep -qs 'gunicorn' "$pid_dir/cmdline" 2>/dev/null; then
        # Master is PID 1 or lowest gunicorn PID
        GUNICORN_PID="$pid"
        break
    fi
done
if [ -n "$GUNICORN_PID" ] && kill -HUP "$GUNICORN_PID" 2>/dev/null; then
    log "Gunicorn neu geladen (PID $GUNICORN_PID)."
else
    log "HINWEIS: Gunicorn konnte nicht neu geladen werden — Container-Neustart nötig."
fi

log "Fertig. Version: $(git rev-parse --short HEAD)"
log "Update-Script Version: v2"
