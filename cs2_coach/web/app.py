"""Flask-App für CS2 Coach Web-Interface."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import yaml
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response, stream_with_context
from werkzeug.utils import secure_filename

from demoparser2 import DemoParser

from ..parser import parse_demo, MatchResult
from ..coach import generate_report
from ..obsidian import export_match
from ..maps import MAP_RADAR_DATA, game_to_radar

CONFIG_PATH = Path(__file__).parent.parent.parent / "config.yaml"
UPLOAD_FOLDER = tempfile.mkdtemp(prefix="cs2coach_")
ALLOWED_EXTENSIONS = {".dem"}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = os.urandom(24)
    app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
    app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500MB max
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # no static file caching in dev

    cfg = load_config()

    @app.route("/")
    def index():
        exports = _get_exports(cfg)
        map_stats = _get_map_stats(exports)
        dashboard = _build_dashboard_data(exports, map_stats)
        return render_template("index.html", exports=exports, map_stats=map_stats,
                               dashboard=dashboard, config=cfg)

    @app.route("/analyze", methods=["POST"])
    def analyze():
        player_name = request.form.get("player", cfg.get("player_name", ""))
        steam_id = request.form.get("steamid", cfg.get("steam_id", ""))

        # File upload
        if "demo" not in request.files:
            flash("Keine Demo-Datei ausgewählt.", "error")
            return redirect(url_for("index"))

        file = request.files["demo"]
        if file.filename == "":
            flash("Keine Demo-Datei ausgewählt.", "error")
            return redirect(url_for("index"))

        if not file.filename.lower().endswith(".dem"):
            flash("Nur .dem-Dateien werden unterstützt.", "error")
            return redirect(url_for("index"))

        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(filepath)

        try:
            result = parse_demo(filepath, player_name, steam_id)
            report = generate_report(result)

            # Obsidian export
            vault_path = cfg.get("obsidian_vault_path", "")
            subfolder = cfg.get("coach_subfolder", "CS2-Coach")
            obsidian_path = None
            if vault_path:
                obsidian_path = str(export_match(result, report, vault_path, subfolder))

            return render_template(
                "result.html",
                result=result,
                report=report,
                stats=result.player_stats,
                obsidian_path=obsidian_path,
                kill_map_data=_build_kill_map_data(result),
                duel_matrix=result.duel_matrix,
                round_timeline=result.round_timeline,
                economy=result.economy_performance,
                config=cfg,
            )
        except Exception as e:
            flash(f"Fehler beim Parsen: {e}", "error")
            return redirect(url_for("index"))
        finally:
            if os.path.exists(filepath):
                os.remove(filepath)

    @app.route("/analyze-path", methods=["POST"])
    def analyze_path():
        """Analyze a demo from a local file path (no upload needed)."""
        demo_path = request.form.get("demo_path", "").strip()
        player_name = request.form.get("player", cfg.get("player_name", ""))
        steam_id = request.form.get("steamid", cfg.get("steam_id", ""))

        if not demo_path or not Path(demo_path).exists():
            flash(f"Demo nicht gefunden: {demo_path}", "error")
            return redirect(url_for("index"))

        try:
            result = parse_demo(demo_path, player_name, steam_id)
            report = generate_report(result)

            vault_path = cfg.get("obsidian_vault_path", "")
            subfolder = cfg.get("coach_subfolder", "CS2-Coach")
            obsidian_path = None
            if vault_path:
                obsidian_path = str(export_match(result, report, vault_path, subfolder))

            return render_template(
                "result.html",
                result=result,
                report=report,
                stats=result.player_stats,
                obsidian_path=obsidian_path,
                kill_map_data=_build_kill_map_data(result),
                duel_matrix=result.duel_matrix,
                round_timeline=result.round_timeline,
                economy=result.economy_performance,
                config=cfg,
            )
        except Exception as e:
            flash(f"Fehler beim Parsen: {e}", "error")
            return redirect(url_for("index"))

    @app.route("/api/analyze", methods=["POST"])
    def api_analyze():
        """JSON-API: Demo analysieren und Raw-JSON zurückgeben."""
        player_name = request.form.get("player", cfg.get("player_name", ""))
        steam_id = request.form.get("steamid", cfg.get("steam_id", ""))

        if "demo" not in request.files:
            return jsonify({"error": "Keine Demo-Datei"}), 400

        file = request.files["demo"]
        if not file.filename.lower().endswith(".dem"):
            return jsonify({"error": "Nur .dem-Dateien"}), 400

        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(filepath)

        try:
            result = parse_demo(filepath, player_name, steam_id)
            raw = _build_api_json(result)
            return jsonify(raw)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        finally:
            if os.path.exists(filepath):
                os.remove(filepath)

    @app.route("/api/players", methods=["POST"])
    def api_players():
        """Return player list from a demo file (upload or local path)."""
        demo_path = request.form.get("demo_path", "").strip()

        if demo_path and Path(demo_path).exists():
            filepath = demo_path
            cleanup = False
        elif "demo" in request.files and request.files["demo"].filename:
            file = request.files["demo"]
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            file.save(filepath)
            cleanup = True
        else:
            return jsonify({"error": "Keine Demo angegeben"}), 400

        try:
            p = DemoParser(str(filepath))
            info = p.parse_player_info()
            players = []
            for _, row in info.iterrows():
                name = str(row.get("name", ""))
                sid = str(row.get("steamid", ""))
                if name and sid:
                    players.append({"name": name, "steamid": sid})
            return jsonify({"players": players})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        finally:
            if cleanup and os.path.exists(filepath):
                os.remove(filepath)

    @app.route("/batch", methods=["GET"])
    def batch():
        return render_template("batch.html", config=cfg)

    @app.route("/api/scan-folder")
    def scan_folder():
        """List .dem files in a folder and mark which are already analyzed."""
        folder = request.args.get("folder", "").strip()
        folder_path = Path(folder)
        if not folder_path.is_dir():
            return jsonify({"error": "Ordner nicht gefunden"}), 400

        demos = sorted(folder_path.glob("*.dem"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not demos:
            return jsonify({"error": "Keine .dem-Dateien gefunden"}), 400

        # Collect analyzed demo filenames from exports
        analyzed_files = set()
        vault_path = cfg.get("obsidian_vault_path", "")
        sub = cfg.get("coach_subfolder", "CS2-Coach")
        export_dir = Path(vault_path) / sub / "exports" if vault_path else None
        if export_dir and export_dir.exists():
            for ef in export_dir.glob("*_coach.json"):
                try:
                    data = json.loads(ef.read_text(encoding="utf-8"))
                    demo_file = data.get("match", {}).get("demo_file", "")
                    if demo_file:
                        analyzed_files.add(demo_file)
                except Exception:
                    pass

        result = []
        for d in demos:
            mtime = d.stat().st_mtime
            date_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            size_mb = round(d.stat().st_size / (1024 * 1024), 1)
            result.append({
                "filename": d.name,
                "path": str(d),
                "date": date_str,
                "size_mb": size_mb,
                "analyzed": d.name in analyzed_files,
            })

        analyzed_count = sum(1 for r in result if r["analyzed"])
        return jsonify({
            "demos": result,
            "total": len(result),
            "analyzed": analyzed_count,
            "new": len(result) - analyzed_count,
        })

    @app.route("/api/batch-stream")
    def batch_stream():
        """SSE endpoint: streams progress for each demo in a folder."""
        folder = request.args.get("folder", "").strip()
        player_name = request.args.get("player", cfg.get("player_name", ""))
        steam_id = request.args.get("steamid", cfg.get("steam_id", ""))
        new_only = request.args.get("new_only", "") == "1"

        folder_path = Path(folder)
        if not folder_path.is_dir():
            def err():
                yield f"data: {json.dumps({'type': 'error', 'message': 'Ordner nicht gefunden'})}\n\n"
            return Response(err(), mimetype="text/event-stream")

        demos = sorted(folder_path.glob("*.dem"))
        if not demos:
            def err():
                yield f"data: {json.dumps({'type': 'error', 'message': 'Keine .dem-Dateien gefunden'})}\n\n"
            return Response(err(), mimetype="text/event-stream")

        if new_only:
            analyzed_files: set[str] = set()
            vault_path = cfg.get("obsidian_vault_path", "")
            sub = cfg.get("coach_subfolder", "CS2-Coach")
            export_dir = Path(vault_path) / sub / "exports" if vault_path else None
            if export_dir and export_dir.exists():
                for ef in export_dir.glob("*_coach.json"):
                    try:
                        data = json.loads(ef.read_text(encoding="utf-8"))
                        demo_file = data.get("match", {}).get("demo_file", "")
                        if demo_file:
                            analyzed_files.add(demo_file)
                    except Exception:
                        pass
            demos = [d for d in demos if d.name not in analyzed_files]
            if not demos:
                def err():
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Keine neuen Demos gefunden'})}\n\n"
                return Response(err(), mimetype="text/event-stream")

        def generate():
            total = len(demos)
            yield f"data: {json.dumps({'type': 'init', 'total': total})}\n\n"

            for i, demo in enumerate(demos):
                try:
                    result = parse_demo(str(demo), player_name, steam_id)
                    report = generate_report(result)

                    vault_path = cfg.get("obsidian_vault_path", "")
                    sub = cfg.get("coach_subfolder", "CS2-Coach")
                    if vault_path:
                        export_match(result, report, vault_path, sub)

                    s = result.player_stats
                    yield f"data: {json.dumps({'type': 'result', 'current': i + 1, 'total': total, 'demo_path': str(demo), 'filename': demo.name, 'map': result.map_name, 'date': result.match_date, 'score': f'{result.score_team1}:{result.score_team2}', 'result_str': result.result_str, 'kills': s.kills, 'deaths': s.deaths, 'assists': s.assists, 'adr': round(s.adr, 1), 'hs_pct': round(s.headshot_pct, 1), 'kast_pct': round(s.kast_pct, 1), 'rating': result.rating})}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'error_item', 'current': i + 1, 'total': total, 'filename': demo.name, 'message': str(e)})}\n\n"

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        return Response(stream_with_context(generate()), mimetype="text/event-stream")

    @app.route("/trends")
    def trends():
        export_list = _get_exports(cfg)
        map_stats = _get_map_stats(export_list)
        return render_template("trends.html", exports=export_list, map_stats=map_stats, config=cfg)

    @app.route("/sessions")
    def sessions():
        export_list = _get_exports(cfg)
        session_data = _build_sessions(export_list)
        return render_template("sessions.html", sessions=session_data, config=cfg)

    @app.route("/exports")
    def exports():
        export_list = _get_exports(cfg)
        return render_template("exports.html", exports=export_list, config=cfg)

    @app.route("/export/<path:filename>")
    def view_export(filename):
        vault_path = cfg.get("obsidian_vault_path", "")
        subfolder = cfg.get("coach_subfolder", "CS2-Coach")
        export_dir = Path(vault_path) / subfolder / "exports"
        filepath = export_dir / filename

        if not filepath.resolve().is_relative_to(export_dir.resolve()) or not filepath.exists():
            flash("Export nicht gefunden.", "error")
            return redirect(url_for("exports"))

        data = json.loads(filepath.read_text(encoding="utf-8"))
        analysis = _analyze_rounds(data)
        return render_template("export_detail.html", data=data, analysis=analysis,
                               filename=filename, config=cfg)

    @app.route("/settings")
    def settings():
        # Always reload config from disk
        current = load_config()
        # Count exports
        vault_path = current.get("obsidian_vault_path", "")
        subfolder = current.get("coach_subfolder", "CS2-Coach")
        export_dir = Path(vault_path) / subfolder / "exports" if vault_path else None
        export_count = len(list(export_dir.glob("*_coach.json"))) if export_dir and export_dir.exists() else 0
        notes_dir = Path(vault_path) / subfolder if vault_path else None
        notes_count = len(list(notes_dir.glob("*.md"))) if notes_dir and notes_dir.exists() else 0
        # Count pycache
        project_root = Path(__file__).parent.parent.parent
        pycache_dirs = list(project_root.rglob("__pycache__"))
        pyc_count = sum(len(list(d.glob("*.pyc"))) for d in pycache_dirs)
        return render_template(
            "settings.html",
            config=current,
            config_path=str(CONFIG_PATH),
            export_count=export_count,
            notes_count=notes_count,
            pyc_count=pyc_count,
            pycache_count=len(pycache_dirs),
        )

    @app.route("/settings/save", methods=["POST"])
    def settings_save():
        new_cfg = {
            "obsidian_vault_path": request.form.get("obsidian_vault_path", "").strip(),
            "coach_subfolder": request.form.get("coach_subfolder", "CS2-Coach").strip(),
            "player_name": request.form.get("player_name", "").strip(),
            "steam_id": request.form.get("steam_id", "").strip(),
            "language": request.form.get("language", "de").strip(),
        }
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write("# CS2-Coach Konfiguration\n")
            yaml.dump(new_cfg, f, default_flow_style=False, allow_unicode=True)
        # Reload into running app
        cfg.clear()
        cfg.update(new_cfg)
        flash("Einstellungen gespeichert.", "success")
        return redirect(url_for("settings"))

    @app.route("/settings/clear-cache", methods=["POST"])
    def settings_clear_cache():
        project_root = Path(__file__).parent.parent.parent
        cleared = 0
        for d in project_root.rglob("__pycache__"):
            shutil.rmtree(d, ignore_errors=True)
            cleared += 1
        flash(f"{cleared} __pycache__-Ordner geloescht. Neustart empfohlen.", "success")
        return redirect(url_for("settings"))

    @app.route("/settings/reset-exports", methods=["POST"])
    def settings_reset_exports():
        vault_path = cfg.get("obsidian_vault_path", "")
        subfolder = cfg.get("coach_subfolder", "CS2-Coach")
        if not vault_path:
            flash("Kein Vault-Pfad konfiguriert.", "error")
            return redirect(url_for("settings"))
        removed = 0
        export_dir = Path(vault_path) / subfolder / "exports"
        if export_dir.exists():
            for f in export_dir.glob("*_coach.json"):
                f.unlink()
                removed += 1
        flash(f"{removed} Export-Dateien geloescht.", "success")
        return redirect(url_for("settings"))

    @app.route("/settings/reset-notes", methods=["POST"])
    def settings_reset_notes():
        vault_path = cfg.get("obsidian_vault_path", "")
        subfolder = cfg.get("coach_subfolder", "CS2-Coach")
        if not vault_path:
            flash("Kein Vault-Pfad konfiguriert.", "error")
            return redirect(url_for("settings"))
        removed = 0
        notes_dir = Path(vault_path) / subfolder
        if notes_dir.exists():
            for f in notes_dir.glob("*.md"):
                f.unlink()
                removed += 1
        flash(f"{removed} Obsidian-Notizen geloescht.", "success")
        return redirect(url_for("settings"))

    @app.route("/settings/reset-all", methods=["POST"])
    def settings_reset_all():
        vault_path = cfg.get("obsidian_vault_path", "")
        subfolder = cfg.get("coach_subfolder", "CS2-Coach")
        removed = 0
        if vault_path:
            base = Path(vault_path) / subfolder
            for subdir in ["exports", "matches", "concepts"]:
                d = base / subdir
                if d.exists():
                    for f in d.iterdir():
                        if f.is_file():
                            f.unlink()
                            removed += 1
        # Also clear pycache
        project_root = Path(__file__).parent.parent.parent
        for d in project_root.rglob("__pycache__"):
            shutil.rmtree(d, ignore_errors=True)
        flash(f"Komplett-Reset: {removed} Dateien + __pycache__ geloescht.", "success")
        return redirect(url_for("settings"))

    return app


def _get_exports(cfg: dict) -> list[dict]:
    vault_path = cfg.get("obsidian_vault_path", "")
    subfolder = cfg.get("coach_subfolder", "CS2-Coach")
    if not vault_path:
        return []

    export_dir = Path(vault_path) / subfolder / "exports"
    if not export_dir.exists():
        return []

    exports = []
    for f in sorted(export_dir.glob("*_coach.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            match = data.get("match", {})
            player = data.get("player", {})
            weapons = player.get("weapons", {})
            side = player.get("side_split", {})
            exports.append({
                "filename": f.name,
                "date": match.get("date", match.get("datetime", "?")),
                "map": match.get("map", "?"),
                "total_rounds": match.get("total_rounds", 0),
                "score": f"{match.get('score_own', '?')}:{match.get('score_enemy', '?')}",
                "result": match.get("result", "?"),
                "kd": player.get("kd", 0),
                "adr": player.get("adr", 0),
                "rating": player.get("rating", 0),
                "kast": player.get("kast_pct", 0),
                "hs_pct": player.get("hs_pct", 0),
                "kills": player.get("kills", 0),
                "deaths": player.get("deaths", 0),
                "assists": player.get("assists", 0),
                "crosshair_placement": player.get("crosshair_placement", {}).get("avg_degrees", 0),
                "counter_strafe": player.get("counter_strafe_score", 0),
                "utility_per_round": player.get("utility_per_round", 0),
                "opening_kills": player.get("opening_kills", 0),
                "opening_deaths": player.get("opening_deaths", 0),
                "trade_kills": player.get("trade_kills", 0),
                "survival_rate": player.get("survival_rate", 0),
                "awp_kills": weapons.get("awp_kills", 0),
                "rifle_kills": weapons.get("rifle_kills", 0),
                "pistol_kills": weapons.get("pistol_kills", 0),
                "avg_fight_distance": player.get("engagement_distance", {}).get("avg", 0),
                "clutch_wins": player.get("clutches", {}).get("wins", 0),
                "clutch_attempts": player.get("clutches", {}).get("attempts", 0),
                "ct_kills": side.get("ct_kills", 0),
                "ct_deaths": side.get("ct_deaths", 0),
                "t_kills": side.get("t_kills", 0),
                "t_deaths": side.get("t_deaths", 0),
                "deaths_early": player.get("death_timing", {}).get("early", 0),
                "deaths_mid": player.get("death_timing", {}).get("mid", 0),
                "deaths_late": player.get("death_timing", {}).get("late", 0),
                "flash_enemies": player.get("flash_effectiveness", {}).get("enemies_blinded", 0),
                "flash_teammates": player.get("flash_effectiveness", {}).get("teammates_blinded", 0),
            })
        except (json.JSONDecodeError, KeyError):
            continue

    return exports


def _build_api_json(result: MatchResult) -> dict:
    s = result.player_stats
    scoreboard = []
    for p in sorted(result.all_players, key=lambda x: x.kills, reverse=True):
        scoreboard.append({
            "name": p.name,
            "kills": p.kills,
            "deaths": p.deaths,
            "assists": p.assists,
            "adr": round(p.adr, 1),
            "kd": round(p.kd_ratio, 2),
            "is_target": p.steam_id == s.steam_id,
        })

    return {
        "match": {
            "map": result.map_name,
            "score_own": result.score_team1,
            "score_enemy": result.score_team2,
            "result": result.result_str,
            "total_rounds": result.total_rounds,
        },
        "player": {
            "name": s.name,
            "kills": s.kills,
            "deaths": s.deaths,
            "assists": s.assists,
            "kd": round(s.kd_ratio, 2),
            "adr": round(s.adr, 1),
            "hs_pct": round(s.headshot_pct, 1),
            "kast_pct": round(s.kast_pct, 1),
            "rating": result.rating,
        },
        "report": generate_report(result),
        "scoreboard": scoreboard,
    }


def _build_kill_map_data(result: MatchResult) -> dict | None:
    """Build kill map JSON for canvas rendering. Returns None if map not supported."""
    map_key = result.map_name.lower()
    if map_key not in MAP_RADAR_DATA:
        return None
    if not result.kill_positions:
        return None

    target_id = result.player_stats.steam_id
    total_rounds = result.score_team1 + result.score_team2
    dots = []
    for kp in result.kill_positions:
        att_pos = game_to_radar(kp["attacker_x"], kp["attacker_y"], map_key)
        vic_pos = game_to_radar(kp["victim_x"], kp["victim_y"], map_key)
        if not att_pos or not vic_pos:
            continue

        is_attacker = kp["attacker_id"] == target_id
        is_victim = kp["victim_id"] == target_id
        round_num = kp.get("round", 0)

        if is_attacker:
            dots.append({
                "x": round(att_pos[0], 1), "y": round(att_pos[1], 1),
                "type": "kill", "weapon": kp["weapon"],
                "headshot": kp["headshot"],
                "label": f"Kill: {kp['victim_name']}",
                "round": round_num,
            })
        if is_victim:
            dots.append({
                "x": round(vic_pos[0], 1), "y": round(vic_pos[1], 1),
                "type": "death", "weapon": kp["weapon"],
                "label": f"Death by {kp['attacker_name']}",
                "round": round_num,
            })

    # Utility positions
    utils = []
    for up in getattr(result, "utility_positions", []):
        pos = game_to_radar(up["x"], up["y"], map_key)
        if not pos:
            continue
        utils.append({
            "x": round(pos[0], 1), "y": round(pos[1], 1),
            "type": up["type"],
            "player": up["player_name"],
            "player_id": up["player_id"],
            "is_own": up["player_id"] == target_id,
            "round": up.get("round", 0),
        })

    return {
        "map": map_key,
        "dots": dots,
        "utils": utils,
        "total_rounds": total_rounds,
    }


def _get_map_stats(exports: list[dict]) -> list[dict]:
    """Aggregate per-map statistics from exports."""
    if not exports:
        return []

    by_map: dict[str, list[dict]] = {}
    for e in exports:
        m = e.get("map", "?")
        by_map.setdefault(m, []).append(e)

    result = []
    for map_name, matches in sorted(by_map.items()):
        n = len(matches)
        wins = sum(1 for m in matches if m.get("result") == "Sieg")
        losses = sum(1 for m in matches if m.get("result") == "Niederlage")
        draws = n - wins - losses

        avg_rating = round(sum(m.get("rating", 0) for m in matches) / n, 2)
        avg_kd = round(sum(m.get("kd", 0) for m in matches) / n, 2)
        avg_adr = round(sum(m.get("adr", 0) for m in matches) / n, 1)
        avg_kast = round(sum(m.get("kast", 0) for m in matches) / n, 1)
        avg_hs = round(sum(m.get("hs_pct", 0) for m in matches) / n, 1)
        total_kills = sum(m.get("kills", 0) for m in matches)
        total_deaths = sum(m.get("deaths", 0) for m in matches)
        win_rate = round(wins / n * 100, 1)

        result.append({
            "map": map_name,
            "matches": n,
            "wins": wins,
            "losses": losses,
            "draws": draws,
            "win_rate": win_rate,
            "avg_rating": avg_rating,
            "avg_kd": avg_kd,
            "avg_adr": avg_adr,
            "avg_kast": avg_kast,
            "avg_hs": avg_hs,
            "total_kills": total_kills,
            "total_deaths": total_deaths,
        })

    return sorted(result, key=lambda x: -x["matches"])


# Skill benchmarks: (min, max) range per rank tier
_BENCHMARKS = {
    "adr":       {"tiers": [("Silver", 55), ("Gold Nova", 65), ("MG", 73), ("DMG/LE", 80), ("Faceit 4-6", 85), ("Faceit 7+", 92), ("Pro", 98)], "label": "ADR", "desc": "Average Damage per Round", "fmt": ".1f", "unit": ""},
    "hs_pct":    {"tiers": [("Silver", 30), ("Gold Nova", 38), ("MG", 42), ("DMG/LE", 48), ("Faceit 4-6", 52), ("Faceit 7+", 56), ("Pro", 60)], "label": "HS%", "desc": "Headshot-Prozent", "fmt": ".1f", "unit": "%"},
    "kast":      {"tiers": [("Silver", 55), ("Gold Nova", 60), ("MG", 65), ("DMG/LE", 68), ("Faceit 4-6", 72), ("Faceit 7+", 76), ("Pro", 80)], "label": "KAST%", "desc": "Kill, Assist, Survived, Traded", "fmt": ".1f", "unit": "%"},
    "rating":    {"tiers": [("Silver", 0.6), ("Gold Nova", 0.75), ("MG", 0.85), ("DMG/LE", 0.95), ("Faceit 4-6", 1.0), ("Faceit 7+", 1.08), ("Pro", 1.18)], "label": "Rating", "desc": "Gesamtbewertung", "fmt": ".2f", "unit": ""},
    "counter_strafe": {"tiers": [("Silver", 50), ("Gold Nova", 60), ("MG", 70), ("DMG/LE", 78), ("Faceit 4-6", 83), ("Faceit 7+", 88), ("Pro", 93)], "label": "Counter-Strafe", "desc": "Anteil stehender Schuesse", "fmt": ".1f", "unit": "%"},
    "utility":   {"tiers": [("Silver", 0.5), ("Gold Nova", 0.8), ("MG", 1.2), ("DMG/LE", 1.6), ("Faceit 4-6", 2.0), ("Faceit 7+", 2.5), ("Pro", 3.0)], "label": "Utility/Runde", "desc": "Granaten pro Runde", "fmt": ".2f", "unit": ""},
    "crosshair": {"tiers": [("Pro", 5), ("Faceit 7+", 8), ("Faceit 4-6", 12), ("DMG/LE", 16), ("MG", 22), ("Gold Nova", 28), ("Silver", 35)], "label": "Crosshair Placement", "desc": "Fadenkreuz-Abweichung in Grad (niedriger = besser)", "fmt": ".1f", "unit": "°", "lower_is_better": True},
}


def _rank_for_value(key: str, value: float) -> tuple[str, float]:
    """Return (rank_label, pct 0-100) for a benchmark metric."""
    bm = _BENCHMARKS[key]
    tiers = bm["tiers"]
    lower_better = bm.get("lower_is_better", False)

    if lower_better:
        # Tiers sorted best→worst (5, 8, 12, ... 35)
        for i, (name, threshold) in enumerate(tiers):
            if value <= threshold:
                pct = 100 - (i / len(tiers)) * 100
                return name, min(pct, 100)
        return tiers[-1][0], 5.0
    else:
        # Tiers sorted worst→best
        for i in range(len(tiers) - 1, -1, -1):
            if value >= tiers[i][1]:
                pct = ((i + 1) / len(tiers)) * 100
                return tiers[i][0], min(pct, 100)
        return tiers[0][0], 5.0


def _build_sessions(exports: list[dict]) -> list[dict]:
    """Group exports by date into sessions, detect tilt and performance trends."""
    if not exports:
        return []

    # Group by date
    by_date: dict[str, list[dict]] = {}
    for e in exports:
        date = e.get("date", "?")[:10]  # YYYY-MM-DD
        by_date.setdefault(date, []).append(e)

    sessions = []
    for date in sorted(by_date.keys(), reverse=True):
        matches = by_date[date]
        n = len(matches)

        wins = sum(1 for m in matches if m.get("result") == "Sieg")
        losses = sum(1 for m in matches if m.get("result") == "Niederlage")
        draws = n - wins - losses
        win_rate = round(wins / n * 100, 1)

        avg_rating = round(sum(m.get("rating", 0) for m in matches) / n, 2)
        avg_kd = round(sum(m.get("kd", 0) for m in matches) / n, 2)
        avg_adr = round(sum(m.get("adr", 0) for m in matches) / n, 1)

        # Performance degradation: compare first half vs second half of session
        tilt = None
        if n >= 3:
            mid = n // 2
            first_half = matches[:mid] if mid > 0 else matches[:1]
            second_half = matches[mid:]
            first_rating = sum(m.get("rating", 0) for m in first_half) / len(first_half)
            second_rating = sum(m.get("rating", 0) for m in second_half) / len(second_half)
            rating_drop = first_rating - second_rating

            first_kd = sum(m.get("kd", 0) for m in first_half) / len(first_half)
            second_kd = sum(m.get("kd", 0) for m in second_half) / len(second_half)

            if rating_drop > 0.15 and second_rating < 0.9:
                tilt = {
                    "type": "tilt",
                    "label": "Tilt erkannt",
                    "desc": f"Rating fiel von {first_rating:.2f} auf {second_rating:.2f} im Verlauf der Session",
                    "drop": round(rating_drop, 2),
                }
            elif rating_drop > 0.1:
                tilt = {
                    "type": "fatigue",
                    "label": "Ermuedung",
                    "desc": f"Leistung sank leicht: {first_rating:.2f} -> {second_rating:.2f}",
                    "drop": round(rating_drop, 2),
                }
            elif rating_drop < -0.1:
                tilt = {
                    "type": "warmup",
                    "label": "Warmup-Effekt",
                    "desc": f"Leistung stieg: {first_rating:.2f} -> {second_rating:.2f}",
                    "drop": round(rating_drop, 2),
                }

        # Loss streak within session
        max_loss_streak = 0
        current_streak = 0
        for m in matches:
            if m.get("result") == "Niederlage":
                current_streak += 1
                max_loss_streak = max(max_loss_streak, current_streak)
            else:
                current_streak = 0

        # Match-by-match rating trend for sparkline
        ratings = [m.get("rating", 0) for m in matches]

        sessions.append({
            "date": date,
            "matches": n,
            "wins": wins,
            "losses": losses,
            "draws": draws,
            "win_rate": win_rate,
            "avg_rating": avg_rating,
            "avg_kd": avg_kd,
            "avg_adr": avg_adr,
            "tilt": tilt,
            "max_loss_streak": max_loss_streak,
            "ratings": ratings,
            "match_details": [{
                "map": m.get("map", "?"),
                "result": m.get("result", "?"),
                "score": m.get("score", "?"),
                "rating": m.get("rating", 0),
                "kd": m.get("kd", 0),
                "adr": m.get("adr", 0),
                "filename": m.get("filename", ""),
            } for m in matches],
        })

    return sessions


def _analyze_rounds(data: dict) -> dict:
    """Analyze round_timeline for death categories and round highlights."""
    timeline = data.get("round_timeline", [])
    player = data.get("player", {})
    if not timeline:
        return {"deaths": [], "death_summary": {}, "highlights": [], "highlight_summary": {}}

    total_rounds = len(timeline)

    # ── Death Categorization ──
    # Categories: Positioning (died without kills, early), Timing (died early with bad pct),
    # Aim (died in duel — had a kill event close to death), Utility (died to nade/molotov),
    # Numbers (died when outnumbered — 0 kills, late in round)
    death_categories = []
    util_weapons = {"hegrenade", "molotov", "incgrenade", "inferno", "flashbang"}

    for r in timeline:
        if not r.get("player_died"):
            continue

        death_event = None
        kill_events = []
        for e in r["events"]:
            if e["type"] == "death":
                death_event = e
            elif e["type"] == "kill":
                kill_events.append(e)

        if not death_event:
            continue

        death_pct = death_event.get("pct", 50)
        weapon = death_event.get("weapon", "")
        kills_in_round = r.get("player_kills", 0)

        # Determine category
        if weapon in util_weapons:
            category = "Utility"
            reason = f"Gestorben durch {weapon}"
        elif r.get("died_early") or death_pct <= 25:
            if kills_in_round == 0:
                category = "Positioning"
                reason = "Frueh gestorben ohne Impact — schlechte Position"
            else:
                category = "Timing"
                reason = "Frueh gestorben trotz Kill — Overpeek"
        elif kills_in_round == 0 and death_pct >= 60:
            category = "Numbers"
            reason = "Spaet gestorben ohne Kill — Ueberzahl-Situation verloren"
        elif kills_in_round > 0 and any(abs(k["pct"] - death_pct) < 10 for k in kill_events):
            category = "Aim"
            reason = f"Duell verloren ({kills_in_round}K)"
        elif kills_in_round == 0:
            category = "Positioning"
            reason = "Gestorben ohne Trade-Moeglichkeit"
        else:
            category = "Aim"
            reason = f"Gestorben nach {kills_in_round} Kill(s)"

        death_categories.append({
            "round": r["round"],
            "side": r["side"],
            "won": r["won"],
            "category": category,
            "reason": reason,
            "weapon": weapon,
            "killer": death_event.get("killer", "?"),
            "death_pct": death_pct,
            "kills": kills_in_round,
        })

    # Summary counts
    cat_counts = {}
    for d in death_categories:
        cat_counts[d["category"]] = cat_counts.get(d["category"], 0) + 1

    death_summary = {
        "total": len(death_categories),
        "categories": cat_counts,
        "worst": max(cat_counts, key=cat_counts.get) if cat_counts else None,
    }

    # ── Round Highlights ──
    highlights = []
    for r in timeline:
        kills = r.get("player_kills", 0)
        died = r.get("player_died", False)
        won = r.get("won", False)
        events = r.get("events", [])

        # Check for opening kill (first kill event with low pct)
        has_opening = False
        kill_events = [e for e in events if e["type"] == "kill"]
        if kill_events and kill_events[0].get("pct", 100) <= 35:
            has_opening = True

        # Check for clutch (last alive, multiple enemies)
        has_bomb = any(e["type"] in ("bomb_plant", "bomb_defuse") and e.get("is_self") for e in events)

        # Classify round
        if kills >= 3:
            tag = "hero"
            label = f"{kills}K Highlight"
        elif has_opening and won and kills >= 1:
            tag = "impact"
            label = "Opening-Kill + Win"
        elif has_bomb and won:
            tag = "clutch"
            label = "Bombe + Sieg"
        elif kills >= 2 and won:
            tag = "impact"
            label = f"{kills}K Impact"
        elif not died and won and kills == 0:
            tag = "silent"
            label = "Survived (kein Kill)"
        elif kills == 0 and died and r.get("died_early"):
            tag = "throw"
            label = "Frueh gestorben, 0 Impact"
        elif kills == 0 and died and not won:
            tag = "invisible"
            label = "0K Death, Runde verloren"
        else:
            tag = "normal"
            label = None

        if tag != "normal":
            highlights.append({
                "round": r["round"],
                "side": r["side"],
                "won": won,
                "tag": tag,
                "label": label,
                "kills": kills,
                "died": died,
            })

    # Highlight summary
    tag_counts = {}
    for h in highlights:
        tag_counts[h["tag"]] = tag_counts.get(h["tag"], 0) + 1

    highlight_summary = {
        "total_rounds": total_rounds,
        "hero": tag_counts.get("hero", 0),
        "impact": tag_counts.get("impact", 0),
        "clutch": tag_counts.get("clutch", 0),
        "throw": tag_counts.get("throw", 0),
        "invisible": tag_counts.get("invisible", 0),
        "silent": tag_counts.get("silent", 0),
    }

    return {
        "deaths": death_categories,
        "death_summary": death_summary,
        "highlights": highlights,
        "highlight_summary": highlight_summary,
    }


def _build_dashboard_data(exports: list[dict], map_stats: list[dict]) -> dict:
    """Compute dashboard metrics from export data."""
    if not exports:
        return {"has_data": False}

    n = len(exports)
    last5 = exports[:5]

    # Averages
    avg_rating = round(sum(e.get("rating", 0) for e in exports) / n, 2)
    avg_kd = round(sum(e.get("kd", 0) for e in exports) / n, 2)
    avg_adr = round(sum(e.get("adr", 0) for e in exports) / n, 1)
    avg_kast = round(sum(e.get("kast", 0) for e in exports) / n, 1)
    avg_hs = round(sum(e.get("hs_pct", 0) for e in exports) / n, 1)
    avg_cs = round(sum(e.get("counter_strafe", 0) for e in exports) / n, 1)
    avg_util = round(sum(e.get("utility_per_round", 0) for e in exports) / n, 2)
    avg_cp = round(sum(e.get("crosshair_placement", 0) for e in exports) / n, 1)
    total_kills = sum(e.get("kills", 0) for e in exports)
    total_deaths = sum(e.get("deaths", 0) for e in exports)
    wins = sum(1 for e in exports if e.get("result") == "Sieg")
    losses = sum(1 for e in exports if e.get("result") == "Niederlage")
    draws = n - wins - losses
    win_rate = round(wins / n * 100, 1)

    # Last 5 form
    last5_wins = sum(1 for e in last5 if e.get("result") == "Sieg")
    last5_rating = round(sum(e.get("rating", 0) for e in last5) / len(last5), 2) if last5 else 0
    form_trend = last5_rating - avg_rating  # positive = improving

    # Spider chart: 6 axes normalized to 0-100 against Pro benchmarks
    def _norm(val, lo, hi, lower_better=False):
        if lower_better:
            return max(0, min(100, (hi - val) / max(hi - lo, 0.01) * 100))
        return max(0, min(100, (val - lo) / max(hi - lo, 0.01) * 100))

    spider = {
        "labels": ["Aim (HS%)", "Impact (ADR)", "Consistency (KAST)", "Utility", "Positioning (Survival)", "Praezision (CP)"],
        "scores": [
            round(_norm(avg_hs, 25, 60), 1),
            round(_norm(avg_adr, 50, 98), 1),
            round(_norm(avg_kast, 50, 80), 1),
            round(_norm(avg_util, 0.3, 3.0), 1),
            round(_norm(100 - (total_deaths / max(total_kills, 1)) * 50, 20, 80), 1),
            round(_norm(avg_cp, 5, 35, lower_better=True), 1),
        ],
    }

    # Benchmarks
    benchmarks = []
    bench_values = {
        "adr": avg_adr, "hs_pct": avg_hs, "kast": avg_kast,
        "rating": avg_rating, "counter_strafe": avg_cs,
        "utility": avg_util, "crosshair": avg_cp,
    }
    for key, val in bench_values.items():
        bm = _BENCHMARKS[key]
        rank, pct = _rank_for_value(key, val)
        benchmarks.append({
            "key": key, "label": bm["label"], "desc": bm["desc"],
            "value": val, "fmt": bm["fmt"], "unit": bm["unit"],
            "rank": rank, "pct": pct,
            "lower_is_better": bm.get("lower_is_better", False),
        })

    # Strengths / Weaknesses (top 2 / bottom 2 by percentile)
    ranked = sorted(benchmarks, key=lambda b: b["pct"], reverse=True)
    strengths = ranked[:2]
    weaknesses = ranked[-2:]

    # Focus recommendation (worst benchmark)
    focus_map = {
        "adr": "Mehr Schaden pro Runde machen — Trade-Kills suchen und aggressiver peeken",
        "hs_pct": "Headshot-Rate verbessern — Crosshair Placement auf Kopfhoehe trainieren",
        "kast": "Konstanter spielen — Weniger unnoetige Peeks, mehr traden lassen",
        "rating": "Gesamtperformance verbessern — Fokus auf Impact-Runden",
        "counter_strafe": "Counter-Strafe trainieren — YPRAC Movement Map spielen",
        "utility": "Mehr Utility einsetzen — Smoke/Flash Lineups lernen",
        "crosshair": "Crosshair Placement verbessern — Prefire Maps spielen",
    }
    worst = ranked[-1]
    focus = focus_map.get(worst["key"], "Weiter trainieren!")

    # Best/worst map
    best_map = map_stats[0]["map"] if map_stats else None
    worst_map = None
    if len(map_stats) >= 2:
        # Sort by rating to find worst
        by_rating = sorted(map_stats, key=lambda m: m["avg_rating"])
        worst_map = by_rating[0]["map"]
        best_map_data = sorted(map_stats, key=lambda m: -m["avg_rating"])[0]
        best_map = best_map_data["map"]

    # Streak
    streak = 0
    streak_type = ""
    for e in exports:
        r = e.get("result", "")
        if streak == 0:
            streak_type = r
            streak = 1
        elif r == streak_type:
            streak += 1
        else:
            break

    # ── Role Detection ──
    total_opening = sum(e.get("opening_kills", 0) + e.get("opening_deaths", 0) for e in exports)
    total_ok = sum(e.get("opening_kills", 0) for e in exports)
    opening_rate = total_ok / max(total_opening, 1)
    avg_survival = round(sum(e.get("survival_rate", 0) for e in exports) / n, 1)
    total_awp = sum(e.get("awp_kills", 0) for e in exports)
    total_rifle = sum(e.get("rifle_kills", 0) for e in exports)
    awp_pct = total_awp / max(total_awp + total_rifle, 1) * 100
    avg_assists = sum(e.get("assists", 0) for e in exports) / n
    total_early_deaths = sum(e.get("deaths_early", 0) for e in exports)
    total_late_deaths = sum(e.get("deaths_late", 0) for e in exports)
    total_all_deaths = total_early_deaths + sum(e.get("deaths_mid", 0) for e in exports) + total_late_deaths
    early_death_pct = total_early_deaths / max(total_all_deaths, 1) * 100
    late_death_pct = total_late_deaths / max(total_all_deaths, 1) * 100
    avg_trade = sum(e.get("trade_kills", 0) for e in exports) / n

    role_scores = {
        "Entry Fragger": (opening_rate * 120) + (early_death_pct * 0.3) - (avg_survival * 0.2),
        "Support": (avg_util * 15) + (avg_assists * 5) + (avg_trade * 8) - (opening_rate * 20),
        "AWPer": (awp_pct * 1.5) + (sum(e.get("avg_fight_distance", 0) for e in exports) / n * 0.01),
        "Lurker": (late_death_pct * 0.8) + (avg_survival * 0.3) - (avg_trade * 5) - (opening_rate * 30),
        "Anchor": (avg_survival * 0.5) + (sum(e.get("ct_kills", 0) for e in exports) / max(sum(e.get("t_kills", 0) for e in exports), 1) * 15) - (early_death_pct * 0.2),
    }
    role_sorted = sorted(role_scores.items(), key=lambda x: -x[1])
    primary_role = role_sorted[0][0]
    secondary_role = role_sorted[1][0]

    role_descriptions = {
        "Entry Fragger": "Du suchst aktiv den ersten Kontakt und oeffnest Runden fuer dein Team.",
        "Support": "Du unterstuetzt mit Utility und Trade-Kills — das Rueckgrat des Teams.",
        "AWPer": "Du kontrollierst Lanes mit der AWP und hast hohe Kampfdistanzen.",
        "Lurker": "Du spielst isoliert, stirbst spaet und suchst Off-Angle Kills.",
        "Anchor": "Du haeltst Sites auf der CT-Seite und spielst passiv-defensiv.",
    }

    role_tips = {
        "Entry Fragger": "Trainiere Prefire-Angles und Flash-Entries. Dein Team verlaesst sich auf deinen ersten Kill.",
        "Support": "Lerne mehr Smoke/Flash Lineups. Gutes Support-Play gewinnt Runden ohne Highlight-Kills.",
        "AWPer": "Arbeite an Quick-Scopes und Repositioning nach dem ersten Schuss.",
        "Lurker": "Timing ist alles — lerne wann du rotieren und wann du halten musst.",
        "Anchor": "Trainiere Retake-Situationen und lerne Delay-Utility (Molotov, Smoke).",
    }

    role = {
        "primary": primary_role,
        "secondary": secondary_role,
        "description": role_descriptions[primary_role],
        "tip": role_tips[primary_role],
        "stats": {
            "opening_rate": round(opening_rate * 100, 1),
            "awp_pct": round(awp_pct, 1),
            "avg_survival": avg_survival,
            "avg_util": avg_util,
            "early_death_pct": round(early_death_pct, 1),
        },
    }

    # ── Training Plan ──
    training_exercises = []
    # Sort weaknesses by severity and build exercises
    weak_keys = [w["key"] for w in weaknesses]

    exercise_db = {
        "hs_pct": [
            {"name": "Aim Botz", "url": "steam://openurl/https://steamcommunity.com/sharedfiles/filedetails/?id=243702660", "duration": "10 Min", "desc": "Kopf-Level Tracking und Flick-Aim trainieren", "type": "Aim"},
            {"name": "Prefire Map (aktive Map)", "url": "", "duration": "5 Min", "desc": "Angle-Peeking mit Crosshair auf Kopfhoehe ueben", "type": "Aim"},
        ],
        "crosshair": [
            {"name": "Yprac Prefire", "url": "steam://openurl/https://steamcommunity.com/sharedfiles/filedetails/?id=3070253025", "duration": "10 Min", "desc": "Pre-Aim auf alle Standard-Angles deiner Map", "type": "Aim"},
            {"name": "Fast Aim / Reflex", "url": "steam://openurl/https://steamcommunity.com/sharedfiles/filedetails/?id=3075927937", "duration": "5 Min", "desc": "Reaktionszeit und Crosshair-Praezision", "type": "Aim"},
        ],
        "counter_strafe": [
            {"name": "Yprac Movement", "url": "steam://openurl/https://steamcommunity.com/sharedfiles/filedetails/?id=3070194772", "duration": "10 Min", "desc": "Counter-Strafe und A/D-Peeks ueben", "type": "Movement"},
            {"name": "Peek Practice", "url": "", "duration": "5 Min", "desc": "In einem Offline-Server Jiggle-Peeks mit Stops ueben", "type": "Movement"},
        ],
        "utility": [
            {"name": "Smoke Lineups ueben", "url": "", "duration": "10 Min", "desc": "Die 5 wichtigsten Smokes deiner Hauptmap lernen", "type": "Utility"},
            {"name": "Yprac Utility Practice", "url": "steam://openurl/https://steamcommunity.com/sharedfiles/filedetails/?id=3070253025", "duration": "5 Min", "desc": "Flash-, Smoke- und Molotov-Lineups trainieren", "type": "Utility"},
        ],
        "adr": [
            {"name": "Deathmatch (FFA)", "url": "", "duration": "10 Min", "desc": "Aggressiver spielen und mehr Kaempfe suchen", "type": "Aim"},
            {"name": "Trade-Kill Bewusstsein", "url": "", "duration": "5 Min", "desc": "In Matches bewusst auf Trade-Positionen achten", "type": "Gameplan"},
        ],
        "kast": [
            {"name": "Positioning Review", "url": "", "duration": "10 Min", "desc": "Letzte 3 Demos anschauen: Wo stirbst du ohne Impact?", "type": "Review"},
            {"name": "Crossfire-Setups", "url": "", "duration": "5 Min", "desc": "Mit Teammates Crossfire-Positionen auf Maps einueben", "type": "Gameplan"},
        ],
        "rating": [
            {"name": "Demo Review", "url": "", "duration": "15 Min", "desc": "Letzte Niederlage anschauen und 3 Fehler notieren", "type": "Review"},
            {"name": "Impact-Runden bewusst spielen", "url": "", "duration": "5 Min", "desc": "In jeder Runde fragen: Was ist mein Job hier?", "type": "Gameplan"},
        ],
    }

    # Primary: exercises for the two weakest areas
    for wk in weak_keys:
        for ex in exercise_db.get(wk, []):
            training_exercises.append(ex)

    # Add role-specific exercise
    role_exercises = {
        "Entry Fragger": {"name": "Prefire + Flash-Entry", "duration": "5 Min", "desc": "Flash werfen und sofort peeken ueben — Timing ist alles", "type": "Rolle"},
        "Support": {"name": "Smoke/Flash Combos", "duration": "5 Min", "desc": "2-3 neue Utility-Combos fuer deine Hauptmap lernen", "type": "Rolle"},
        "AWPer": {"name": "Quick-Scope Drill", "duration": "5 Min", "desc": "Aim Botz mit AWP — schnelles Scopen und Repositioning", "type": "Rolle"},
        "Lurker": {"name": "Timing-Uebung", "duration": "5 Min", "desc": "Offline-Server: Rotations-Timing von verschiedenen Positionen testen", "type": "Rolle"},
        "Anchor": {"name": "Retake Practice", "duration": "5 Min", "desc": "Retake-Smokes und Molotov-Lineups fuer deine Sites lernen", "type": "Rolle"},
    }
    training_exercises.append(role_exercises[primary_role])

    total_training_min = sum(int(ex["duration"].split()[0]) for ex in training_exercises)

    training = {
        "exercises": training_exercises,
        "total_minutes": total_training_min,
        "weak_areas": [w["label"] for w in weaknesses],
        "role_exercise": role_exercises[primary_role],
    }

    # ── Trend Alerts ──
    alerts = []
    if n >= 5:
        last5_avg = lambda key: sum(e.get(key, 0) for e in last5) / 5
        all_avg = lambda key: sum(e.get(key, 0) for e in exports) / n

        # HS% dropping
        hs_diff = last5_avg("hs_pct") - all_avg("hs_pct")
        if hs_diff < -5:
            alerts.append({"type": "warning", "icon": "trending-down",
                           "text": f"HS% faellt: {last5_avg('hs_pct'):.1f}% (letzte 5) vs. {all_avg('hs_pct'):.1f}% (gesamt)"})
        elif hs_diff > 5:
            alerts.append({"type": "success", "icon": "trending-up",
                           "text": f"HS% steigt: {last5_avg('hs_pct'):.1f}% (letzte 5) vs. {all_avg('hs_pct'):.1f}% (gesamt)"})

        # ADR trend
        adr_diff = last5_avg("adr") - all_avg("adr")
        if adr_diff < -8:
            alerts.append({"type": "warning", "icon": "trending-down",
                           "text": f"ADR faellt: {last5_avg('adr'):.1f} vs. {all_avg('adr'):.1f} Durchschnitt"})
        elif adr_diff > 8:
            alerts.append({"type": "success", "icon": "trending-up",
                           "text": f"ADR steigt: {last5_avg('adr'):.1f} vs. {all_avg('adr'):.1f} Durchschnitt"})

        # KAST dropping
        kast_diff = last5_avg("kast") - all_avg("kast")
        if kast_diff < -4:
            alerts.append({"type": "warning", "icon": "trending-down",
                           "text": f"KAST% sinkt: {last5_avg('kast'):.1f}% vs. {all_avg('kast'):.1f}% — weniger Impact"})

        # Rating trend
        rat_diff = last5_avg("rating") - all_avg("rating")
        if rat_diff > 0.08:
            alerts.append({"type": "success", "icon": "trending-up",
                           "text": f"Starke Form! Rating {last5_avg('rating'):.2f} vs. {all_avg('rating'):.2f} Durchschnitt"})
        elif rat_diff < -0.08:
            alerts.append({"type": "warning", "icon": "trending-down",
                           "text": f"Rating sinkt: {last5_avg('rating'):.2f} vs. {all_avg('rating'):.2f} Durchschnitt"})

        # Counter-Strafe trend
        cs_diff = last5_avg("counter_strafe") - all_avg("counter_strafe")
        if cs_diff > 5:
            alerts.append({"type": "success", "icon": "trending-up",
                           "text": f"Counter-Strafe verbessert: {last5_avg('counter_strafe'):.1f}% vs. {all_avg('counter_strafe'):.1f}%"})

        # Utility trend
        util_diff = last5_avg("utility_per_round") - all_avg("utility_per_round")
        if util_diff < -0.3:
            alerts.append({"type": "warning", "icon": "trending-down",
                           "text": f"Weniger Utility: {last5_avg('utility_per_round'):.1f}/Runde vs. {all_avg('utility_per_round'):.1f} Durchschnitt"})

        # Opening death rate in last 5
        last5_od = sum(e.get("opening_deaths", 0) for e in last5)
        last5_ok_val = sum(e.get("opening_kills", 0) for e in last5)
        if last5_od > 0 and last5_ok_val > 0 and last5_od / max(last5_ok_val, 1) > 1.5:
            alerts.append({"type": "warning", "icon": "alert-triangle",
                           "text": f"Opening Duels negativ: {last5_ok_val} First-Kills vs. {last5_od} First-Deaths (letzte 5)"})

        # Win streak / loss streak
        if streak >= 3 and streak_type == "Sieg":
            alerts.append({"type": "success", "icon": "flame",
                           "text": f"Hot Streak! {streak} Siege in Folge"})
        elif streak >= 3 and streak_type == "Niederlage":
            alerts.append({"type": "warning", "icon": "alert-triangle",
                           "text": f"{streak} Niederlagen in Folge — Zeit fuer eine Pause?"})

    return {
        "has_data": True,
        "total_matches": n,
        "wins": wins, "losses": losses, "draws": draws,
        "win_rate": win_rate,
        "avg_rating": avg_rating, "avg_kd": avg_kd, "avg_adr": avg_adr,
        "avg_kast": avg_kast, "avg_hs": avg_hs,
        "total_kills": total_kills, "total_deaths": total_deaths,
        "last5": last5,
        "last5_wins": last5_wins,
        "last5_rating": last5_rating,
        "form_trend": round(form_trend, 2),
        "spider": spider,
        "benchmarks": benchmarks,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "focus": focus,
        "best_map": best_map,
        "worst_map": worst_map,
        "streak": streak,
        "streak_type": streak_type,
        "role": role,
        "training": training,
        "alerts": alerts,
    }
