"""/live: Live-Coach fuers Handy oder den zweiten Bildschirm, /api/gsi/<token>: GSI-Empfang.

Eigenes Modul statt weiterer Routen in app.py (ueber 11.000 Zeilen).
Der Empfang ist oeffentlich (der Spiel-Client kann sich nicht anmelden) und
nur durch den Token in URL und Nutzlast geschuetzt - wie /api/serverlog.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Response, abort, jsonify, render_template, request

from .. import live_coach


def create_blueprint(load_config, config_path) -> Blueprint:
    bp = Blueprint("live", __name__)

    def _url(cfg: dict) -> str:
        """GSI ins Heimnetz schicken, nicht ueber Cloudflare (dessen Bot-Schutz blockte schon GitHub)."""
        base = (cfg.get("live_gsi_url") or "").strip().rstrip("/")
        if not base and cfg.get("server_rcon_host"):
            base = f"http://{cfg['server_rcon_host']}:5001"
        base = base or request.url_root.rstrip("/")
        return f"{base}/api/gsi/{live_coach.load_or_create_token(Path(config_path()))}"

    @bp.route("/api/gsi/<token>", methods=["POST"])
    def ingest(token):
        path = Path(config_path())
        expected = live_coach.load_or_create_token(path)
        payload = request.get_json(silent=True, force=True) or {}
        if token != expected or (payload.get("auth") or {}).get("token") != expected:
            abort(403)
        live_coach.ingest(load_config(), path, payload)
        return "", 200

    @bp.route("/api/live")
    def state():
        st = live_coach.load_state(Path(config_path()))
        thrown, rounds, wasted = live_coach.half_stats(st) if st else (0, 0, 0)
        early, full_buys = live_coach.early_stats(st) if st else (0, 0)
        return jsonify({"map": st.get("map"), "round": st.get("round"), "half": st.get("half"),
                        "target": st.get("target"), "thrown": thrown, "rounds": rounds, "wasted": wasted,
                        "early": early, "full_buys": full_buys,
                        "hint": st.get("hint"), "log": st.get("log", [])[-6:], "over": st.get("over", False)})

    @bp.route("/live")
    def page():
        cfg = load_config()
        from .. import training_effect, training_server
        summary = training_effect.live_summary(cfg, training_server.load_sessions(Path(config_path())))
        return render_template("live.html", gsi_url=_url(cfg), config=cfg, summary=summary)

    @bp.route("/live/gamestate_integration_cs2coach.cfg")
    def cfg_file():
        cfg = load_config()
        token = live_coach.load_or_create_token(Path(config_path()))
        return Response(live_coach.gsi_config(_url(cfg), token), mimetype="text/plain",
                        headers={"Content-Disposition": "attachment; filename=gamestate_integration_cs2coach.cfg"})

    return bp
