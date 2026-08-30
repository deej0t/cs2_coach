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
RUN git clone -b "$CS2COACH_BRANCH" \
    https://github.com/deej0t/cs2_coach.git .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Entrypoint and update script
RUN cp docker-entrypoint.sh /usr/local/bin/ && chmod +x /usr/local/bin/docker-entrypoint.sh
RUN cp docker-update.sh /usr/local/bin/ && chmod +x /usr/local/bin/docker-update.sh

# Create directories for mounted volumes
RUN mkdir -p /data/demos /data/vault /data/cfg

# Environment
ENV CS2COACH_CONFIG=/data/config.yaml
ENV PYTHONUNBUFFERED=1

VOLUME ["/data"]
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/')" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]

# Production server via gunicorn
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "2", \
     "--threads", "4", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "wsgi:app"]
