"""Systemstatus: prueft, ob CS2 Coach tatsaechlich funktioniert.

Anlass waren drei Fehler, die jeweils lange unbemerkt blieben, weil sie
nur in Logs standen, die niemand liest:

- Das Auto-Update lief auf dem Server sechs Wochen lang nie (das Image
  startete mit einer alten Kopie des Entrypoints ohne Update-Schleife).
  Sichtbar war nur, dass die letzte Update-Pruefung 293 Stunden alt war.
- Die Steam-Anmeldung wurde taeglich geloescht.
- Die WebUI war nach einem Neuerstellen des Containers einen Tag lang
  nicht erreichbar, waehrend Docker "healthy" meldete - der Healthcheck
  fragt innerhalb des Containers und sieht eine falsche Portzuordnung
  nicht.

Dazu kam zweimal ein volllaufender Cache, den der Container ueber /data
sehen kann.

Jede Pruefung liefert ok, warn, fail oder skip (nicht anwendbar, etwa
ausserhalb des Containers). notify() schickt eine Discord-Nachricht nur
bei einer Zustandsaenderung, nicht bei jedem Lauf.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"

#: Speicherbelegung, ab der gewarnt bzw. Alarm gegeben wird (Prozent).
DISK_WARN, DISK_FAIL = 85, 95

#: Restlaufzeit der Steam-Anmeldung, ab der gewarnt wird (Tage).
STEAM_WARN_DAYS = 14

_REPO = Path(__file__).resolve().parent.parent


@dataclass
class Check:
    key: str
    label: str
    state: str
    message: str


# ── Einzelne Pruefungen ──────────────────────────────────────────────

def check_public_url(cfg: dict, get=None) -> Check:
    """Antwortet die WebUI ueber die Adresse, die der Nutzer aufruft?

    Absichtlich von aussen: der Docker-Healthcheck fragt localhost
    innerhalb des Containers und meldete "healthy", waehrend die WebUI
    einen Tag lang nicht erreichbar war.
    """
    url = (cfg.get("public_url") or "").strip()
    label = "WebUI erreichbar"
    if not url:
        return Check("public_url", label, SKIP,
                     "Keine oeffentliche Adresse eingetragen (Einstellungen).")
    if get is None:
        import requests
        get = requests.get
    try:
        r = get(url, timeout=15, allow_redirects=False)
    except Exception as e:  # noqa: BLE001 - jede Art von Fehler ist "nicht erreichbar"
        return Check("public_url", label, FAIL, f"{url} nicht erreichbar: {e}")
    if r.status_code < 400:
        return Check("public_url", label, OK, f"{url} antwortet (HTTP {r.status_code})")
    return Check("public_url", label, FAIL, f"{url} antwortet mit HTTP {r.status_code}")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, timeout=15).stdout.strip()


def check_updates(repo: Path = _REPO, now: float | None = None,
                  in_container: bool | None = None) -> Check:
    """Laeuft das Auto-Update, und ist der Code auf dem Stand von GitHub?"""
    label = "Auto-Update"
    if in_container is None:
        in_container = Path("/.dockerenv").exists()
    if not in_container:
        return Check("updates", label, SKIP, "Nur im Container relevant.")
    interval = int(os.environ.get("CS2COACH_UPDATE_INTERVAL", "21600") or 0)
    if interval <= 0:
        return Check("updates", label, SKIP, "Auto-Update ist deaktiviert.")
    fetch_head = repo / ".git" / "FETCH_HEAD"
    if not fetch_head.exists():
        return Check("updates", label, WARN, "Noch keine Update-Pruefung gelaufen.")

    now = time.time() if now is None else now
    age_h = (now - fetch_head.stat().st_mtime) / 3600
    limit_h = 2 * interval / 3600
    if age_h > limit_h:
        return Check("updates", label, FAIL,
                     f"Letzte Update-Pruefung vor {age_h:.0f} h, erwartet alle "
                     f"{interval / 3600:.0f} h. Die Update-Schleife laeuft nicht.")

    branch = os.environ.get("CS2COACH_BRANCH", "main")
    head = _git(repo, "rev-parse", "--short", "HEAD")
    remote = _git(repo, "rev-parse", "--short", f"origin/{branch}")
    if head and remote and head != remote:
        return Check("updates", label, FAIL,
                     f"Update haengt: es laeuft {head}, verfuegbar ist {remote}.")
    return Check("updates", label, OK,
                 f"Aktuell ({head}), zuletzt geprueft vor {age_h:.1f} h.")


def check_steam(status: dict | None = None) -> Check:
    """Ist die Steam-Anmeldung fuer den Demo-Download gueltig?"""
    label = "Steam-Anmeldung"
    if status is None:
        from .sharecode import steam_session_status
        status = steam_session_status()
    if not status.get("refresh_expires"):
        return Check("steam", label, WARN,
                     "Nicht bei Steam angemeldet - Demos werden nicht automatisch geladen.")
    if not status.get("logged_in"):
        return Check("steam", label, FAIL, "Anmeldung abgelaufen, bitte neu anmelden.")
    days = status.get("days_left", 0)
    if days < STEAM_WARN_DAYS:
        return Check("steam", label, WARN, f"Anmeldung laeuft in {days:.0f} Tagen ab.")
    return Check("steam", label, OK, f"Gueltig noch {days:.0f} Tage.")


def check_disk(paths: dict[str, Path], usage=shutil.disk_usage) -> list[Check]:
    """Belegung der Datentraeger. Gleiche Datentraeger werden nur einmal geprueft.

    Im Container zeigt /data auf appdata und damit auf den Cache - dessen
    Belegung war zweimal das eigentliche Problem.
    """
    out, seen = [], set()
    for label, path in paths.items():
        if not path or not Path(path).exists():
            continue
        try:
            dev = os.stat(path).st_dev
        except OSError:
            continue
        u = usage(path)
        # Die Geraetenummer allein reicht nicht: auf Unraid laufen Cache und
        # Array beide ueber shfs und haben dieselbe (auf dem Server: 47 fuer
        # /data und /data/demos). Erst die Groesse trennt sie.
        key = (dev, u.total)
        if key in seen:
            continue
        seen.add(key)
        pct = u.used / u.total * 100 if u.total else 0
        free_gb = u.free / 1e9
        state = FAIL if pct >= DISK_FAIL else WARN if pct >= DISK_WARN else OK
        out.append(Check(f"disk:{label}", f"Speicher {label}", state,
                         f"{pct:.0f} % belegt, {free_gb:.0f} GB frei"))
    return out


def check_demos(cfg: dict) -> Check:
    """Ist zu jedem Export die Demo auffindbar?

    Ohne Demo funktionieren 2D-Replay und Neuanalyse nicht - und zwar
    lautlos. Nach einem falsch eingebundenen Demo-Ordner waeren alle
    Exporte betroffen, ohne dass irgendwo ein Fehler erscheint.
    """
    from .export_version import demo_status

    label = "Demos"
    raw = (cfg.get("demo_folder") or "").strip()
    folder = Path(raw)
    # Rohwert pruefen: Path("") ist ".", also das Arbeitsverzeichnis -
    # eine fehlende Einstellung galt so als vorhandener Ordner.
    if not raw or not folder.is_dir():
        return Check("demos", label, FAIL, f"Demo-Ordner nicht gefunden: {folder}")
    export_dir = _export_dir(cfg)
    known = demo_status(export_dir) if export_dir else {}
    if not known:
        return Check("demos", label, OK, "Noch keine Exporte.")
    missing = [d for d in known if not (folder / d).exists()]
    if len(missing) == len(known):
        return Check("demos", label, FAIL,
                     f"Keine der {len(known)} Demos im Ordner gefunden - "
                     f"Ordner leer oder falsch eingebunden?")
    if missing:
        return Check("demos", label, WARN,
                     f"{len(missing)} von {len(known)} Exporten ohne Demo; "
                     f"dort gehen 2D-Replay und Neuanalyse nicht.")
    return Check("demos", label, OK, f"Alle {len(known)} Demos auffindbar.")


def check_exports(cfg: dict) -> Check:
    """Gibt es Exporte, deren Zahlen nach einer Metrikkorrektur falsch sind?"""
    from .export_version import export_status

    label = "Exporte"
    export_dir = _export_dir(cfg)
    files = list(export_dir.glob("*_coach.json")) if export_dir else []
    if not files:
        return Check("exports", label, OK, "Noch keine Exporte.")
    stale = incomplete = 0
    for f in files:
        try:
            st = export_status(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        stale += st["needs_reanalysis"]
        incomplete += bool(st["missing"]) and not st["needs_reanalysis"]
    if stale:
        return Check("exports", label, WARN,
                     f"{stale} von {len(files)} Exporten mit veralteter Rechnung - "
                     f"Neuanalyse noetig (cs2-coach batch --only-outdated).")
    note = f", {incomplete} ohne neuere Auswertungen" if incomplete else ""
    return Check("exports", label, OK, f"{len(files)} Exporte, Zahlen aktuell{note}.")


def _export_dir(cfg: dict) -> Path | None:
    vault = cfg.get("obsidian_vault_path") or ""
    if not vault:
        return None
    return Path(vault) / cfg.get("coach_subfolder", "CS2-Coach") / "exports"


def run_checks(cfg: dict, config_path: Path) -> list[Check]:
    checks = [check_public_url(cfg), check_updates(), check_steam()]
    checks += check_disk({
        "Config/Vault": Path(config_path).parent,
        "Demos": Path(cfg["demo_folder"]) if cfg.get("demo_folder") else None,
    })
    checks += [check_demos(cfg), check_exports(cfg)]
    return checks


# ── Benachrichtigung ─────────────────────────────────────────────────

_ICON = {OK: "✅", WARN: "⚠️", FAIL: "❌", SKIP: "➖"}


def notify(checks: list[Check], webhook: str, state_path: Path, post=None) -> str | None:
    """Discord-Nachricht bei Zustandsaenderung. Gibt den Text zurueck, wenn gesendet.

    Gemeldet werden neue oder veraenderte Probleme (warn/fail) und behobene
    Probleme. Wechsel zwischen ok und skip sind keine Nachricht wert. Der
    Zustand wird erst nach erfolgreichem Versand gespeichert - schlaegt der
    Versand fehl oder fehlt der Webhook, wird beim naechsten Lauf erneut
    gemeldet.
    """
    try:
        prev = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}
    cur = {c.key: c.state for c in checks}
    bad = (WARN, FAIL)

    changed = [c for c in checks if c.state in bad and prev.get(c.key) != c.state]
    fixed = [c for c in checks if prev.get(c.key) in bad and c.state not in bad]
    if not changed and not fixed:
        return None
    if not webhook:
        return None

    lines = ["**CS2 Coach – Systemstatus**"]
    for c in checks:
        if c.state in bad:
            new = " (neu)" if c in changed else ""
            lines.append(f"{_ICON[c.state]} {c.label}{new}: {c.message}")
    for c in fixed:
        lines.append(f"{_ICON[OK]} {c.label} wieder in Ordnung: {c.message}")
    text = "\n".join(lines)[:1900]

    if post is None:
        import requests
        post = requests.post
    try:
        r = post(webhook, json={"content": text}, timeout=15)
        if getattr(r, "status_code", 204) >= 400:
            return None
    except Exception:  # noqa: BLE001 - beim naechsten Lauf erneut versuchen
        return None

    Path(state_path).write_text(json.dumps(cur), encoding="utf-8")
    return text


def as_dicts(checks: list[Check]) -> list[dict]:
    return [asdict(c) for c in checks]
