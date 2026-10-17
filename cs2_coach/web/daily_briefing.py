"""WebUI-Seite "Tages-Briefing": Vorschau und Versand per Knopfdruck.

Vorher liess sich das Briefing nur per Konsole im Container ausloesen
("cs2-coach briefing --send --force"). Eigenes Modul statt weiterer
Routen in app.py (ueber 11.000 Zeilen).

Der Versand laeuft in einem Hintergrund-Thread: die KI braucht mit
Wiederholungen bis zu zweieinhalb Minuten, Gunicorn bricht Anfragen nach
120 Sekunden ab. Eine Sperrdatei neben der Config verhindert doppelten
Versand bei Doppelklick - auch ueber mehrere Gunicorn-Worker hinweg.
Ergebnis und Protokoll landen in .briefing_manual.json; die Seite laedt
sich neu, solange der Versand laeuft.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, url_for
from markupsafe import Markup

#: Nach dieser Zeit gilt eine Sperre als verwaist (Worker abgestuerzt).
STALE_LOCK = 600


def _paths(config_path: Path) -> tuple[Path, Path]:
    base = Path(config_path).parent
    return base / ".briefing_running", base / ".briefing_manual.json"


def _acquire(lock: Path) -> bool:
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > STALE_LOCK:
            lock.unlink()
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        return False


def send_now(cfg: dict, config_path: Path, runner=None) -> bool:
    """Versand im Hintergrund starten. False, wenn schon einer laeuft."""
    from ..briefing import run

    lock, result = _paths(config_path)
    if not _acquire(lock):
        return False
    runner = runner or run

    def work():
        log: list[str] = []
        started = datetime.now().strftime("%d.%m. %H:%M")
        try:
            text = runner(cfg, config_path, send=True, force=True, log=log.append)
            ok = bool(text)
        except Exception as e:  # noqa: BLE001 - Fehler gehoeren auf die Seite, nicht ins Nichts
            ok = False
            log.append(f"Fehler: {e}")
        finally:
            lock.unlink(missing_ok=True)
        result.write_text(json.dumps({"started": started, "ok": ok, "log": log[-8:],
                                      "finished": datetime.now().strftime("%d.%m. %H:%M")},
                                     ensure_ascii=False), encoding="utf-8")

    threading.Thread(target=work, daemon=True).start()
    return True


def _channels(cfg: dict) -> list[str]:
    out = []
    if cfg.get("briefing_webhook") or cfg.get("discord_webhook"):
        out.append("Discord")
    if (cfg.get("telegram_bot_token") or "").strip():
        out.append("Telegram")
    return out


def create_blueprint(load_config, config_path) -> Blueprint:
    """config_path ist eine Funktion, damit Tests die Config austauschen koennen."""
    bp = Blueprint("daily", __name__)

    @bp.route("/daily")
    def page():
        from ..briefing import run
        from ..telegram import to_html

        cfg = load_config()
        path = Path(config_path())
        lock, result = _paths(path)
        running = lock.exists() and time.time() - lock.stat().st_mtime < STALE_LOCK
        try:
            last = json.loads(result.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            last = None
        try:
            state = json.loads((path.parent / ".briefing_state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}

        preview = run(cfg, path, send=False, use_ai=False, log=lambda m: None) or ""
        html = Markup(to_html(preview).replace("\n", "<br>"))
        return render_template("daily_briefing.html", preview=html, running=running, last=last,
                               last_sent=state.get("last_sent", ""), channels=_channels(cfg),
                               hour=os.environ.get("CS2COACH_BRIEFING_HOUR", "17"), config=cfg)

    @bp.route("/daily/send", methods=["POST"])
    def send():
        cfg = load_config()
        if not _channels(cfg):
            flash("Weder Discord noch Telegram eingerichtet - bitte zuerst in den Einstellungen eintragen.",
                  "error")
        elif send_now(cfg, Path(config_path())):
            flash("Briefing wird erstellt und gleich verschickt (bis zu 2-3 Minuten).", "success")
        else:
            flash("Es wird gerade schon ein Briefing verschickt.", "success")
        return redirect(url_for("daily.page"))

    return bp
