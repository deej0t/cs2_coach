"""WebUI-Seite /aim-profil: dein Aim in Duellen gegen die Lobby (cs2_coach/aim_profile.py).

Zeigt das zuletzt gerechnete Profil (.aim_profile.json) und stoesst im
Hintergrund das Einlesen neuer Demos an (je Demo ~3 s lokal, ~10 s auf dem
Server; einmal je Demo, danach aus aim_cache/).
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

from .. import aim_profile, map3d_render


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

    @bp.route("/aim-profil/serie/<key>")
    def series(key):
        """Alle deine Faelle zu einem Wert nacheinander im Player (ueber mehrere Matches)."""
        cfg = load_config()
        path = Path(config_path())
        data = aim_profile.load(path) or {}
        group = request.args.get("waffen", "rifle")
        g = (data.get("groups") or {}).get(group) or {}
        row = next((r for r in g.get("rows", []) if r["key"] == key), None)
        exports = data.get("exports") or {}
        items = []
        for d in (g.get("examples") or {}).get(key, []):
            f = exports.get(d["demo"])
            if not f:
                continue
            map_key = f[11:].split("_")[0]
            value = d.get(key)
            label = f"{f[:10]} {map_key} · Runde {d['round']}" + (
                f" · {value:.1f}{row['unit']}" if row and row["unit"] != "%" and isinstance(value, (int, float)) else "")
            items.append({"file": f, "round": d["round"], "tick": d["tick"], "kind": "kill" if d["won"] else "death",
                          "label": label, "map": map_key,
                          "parts": map3d_render.render_parts(cfg, path, map_key)})
        return render_template("aim_series.html", row=row, items=items, group=group,
                               group_label=aim_profile.GROUPS.get(group, group), config=cfg)

    @bp.route("/api/aim-profil/status")
    def status():
        data = aim_profile.load(Path(config_path())) or {}
        return jsonify({"running": aim_profile.running(), "at": data.get("at")})

    return bp
