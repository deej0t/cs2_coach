"""WebUI-Seite /training: Auswertung des Duell-Trainings auf dem eigenen Server.

Das Training selbst laeuft im Spiel (!training, Menue mit !menu); hier steht,
was dabei herauskam: jedes Training mit seinen Duellen und die Situationen,
die du ueber alle Trainings am oeftesten verloren hast - mit Link auf das
Match, aus dem sie stammen.

Nur angemeldet (nicht in auth.PUBLIC_ENDPOINTS). Eigenes Modul statt
weiterer Routen in app.py (ueber 11.000 Zeilen).
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, render_template

from .. import training_server


def create_blueprint(load_config, config_path) -> Blueprint:
    """config_path ist eine Funktion, damit Tests die Config austauschen koennen."""
    bp = Blueprint("training_page", __name__)

    @bp.route("/training")
    def page():
        path = Path(config_path())
        sessions = training_server.load_sessions(path)
        total = sum(s["played"] for s in sessions)
        won = sum(s["won"] for s in sessions)
        cfg = load_config()
        return render_template(
            "training.html", sessions=sessions[:20], hardest=training_server.hardest(sessions),
            running=training_server.state_summary(path), total=total, won=won,
            weapon_label=training_server.weapon_label, aim_side=training_server.aim_side,
            measured=[s for s in sessions[:20] if s["measures"]["seen_n"] or s["measures"]["kill_n"]],
            match_cp=training_server.match_crosshair_median(cfg), max_level=training_server.MAX_LEVEL,
            config=cfg)

    return bp
