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
        return render_template("export_detail.html", data=data, filename=filename, config=cfg)

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
            exports.append({
                "filename": f.name,
                "date": match.get("date", match.get("datetime", "?")),
                "map": match.get("map", "?"),
                "score": f"{match.get('score_own', '?')}:{match.get('score_enemy', '?')}",
                "result": match.get("result", "?"),
                "kd": player.get("kd", 0),
                "adr": player.get("adr", 0),
                "rating": player.get("rating", 0),
                "kast": player.get("kast_pct", 0),
                "hs_pct": player.get("hs_pct", 0),
                "kills": player.get("kills", 0),
                "deaths": player.get("deaths", 0),
                "crosshair_placement": player.get("crosshair_placement", {}).get("avg_degrees", 0),
                "counter_strafe": player.get("counter_strafe_score", 0),
                "utility_per_round": player.get("utility_per_round", 0),
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
    }
