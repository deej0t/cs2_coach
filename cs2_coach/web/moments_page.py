"""WebUI-Seite /momente: Schluesselmomente eines Matches als kommentierter Clip.

Liste = teuerste eigene Tode (costly_moments), Clip = 2D-Replay der Runde
(build_round_replay) mit Markierungen aus key_moments.annotate. Kein
Spielvideo - dafuer muesste CS2 mit GPU laufen.
"""

from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file

from .. import aim_trace, costly_moments, key_moments, map3d, map3d_render
from ..maps import MAP_RADAR_DATA
from ..obsidian import export_sort_key

MAX_EXPORTS = 30


def _export_dir(cfg: dict) -> Path | None:
    vault = cfg.get("obsidian_vault_path", "")
    if not vault:
        return None
    return Path(vault) / cfg.get("coach_subfolder", "CS2-Coach") / "exports"


def _load(export_dir: Path, filename: str) -> dict | None:
    path = export_dir / filename
    if not path.resolve().is_relative_to(export_dir.resolve()) or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _me(cfg: dict, data: dict) -> str:
    return str(cfg.get("steam_id") or (data.get("player") or {}).get("steam_id") or "")


def create_blueprint(load_config, config_path) -> Blueprint:
    bp = Blueprint("moments_page", __name__)

    @bp.route("/momente")
    def page():
        cfg = load_config()
        edir = _export_dir(cfg)
        files = sorted(edir.glob("*_coach.json"), key=export_sort_key, reverse=True)[:MAX_EXPORTS] \
            if edir and edir.exists() else []
        chosen = request.args.get("file") or (files[0].name if files else "")
        data = _load(edir, chosen) if edir and chosen else None
        moments, good, error = [], [], ""
        if data:
            stem = Path((data.get("match") or {}).get("demo_file") or "").stem
            try:
                own, kills = key_moments.priced(cfg, Path(config_path()), _me(cfg, data),
                                                prefer=[stem] if stem else None, scan_new=1)
                moments = costly_moments.for_match(data, own, top=6)
                good = key_moments.best_kills(data, kills, top=5)
            except Exception as e:  # noqa: BLE001 - Seite bleibt nutzbar, zeigt den Grund
                error = f"Siegchance nicht berechenbar: {e}"
            for m in moments:
                m["headline"] = key_moments.headline(m)
            for m in good:
                m["headline"] = key_moments.kill_headline(m)
        demo_ok = bool(data and cfg.get("demo_folder") and (Path(cfg["demo_folder"]) /
                       ((data.get("match") or {}).get("demo_file") or "-")).exists())
        return render_template("moments.html", files=[f.name for f in files], chosen=chosen,
                               match=(data or {}).get("match") or {}, moments=moments, good=good,
                               demo_ok=demo_ok, error=error, config=cfg)

    @bp.route("/api/momente/<path:filename>/<int:round_num>/<int:tick>")
    def clip(filename, round_num, tick):
        from .app import build_round_replay
        cfg = load_config()
        edir = _export_dir(cfg)
        data = _load(edir, filename) if edir else None
        if data is None:
            return jsonify({"error": "Export nicht gefunden"}), 404
        match = data.get("match") or {}
        demo = Path(cfg.get("demo_folder", "")) / (match.get("demo_file") or "-")
        if not demo.exists():
            return jsonify({"error": f"Demo fehlt: {match.get('demo_file')}"}), 404
        map_key = (match.get("map") or "").lower()
        if map_key not in MAP_RADAR_DATA:
            return jsonify({"error": f"Map ohne Radardaten: {map_key}"}), 400
        replay = build_round_replay(str(demo), round_num, _me(cfg, data), map_key)
        if "error" in replay:
            return jsonify(replay), 400
        replay["map"] = map_key
        replay["scale"] = MAP_RADAR_DATA[map_key]["scale"]
        is_kill = request.args.get("kind") == "kill"
        scale = MAP_RADAR_DATA[map_key]["scale"]
        mo = (key_moments.annotate_kill if is_kill else key_moments.annotate)(replay, tick, scale)
        mo["events"] = []
        facts = {}
        if mo.get("killer") and mo.get("killer_idx") is not None:
            name = replay["players"][mo["killer_idx"]]["name"]
            try:
                facts = key_moments.scan_duel(str(demo), _me(cfg, data), mo["killer"], mo["death_tick"])
                notes, mo["events"] = key_moments.duel_notes(facts, name, kill=is_kill)
                mo["notes"] = mo["notes"][:1] + notes + mo["notes"][1:]
            except Exception as e:  # noqa: BLE001 - Clip bleibt nutzbar ohne Duell-Detail
                mo["notes"].append({"tone": "info", "text": f"Duell-Detail nicht lesbar: {e}"})
        replay["moment"] = mo
        if request.args.get("3d"):
            # 32 Hz statt der 8 Hz der 2D-Frames - der Browser interpoliert dazwischen
            fr = replay["frames"]
            clip_ticks = list(range(fr[mo["clip"][0]]["t"], fr[mo["clip"][1]]["t"] + 1, key_moments.VIEW3D_STEP))
            try:
                replay["view3d"] = key_moments.scan_3d(str(demo), replay["players"], clip_ticks)
            except Exception as e:  # noqa: BLE001 - 2D bleibt
                replay["view3d_error"] = str(e)
            if mo.get("killer"):
                start = max(clip_ticks[0] if clip_ticks else 0, mo["death_tick"] - 4 * key_moments.TICKRATE)
                try:
                    replay["aim"] = aim_trace.scan(str(demo), _me(cfg, data), mo["killer"], start,
                                                   mo["death_tick"], facts.get("my_sight"))
                except Exception as e:  # noqa: BLE001 - 3D bleibt ohne Aim-Spur
                    replay["aim_error"] = str(e)
        replay["has_geo"] = map3d.geo_path(cfg, Path(config_path()), map_key) is not None
        replay["has_render"] = map3d_render.render_path(cfg, Path(config_path()), map_key) is not None
        return jsonify(replay)

    @bp.route("/api/momente/geo/<map_key>")
    def geo(map_key):
        """Kartengeometrie (vorkomprimiert) - der Browser entpackt sie selbst ueber Content-Encoding."""
        path = map3d.geo_path(load_config(), Path(config_path()), map_key)
        if path is None:
            return jsonify({"error": "Für diese Map gibt es noch keine 3D-Geometrie"}), 404
        resp = send_file(path, mimetype="application/octet-stream", max_age=7 * 86400)
        resp.headers["Content-Encoding"] = "gzip"
        return resp

    @bp.route("/api/momente/render/<map_key>")
    def render(map_key):
        """Texturierte Map (map3d_render.py), vorkomprimiertes glb."""
        path = map3d_render.render_path(load_config(), Path(config_path()), map_key)
        if path is None:
            return jsonify({"error": "Für diese Map gibt es noch keine texturierte 3D-Map"}), 404
        resp = send_file(path, mimetype="model/gltf-binary", max_age=7 * 86400)
        resp.headers["Content-Encoding"] = "gzip"
        return resp

    return bp
