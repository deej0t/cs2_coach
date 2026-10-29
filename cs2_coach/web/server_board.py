"""Server-Bestenliste: oeffentliche Seite /server und Log-Empfang.

Beide Routen sind ohne Login erreichbar (auth.PUBLIC_ENDPOINTS): die
Bestenliste ist fuer alle, die auf dem Server spielen, und der CS2-Server
kann sich nicht anmelden. Den Empfang schuetzt stattdessen ein geheimer
Token in der URL. Die Einrichtungs-Zeilen fuer die server.cfg (mit diesem
Token) sieht nur, wer angemeldet ist.

Eigenes Modul statt weiterer Routen in app.py (ueber 11.000 Zeilen).
"""

from __future__ import annotations

import secrets
from pathlib import Path

from flask import Blueprint, abort, redirect, render_template, request, session, url_for

from .. import server_live, server_stats
from . import auth


def create_blueprint(config_path, load_config=None) -> Blueprint:
    """config_path ist eine Funktion, damit Tests die Config austauschen koennen.

    load_config liefert die aktuelle Config (Webhook, RCON) fuer Chat-Befehle
    und Discord-Meldungen; ohne sie wird nur gespeichert.
    """
    bp = Blueprint("server", __name__)

    @bp.route("/server")
    def page():
        path = Path(config_path())
        board = server_stats.build_board(server_stats.read_logs(path),
                                         request.args.get("zeitraum", "alle"))
        is_admin = not auth.is_enabled(path) or bool(session.get("authenticated"))
        setup = None
        if is_admin:
            url = f"{request.url_root}api/serverlog/{server_stats.load_or_create_token(path)}"
            setup = {"url": url}
        cfg = load_config() if load_config else {}
        address = (cfg.get("server_address") or "").strip()
        connect = None
        if address:
            connect = {"address": address,
                       "url": f"steam://connect/{address if ':' in address else address + ':27015'}"}
        return render_template("server_board.html", board=board, setup=setup, is_admin=is_admin,
                               connect=connect)

    @bp.route("/server/reset", methods=["POST"])
    def reset():
        """Nicht in PUBLIC_ENDPOINTS - zusaetzlich hier geprueft, weil die Seite oeffentlich ist."""
        path = Path(config_path())
        if auth.is_enabled(path) and not session.get("authenticated"):
            abort(403)
        n = server_stats.archive_logs(path)
        return redirect(url_for("server.page", reset=n))

    @bp.route("/api/serverlog/<token>", methods=["POST"])
    def ingest(token):
        path = Path(config_path())
        expected = server_stats.load_or_create_token(path)
        if not expected or not secrets.compare_digest(token, expected):
            abort(403)
        if (request.content_length or 0) > server_stats.MAX_BATCH_BYTES:
            abort(413)
        body = request.get_data()
        server_stats.store_batch(path, request.headers, body)
        if load_config is not None:
            try:
                server_live.on_batch(load_config(), path, body)
            except Exception as e:  # noqa: BLE001 - der Empfang darf daran nie scheitern
                print(f"[server_live] {e}", flush=True)
        return "", 200

    return bp
