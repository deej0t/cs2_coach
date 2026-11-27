"""WebUI-Seite /aim-profil: dein Aim in Duellen gegen die Lobby (cs2_coach/aim_profile.py).

Zeigt das zuletzt gerechnete Profil (.aim_profile.json) und stoesst im
Hintergrund das Einlesen neuer Demos an (je Demo ~3 s lokal, ~10 s auf dem
Server; einmal je Demo, danach aus aim_cache/).
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

from .. import aim_profile


def create_blueprint(load_config, config_path) -> Blueprint:
    bp = Blueprint("aim_profile_page", __name__)

    @bp.route("/aim-profil")
    def page():
        cfg = load_config()
        path = Path(config_path())
        data = aim_profile.load(path)
        aim_profile.start(cfg, path)        # neue Demos nachlesen, Ergebnis beim naechsten Aufruf
        group = request.args.get("waffen", "rifle")
        if group not in aim_profile.GROUPS:
            group = "rifle"
        return render_template("aim_profile.html", data=data, group=group, groups=aim_profile.GROUPS,
                               running=aim_profile.running(), config=cfg)

    @bp.route("/api/aim-profil/status")
    def status():
        data = aim_profile.load(Path(config_path())) or {}
        return jsonify({"running": aim_profile.running(), "at": data.get("at")})

    return bp
