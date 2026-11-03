"""WebUI-Seite /lineups: Granaten-Lineups auf der Radarkarte.

Eigene (im Spiel mit !speichern aufgenommen) und die haeufigsten aus den Demos
(nur bereits gescannte - die Seite startet keinen Demo-Scan, der dauert Minuten).
Linie = Wurfplatz -> Landung, Farbe = Granatentyp. Umrechnung serverseitig
mit maps.game_to_radar(), nie im JavaScript.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, redirect, render_template, request, url_for

from .. import own_lineups
from ..grenade_lineups import LABELS, collect, library, nade_keys, style_text
from ..maps import game_to_radar
from ..training_server import DEFAULT_MAPS


def _points(g: dict, short: str, own: bool) -> dict | None:
    a = game_to_radar(g["pos"][0], g["pos"][1], short)
    b = game_to_radar(g["land"][0], g["land"][1], short)
    if not a or not b:
        return None
    style = g.get("style") or {}
    return {"x1": round(a[0]), "y1": round(a[1]), "x2": round(b[0]), "y2": round(b[1]), "type": g["type"],
            "own": own, "title": (g.get("name") or f"{g.get('count', 1)}x geworfen"),
            "label": f"{g.get('side', '')} {LABELS[g['type']]}",
            "style": f"{style_text(style)} - {nade_keys(style)}" if style else ""}


def create_blueprint(load_config, config_path) -> Blueprint:
    bp = Blueprint("lineups_page", __name__)

    @bp.route("/lineups")
    def page():
        cfg = load_config()
        path = Path(config_path())
        maps = list(cfg.get("training_maps") or DEFAULT_MAPS)
        bsp = request.args.get("map") or maps[0]
        short = bsp.removeprefix("de_")
        own = own_lineups.load(path).get(bsp, [])
        demo = library(collect(cfg, path, bsp, log=lambda *a: None, max_new=0), limit=30)
        lines = [p for g in own if (p := _points(g, short, True))] + \
                [p for g in demo if (p := _points(g, short, False))]
        return render_template("lineups.html", maps=maps, bsp=bsp, short=short, lines=lines,
                               own=own, labels=LABELS, demo_count=len(demo))

    @bp.route("/lineups/delete", methods=["POST"])
    def delete():
        path = Path(config_path())
        bsp, name = request.form.get("map", ""), request.form.get("name", "")
        data = own_lineups.load(path)
        data[bsp] = [g for g in data.get(bsp, []) if g["name"] != name]
        own_lineups._save(path, data)
        return redirect(url_for("lineups_page.page", map=bsp))

    return bp
