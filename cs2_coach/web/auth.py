"""Optionaler Passwortschutz fuer die Web-UI.

Ein einzelnes Passwort schuetzt die gesamte App. Ist keines gesetzt, bleibt
die Authentifizierung vollstaendig deaktiviert - so sperrt ein Update
niemanden aus seiner laufenden Instanz aus.

Password source precedence:
    1. CS2COACH_PASSWORD (env, plaintext - hashed in memory at startup)
    2. cfg["auth"]["password_hash"] (config.yaml)
    3. nothing -> auth disabled
"""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

import yaml
from werkzeug.security import check_password_hash, generate_password_hash

# Endpoints reachable without a session. "static" is required so the login
# page can load its own CSS; "share_card" keeps match share links public.
PUBLIC_ENDPOINTS = {"login", "static", "share_card"}

# Brute-force throttle: after MAX_ATTEMPTS failures from one remote address,
# further attempts are refused for LOCKOUT_SECONDS.
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 300

_failed: dict[str, list] = {}  # ip -> [attempt_count, first_attempt_ts]


def load_or_create_secret_key(config_path: Path) -> bytes:
    """Return a stable Flask secret key.

    A random per-process key would break sessions immediately: gunicorn runs
    several workers, so a cookie signed by one worker would be rejected by the
    next. The key is therefore persisted next to the config file.

    Uses O_CREAT|O_EXCL so that concurrent workers starting at the same time
    cannot each generate a different key - exactly one creates it, the others
    read what the winner wrote.
    """
    env_key = os.environ.get("CS2COACH_SECRET_KEY", "").strip()
    if env_key:
        return env_key.encode("utf-8")

    key_path = config_path.parent / ".secret_key"
    try:
        fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    except OSError:
        # Read-only filesystem or similar - fall back to an ephemeral key.
        return secrets.token_bytes(32)
    else:
        key = secrets.token_hex(32).encode("ascii")
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        return key

    # Another process created the file; it may not have finished writing yet.
    for _ in range(50):
        try:
            data = key_path.read_bytes().strip()
        except OSError:
            data = b""
        if data:
            return data
        time.sleep(0.02)
    return secrets.token_bytes(32)


# The password hash is read from disk rather than from an in-memory config
# dict: gunicorn runs several workers, and a password set through the UI only
# mutates the dict of the worker that handled that request. The others would
# keep serving with auth disabled, and a login landing on such a worker would
# redirect without ever establishing a session. Cached on the config file's
# mtime so this costs one stat() per request.
_env_hash: str | None = None
_cache_mtime: int | None = None
_cache_hash: str = ""


def get_password_hash(config_path: Path) -> str:
    """Return the configured password hash, or "" if auth is off."""
    global _env_hash, _cache_mtime, _cache_hash

    env_pw = os.environ.get("CS2COACH_PASSWORD", "")
    if env_pw:
        # Hashing is deliberately slow - do it once, not per request.
        if _env_hash is None:
            _env_hash = generate_password_hash(env_pw)
        return _env_hash

    try:
        mtime = config_path.stat().st_mtime_ns
    except OSError:
        return ""

    if mtime != _cache_mtime:
        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}
        auth_cfg = data.get("auth") or {}
        _cache_hash = (
            (auth_cfg.get("password_hash") or "").strip()
            if isinstance(auth_cfg, dict) else ""
        )
        _cache_mtime = mtime

    return _cache_hash


def is_enabled(config_path: Path) -> bool:
    """True if a password is configured and the app should require login."""
    return bool(get_password_hash(config_path))


def verify_password(config_path: Path, password: str) -> bool:
    stored = get_password_hash(config_path)
    if not stored or not password:
        return False
    try:
        return check_password_hash(stored, password)
    except Exception:
        return False


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def lockout_remaining(ip: str) -> int:
    """Seconds until *ip* may try again, or 0 if it is not locked out."""
    entry = _failed.get(ip)
    if not entry:
        return 0
    count, first = entry
    if count < MAX_ATTEMPTS:
        return 0
    elapsed = time.time() - first
    if elapsed >= LOCKOUT_SECONDS:
        _failed.pop(ip, None)
        return 0
    return int(LOCKOUT_SECONDS - elapsed)


def record_failure(ip: str) -> None:
    entry = _failed.get(ip)
    now = time.time()
    if not entry or now - entry[1] >= LOCKOUT_SECONDS:
        _failed[ip] = [1, now]
    else:
        entry[0] += 1


def record_success(ip: str) -> None:
    _failed.pop(ip, None)
