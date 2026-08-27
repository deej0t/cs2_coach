FROM python:3.11-slim

LABEL maintainer="deej0t"
LABEL description="CS2 Coach — Web-Interface fuer Demo-Analyse und Coaching"

# System dependencies for demoparser2
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copy application code
COPY cs2_coach/ cs2_coach/
COPY config.yaml.example .

# Create directories for mounted volumes
RUN mkdir -p /data/demos /data/exports /data/vault

# Default config path — mount your config.yaml here
ENV CS2COACH_CONFIG=/app/config.yaml

EXPOSE 5000

# Run with gunicorn for production
CMD ["python", "-m", "cs2_coach", "web", "--host", "0.0.0.0", "--port", "5000"]
