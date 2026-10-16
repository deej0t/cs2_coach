FROM python:3.11-slim

LABEL maintainer="deej0t"
LABEL description="CS2 Coach — Demo-Analyse, KI-Coaching und Practice-Server-Konfigurator"
LABEL org.opencontainers.image.source="https://github.com/deej0t/cs2_coach"

# System dependencies for demoparser2 (needs Rust bindings compiled)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Clone repository
ARG CS2COACH_BRANCH=main
# Ohne diese Zeile cacht Docker den Clone-Schritt: ein Neubau am
# 2026-10-16 lieferte still den Stand von vor zwei Commits. ADD laedt den
# Branch-Stand bei jedem Build und invalidiert den Cache, sobald er sich
# aendert.
ADD https://api.github.com/repos/deej0t/cs2_coach/git/refs/heads/${CS2COACH_BRANCH} /tmp/branch-head.json
RUN git clone -b "$CS2COACH_BRANCH" \
    https://github.com/deej0t/cs2_coach.git .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Entrypoint und Update-Skript laufen direkt aus /app, damit git pull sie
# aktualisiert. Frueher wurde der Entrypoint hierher kopiert; ein Container
# startete dann fuer immer mit dem Stand vom Bau des Images - auf dem
# Server ein Stand ohne Auto-Update-Schleife. Das Ausfuehrbar-Bit steht in
# git; ein chmod hier machte den Baum schmutzig und blockierte git pull.
# Der Symlink bleibt fuer den manuellen Aufruf "docker exec ... docker-update.sh".
RUN ln -sf /app/docker-update.sh /usr/local/bin/docker-update.sh

# Create directories for mounted volumes
RUN mkdir -p /data/demos /data/vault /data/cfg

# Environment
ENV CS2COACH_CONFIG=/data/config.yaml
ENV PYTHONUNBUFFERED=1

VOLUME ["/data"]
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/')" || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]

# Production server via gunicorn
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "2", \
     "--threads", "4", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "wsgi:app"]
