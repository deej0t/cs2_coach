#!/bin/bash
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BRANCH="${CS2COACH_BRANCH:-main}"
LOG="$REPO_DIR/update.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

cd "$REPO_DIR"

log "Prüfe auf Updates (Branch: $BRANCH)..."

git fetch origin "$BRANCH" --quiet

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")

if [ "$LOCAL" = "$REMOTE" ]; then
    log "Bereits aktuell ($LOCAL)."
    exit 0
fi

log "Update verfügbar: $LOCAL -> $REMOTE"

# Stash local changes if any
if ! git diff --quiet 2>/dev/null; then
    log "Lokale Änderungen gesichert (git stash)."
    git stash --quiet
    STASHED=1
fi

git pull origin "$BRANCH" --quiet
log "Update erfolgreich: $(git log --oneline -1)"

# Reinstall dependencies if requirements changed
if git diff "$LOCAL" "$REMOTE" --name-only | grep -q "requirements"; then
    log "requirements geändert — installiere Abhängigkeiten..."
    pip install -r requirements.txt --quiet 2>/dev/null || true
fi

# Restore stashed changes
if [ "${STASHED:-0}" = "1" ]; then
    git stash pop --quiet 2>/dev/null && log "Lokale Änderungen wiederhergestellt." || log "WARNUNG: Stash-Konflikt — manuelle Lösung nötig (git stash list)."
fi

log "Fertig."
