"""Mehrere Spieler auf einer Instanz: der Admin (die bisherige config.yaml) und Mates.

Ein Mate hat einen eigenen Login und eine eigene Steam-ID; alles, was ihm
gehoert, liegt unter ``users/<name>/`` neben der Config:

- ``config.yaml``: nur seine Schluessel (USER_KEYS) - Steam-ID, Steam-Sync,
  Benachrichtigungen, KI-Schluessel. Alles andere (Demo-Ordner, Server,
  Training) kommt aus der Admin-Config. Die persoenlichen Schluessel des
  Admins sieht ein Mate nie.
- ``vault/CS2-Coach/exports``: seine Exporte (die Leser finden sie ueber
  ``obsidian_vault_path`` wie beim Admin - kein Leser braucht Sonderfaelle).
- Zustandsdateien der Auswertungen (``.levers.json``, ``.aim_profile.json``,
  Briefing, ...): neben seiner ``config.yaml``, weil die Module
  ``Path(config_path).parent`` nehmen.

Demos und Caches, die nur von der Demo abhaengen (Siegchance, Aim-Duelle,
Szenen, Maps), liegen weiter einmal neben der Admin-Config: ``shared_dir``.

Ohne angelegte Mates aendert sich nichts.
"""

from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

USERS_FILE = ".users.json"
USERS_DIR = "users"
NAME_RE = re.compile(r"^[a-z0-9_-]{2,24}$")

#: Schluessel, die jedem Spieler selbst gehoeren (alle anderen gelten fuer alle)
USER_KEYS = ("steam_id", "player_name", "steam_username", "last_sharecode", "cs2_auth_token",
             "discord_webhook", "briefing_webhook", "telegram_bot_token",
             "ai_provider", "ai_model", "gemini_api_key", "ollama_url", "language")
#: Darf ein Mate auf seiner Einstellungsseite aendern (Steam-ID setzt der Admin)
MATE_EDITABLE = tuple(k for k in USER_KEYS if k not in ("steam_id",))

_lock = threading.Lock()

#: Fuer wen laeuft der Code gerade? (Web: je Anfrage gesetzt, CLI: je Spieler im Sync.)
#: Bestimmt z. B. die Steam-Sitzung (sharecode._session_file) - jeder Mate laedt mit seinem Konto.
ACTIVE: ContextVar[str | None] = ContextVar("cs2coach_user", default=None)


@contextmanager
def as_user(name: str | None):
    token = ACTIVE.set(name)
    try:
        yield
    finally:
        ACTIVE.reset(token)


def shared_dir(config_path) -> Path:
    """Ordner fuer Gemeinsames (Demo-Caches, Maps, Server): neben der Admin-Config.

    Fuer einen Mate ist ``config_path`` ``<basis>/users/<name>/config.yaml``.
    """
    p = Path(config_path).parent
    return p.parent.parent if p.parent.name == USERS_DIR else p


def user_dir(base_config: Path, name: str) -> Path:
    return Path(base_config).parent / USERS_DIR / name


def user_config_path(base_config: Path, name: str) -> Path:
    return user_dir(base_config, name) / "config.yaml"


# ── Benutzerliste ────────────────────────────────────────────────────────────
def load_users(base_config: Path) -> dict[str, dict]:
    """``{name: {"pw": hash, "steam_id": ..., "display": ...}}`` (leer ohne Datei)."""
    try:
        return json.loads((Path(base_config).parent / USERS_FILE).read_text(encoding="utf-8")).get("mates", {})
    except (OSError, ValueError):
        return {}


def _save_users(base_config: Path, mates: dict) -> None:
    path = Path(base_config).parent / USERS_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"mates": mates, "pending": load_pending(base_config)}, indent=1), encoding="utf-8")
    tmp.replace(path)


def add_user(base_config: Path, name: str, password: str, steam_id: str, display: str = "") -> None:
    """Mate anlegen (oder Passwort/Steam-ID aendern). Wirft ValueError bei ungueltigen Angaben."""
    from .web.auth import hash_password
    name = name.strip().lower()
    if not NAME_RE.match(name) or name == "admin":
        raise ValueError("Name: 2-24 Zeichen, a-z, 0-9, _ oder -, nicht 'admin'")
    steam_id = steam_id.strip()
    if not (steam_id.isdigit() and len(steam_id) == 17):
        raise ValueError("Steam-ID: 17 Ziffern (steamID64)")
    with _lock:
        mates = load_users(base_config)
        entry = mates.get(name, {})
        if password:
            if len(password) < 8:
                raise ValueError("Passwort: mindestens 8 Zeichen")
            entry["pw"] = hash_password(password)
        elif "pw" not in entry:
            raise ValueError("Passwort fehlt")
        entry["steam_id"] = steam_id
        entry["display"] = display.strip() or entry.get("display") or name
        mates[name] = entry
        _save_users(base_config, mates)
    ucfg = user_config_path(base_config, name)
    ucfg.parent.mkdir(parents=True, exist_ok=True)
    over = load_overlay(base_config, name)
    over["steam_id"] = steam_id
    over.setdefault("player_name", entry["display"])
    save_overlay(base_config, name, over)


def remove_user(base_config: Path, name: str) -> None:
    """Login entfernen. Seine Daten (users/<name>/) bleiben liegen - loeschen ist Handarbeit."""
    with _lock:
        mates = load_users(base_config)
        mates.pop(name, None)
        _save_users(base_config, mates)


def verify(base_config: Path, name: str, password: str) -> bool:
    from werkzeug.security import check_password_hash
    entry = load_users(base_config).get(name.strip().lower())
    return bool(entry and entry.get("pw") and check_password_hash(entry["pw"], password))


# ── Konfiguration je Spieler ─────────────────────────────────────────────────
def load_overlay(base_config: Path, name: str) -> dict:
    import yaml
    try:
        return yaml.safe_load(user_config_path(base_config, name).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def save_overlay(base_config: Path, name: str, data: dict) -> None:
    """Nur die eigenen Schluessel speichern - nie die der Admin-Config."""
    import yaml
    path = user_config_path(base_config, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: v for k, v in data.items() if k in USER_KEYS}
    path.write_text("# CS2-Coach - Einstellungen dieses Spielers (Rest aus der Admin-Config)\n"
                    + yaml.dump(clean, default_flow_style=False, allow_unicode=True), encoding="utf-8")


def merged_config(base_cfg: dict, base_config: Path, name: str) -> dict:
    """Admin-Config ohne dessen persoenliche Schluessel, plus die des Mates und sein Ablageort."""
    cfg = {k: v for k, v in base_cfg.items() if k not in USER_KEYS and k != "auth"}
    cfg.update(load_overlay(base_config, name))
    vault = user_dir(base_config, name) / "vault"
    cfg["obsidian_vault_path"] = str(vault)
    cfg["coach_subfolder"] = "CS2-Coach"
    cfg["user"] = name
    return cfg


def all_players(base_cfg: dict, base_config: Path) -> list[tuple[str | None, dict, Path]]:
    """(Name oder None fuer den Admin, Config, Config-Pfad) aller Spieler - fuer Hintergrundlaeufe."""
    out = [(None, base_cfg, Path(base_config))]
    for name in sorted(load_users(base_config)):
        out.append((name, merged_config(base_cfg, base_config, name), user_config_path(base_config, name)))
    return out


# ── Gemeinsame Matches: wer hat zu welcher Demo einen Export? ────────────────
_index_cache: dict[str, tuple[float, dict[str, str]]] = {}


def export_dir(cfg: dict) -> Path | None:
    vault = (cfg.get("obsidian_vault_path") or "").strip()
    return Path(vault) / cfg.get("coach_subfolder", "CS2-Coach") / "exports" if vault else None


def demo_index(cfg: dict) -> dict[str, str]:
    """Demo-Datei -> Export-Datei dieses Spielers (gemerkt, bis sich der Ordner aendert)."""
    d = export_dir(cfg)
    if d is None or not d.exists():
        return {}
    key, mtime = str(d), d.stat().st_mtime
    hit = _index_cache.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    out = {}
    for f in d.glob("*_coach.json"):
        try:
            with open(f, encoding="utf-8") as fh:
                head = fh.read(4096)        # "match" steht vorn im Export - nicht die ganze Datei lesen
            m = re.search(r'"demo_file"\s*:\s*"([^"]+)"', head)
            if not m:
                m = re.search(r'"demo_file"\s*:\s*"([^"]+)"', f.read_text(encoding="utf-8"))
            if m:
                out[Path(m.group(1)).name] = f.name
        except OSError:
            continue
    _index_cache[key] = (mtime, out)
    return out


def views_of(base_cfg: dict, base_config: Path, demo_file: str) -> list[dict]:
    """Alle Spieler mit eigenem Export zu dieser Demo: [{"user": None|Name, "display", "file"}]."""
    out = []
    for name, cfg, _ in all_players(base_cfg, base_config):
        f = demo_index(cfg).get(Path(demo_file).name)
        if f:
            disp = (load_users(base_config).get(name, {}).get("display") if name else None) \
                or cfg.get("player_name") or name or "Admin"
            out.append({"user": name, "display": disp, "file": f})
    return out


# -- Registrierung: Anfragen warten, bis der Admin sie annimmt -----------------
MAX_PENDING = 20


def load_pending(base_config: Path) -> dict[str, dict]:
    try:
        return json.loads((Path(base_config).parent / USERS_FILE).read_text(encoding="utf-8")).get("pending", {})
    except (OSError, ValueError):
        return {}


def _save_all(base_config: Path, mates: dict, pending: dict) -> None:
    path = Path(base_config).parent / USERS_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"mates": mates, "pending": pending}, indent=1), encoding="utf-8")
    tmp.replace(path)


def request_access(base_config: Path, name: str, password: str, steam_id: str, display: str = "") -> str:
    """Registrierung ablegen (Passwort nur als Hash). Rueckgabe: normierter Name. ValueError bei Fehlern."""
    from datetime import datetime

    from .web.auth import hash_password
    name, steam_id = name.strip().lower(), steam_id.strip()
    if not NAME_RE.match(name) or name == "admin":
        raise ValueError("Name: 2-24 Zeichen, a-z, 0-9, _ oder -")
    if not (steam_id.isdigit() and len(steam_id) == 17):
        raise ValueError("Steam-ID: 17 Ziffern (steamID64)")
    if len(password) < 8:
        raise ValueError("Passwort: mindestens 8 Zeichen")
    with _lock:
        mates, pending = load_users(base_config), load_pending(base_config)
        if name in mates or name in pending:
            raise ValueError("Name ist schon vergeben")
        if len(pending) >= MAX_PENDING:
            raise ValueError("Zu viele offene Anfragen - bitte spaeter nochmal")
        pending[name] = {"pw": hash_password(password), "steam_id": steam_id, "display": display.strip()[:40] or name,
                         "at": datetime.now().isoformat(timespec="minutes")}
        _save_all(base_config, mates, pending)
    return name


def decide(base_config: Path, name: str, accept: bool) -> None:
    """Anfrage annehmen (wird Mate) oder ablehnen (wird geloescht)."""
    with _lock:
        mates, pending = load_users(base_config), load_pending(base_config)
        entry = pending.pop(name, None)
        if entry and accept:
            mates[name] = {k: entry[k] for k in ("pw", "steam_id", "display")}
        _save_all(base_config, mates, pending)
    if entry and accept:
        over = load_overlay(base_config, name)
        over.update(steam_id=entry["steam_id"], player_name=entry["display"])
        save_overlay(base_config, name, over)
