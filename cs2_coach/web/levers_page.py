"""WebUI-Seite /hebel: Ergebnis der Hebel-Suche (cs2_coach/levers.py).

Zeigt das zuletzt beim Sync gemessene Ergebnis (.levers.json); fehlt es,
wird einmal gerechnet (unter 1 s ueber 143 Matches). Nur angemeldet.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, render_template

from .. import levers

LEVEL_ORDER = {"Runde": 0, "Match": 1, "Tod": 2, "Zone": 3, "Lage": 4, "Lobby-Zone": 5}


def create_blueprint(load_config, config_path) -> Blueprint:
    bp = Blueprint("levers_page", __name__)

    @bp.route("/hebel")
    def page():
        cfg = load_config()
        path = Path(config_path())
        data = levers.load(path) or levers.run(cfg, path)
        rows = data.get("levers", [])
        found = [x for x in rows if x["verdict"] in levers.SOLID and not x["outcome_driven"]]
        # Echte Kandidaten oben, ergebnisabhaengige unten - sonst liest sich "Survival belastbar"
        # ganz oben wie ein Befund
        rows = sorted(rows, key=lambda x: (x["outcome_driven"], x["verdict"] not in levers.SOLID,
                                           LEVEL_ORDER.get(x["level"], 9), -abs(x["effect"])))
        for x in rows:
            x["label"] = f"[{x['level']}] {x['label']}"
        return render_template("levers.html", data=data, rows=rows, found=found, config=cfg)

    return bp
