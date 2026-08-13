"""Flask-App für CS2 Coach Web-Interface."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.request
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

# Steam avatar cache: {steam_id: (url, timestamp)}
_avatar_cache: dict[str, tuple[str, float]] = {}
_AVATAR_CACHE_TTL = 3600  # 1 hour


def _fetch_steam_avatar(steam_id: str) -> str:
    """Fetch Steam avatar URL from community XML profile. Returns URL or empty string."""
    if not steam_id:
        return ""
    # Check cache
    if steam_id in _avatar_cache:
        url, ts = _avatar_cache[steam_id]
        if time.time() - ts < _AVATAR_CACHE_TTL:
            return url
    try:
        req = urllib.request.Request(
            f"https://steamcommunity.com/profiles/{steam_id}/?xml=1",
            headers={"User-Agent": "CS2-Coach/1.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            xml = resp.read().decode("utf-8", errors="replace")
        # Extract avatarFull URL
        m = re.search(r"<avatarFull><!\[CDATA\[(.*?)\]\]></avatarFull>", xml)
        if not m:
            m = re.search(r"<avatarFull>(.*?)</avatarFull>", xml)
        avatar_url = m.group(1) if m else ""
        _avatar_cache[steam_id] = (avatar_url, time.time())
        return avatar_url
    except Exception:
        return _avatar_cache.get(steam_id, ("", 0))[0]


def _post_discord(webhook_url: str, result, cfg: dict) -> None:
    """Post a match summary to Discord via webhook. Fire-and-forget."""
    if not webhook_url:
        return
    try:
        s = result.player_stats
        player = cfg.get("player_name", s.name)
        result_emoji = {"Sieg": ":green_circle:", "Niederlage": ":red_circle:"}.get(result.result_str, ":yellow_circle:")
        kd_color = 0x4ade80 if s.kd_ratio >= 1.2 else (0xfbbf24 if s.kd_ratio >= 0.9 else 0xf87171)
        rating_color = 0x4ade80 if result.rating >= 1.1 else (0xfbbf24 if result.rating >= 0.9 else 0xf87171)

        embed = {
            "title": f"{result_emoji} {result.result_str} — {result.map_name} {result.score_team1}:{result.score_team2}",
            "color": 0x4ade80 if result.result_str == "Sieg" else (0xf87171 if result.result_str == "Niederlage" else 0xfbbf24),
            "fields": [
                {"name": "K/D", "value": f"**{s.kills}/{s.deaths}** ({s.kd_ratio:.2f})", "inline": True},
                {"name": "Rating", "value": f"**{result.rating}**", "inline": True},
                {"name": "ADR", "value": f"**{s.adr:.0f}**", "inline": True},
                {"name": "HS%", "value": f"{s.hs_pct:.0f}%", "inline": True},
                {"name": "KAST", "value": f"{s.kast_pct:.0f}%", "inline": True},
                {"name": "Utility/R", "value": f"{s.utility_per_round:.1f}", "inline": True},
            ],
            "footer": {"text": f"{player} • CS2 Coach"},
            "timestamp": datetime.now().isoformat(),
        }

        # Add rank change if available
        rank = getattr(s, "rank", None)
        if rank and hasattr(rank, "change") and rank.change:
            sign = "+" if rank.change > 0 else ""
            embed["fields"].append({"name": "Rating Change", "value": f"{sign}{rank.change:.0f}", "inline": True})

        payload = json.dumps({"embeds": [embed]}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "CS2-Coach/1.0"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # Fire-and-forget


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

    def _resolve_steam_id() -> str:
        """Get SteamID64 from config or first export."""
        sid = cfg.get("steam_id", "")
        if sid:
            return sid
        vault = cfg.get("obsidian_vault_path", "")
        sub = cfg.get("coach_subfolder", "CS2-Coach")
        if not vault:
            return ""
        edir = Path(vault) / sub / "exports"
        if not edir.exists():
            return ""
        for f in sorted(edir.glob("*_coach.json"), reverse=True):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                sid = d.get("player", {}).get("steam_id", "")
                if sid:
                    return str(sid)
            except Exception:
                continue
        return ""

    @app.context_processor
    def inject_avatar():
        sid = _resolve_steam_id()
        return {"steam_avatar_url": _fetch_steam_avatar(sid) if sid else ""}

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

            # Discord webhook (file upload route)
            _post_discord(cfg.get("discord_webhook", ""), result, cfg)

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

            # Discord webhook (path-based route)
            _post_discord(cfg.get("discord_webhook", ""), result, cfg)

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
                    _post_discord(cfg.get("discord_webhook", ""), result, cfg)

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

    @app.route("/weapons")
    def weapons():
        export_list = _get_exports(cfg)
        weapon_data = _build_weapon_stats(cfg)
        return render_template("weapons.html", weapons=weapon_data, exports=export_list, config=cfg)

    @app.route("/goals")
    def goals():
        export_list = _get_exports(cfg)
        goals_data = _load_goals(cfg)
        progress = _compute_goal_progress(goals_data, export_list)
        records = _build_personal_records(export_list)
        habits = _build_habits(export_list)
        return render_template("goals.html", goals=progress, records=records,
                               habits=habits, exports=export_list, config=cfg)

    @app.route("/goals/add", methods=["POST"])
    def goals_add():
        goals_data = _load_goals(cfg)
        new_goal = {
            "metric": request.form.get("metric", ""),
            "target": float(request.form.get("target", 0)),
            "label": request.form.get("label", ""),
            "created": datetime.now().strftime("%Y-%m-%d"),
            "achieved": False,
        }
        if new_goal["metric"] and new_goal["target"]:
            goals_data.append(new_goal)
            _save_goals(cfg, goals_data)
            flash("Ziel hinzugefuegt!", "success")
        return redirect(url_for("goals"))

    @app.route("/goals/delete", methods=["POST"])
    def goals_delete():
        idx = int(request.form.get("index", -1))
        goals_data = _load_goals(cfg)
        if 0 <= idx < len(goals_data):
            goals_data.pop(idx)
            _save_goals(cfg, goals_data)
            flash("Ziel entfernt.", "success")
        return redirect(url_for("goals"))

    @app.route("/opponents")
    def opponents():
        opponent_data = _build_opponent_stats(cfg)
        return render_template("opponents.html", opponents=opponent_data, config=cfg)

    @app.route("/compare")
    def compare():
        export_list = _get_exports(cfg)
        periods = _build_period_comparison(export_list)
        achievements = _build_achievements(export_list, cfg)
        return render_template("compare.html", periods=periods,
                               achievements=achievements, exports=export_list, config=cfg)

    @app.route("/economy")
    def economy():
        export_list = _get_exports(cfg)
        eco_data = _build_economy_iq(export_list, cfg)
        return render_template("economy.html", eco=eco_data, config=cfg)

    @app.route("/digest")
    def digest():
        export_list = _get_exports(cfg)
        digest_data = _build_digest(export_list)
        return render_template("digest.html", digest=digest_data, config=cfg)

    @app.route("/digest/export", methods=["POST"])
    def digest_export():
        export_list = _get_exports(cfg)
        digest_data = _build_digest(export_list)
        period_key = request.form.get("period", "")
        period = None
        for p in digest_data.get("periods", []):
            if p["key"] == period_key:
                period = p
                break
        if not period:
            flash("Zeitraum nicht gefunden.", "error")
            return redirect(url_for("digest"))
        # Write Obsidian note
        vault_path = cfg.get("obsidian_vault_path", "")
        subfolder = cfg.get("coach_subfolder", "CS2-Coach")
        if not vault_path:
            flash("Vault-Pfad nicht konfiguriert.", "error")
            return redirect(url_for("digest"))
        out_dir = Path(vault_path) / subfolder
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"Digest_{period['key']}.md"
        filepath = out_dir / fname
        md = _render_digest_markdown(period)
        filepath.write_text(md, encoding="utf-8")
        flash(f"Digest exportiert: {fname}", "success")
        return redirect(url_for("digest"))

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

    @app.route("/watch")
    def watch():
        demo_folder = cfg.get("demo_folder", "")
        watch_data = _build_watch_status(cfg) if demo_folder else {"has_folder": False}
        return render_template("watch.html", watch=watch_data, config=cfg)

    @app.route("/api/watch-check")
    def watch_check():
        """Check for new demos in the configured folder."""
        demo_folder = cfg.get("demo_folder", "")
        if not demo_folder:
            return jsonify({"error": "Kein Demo-Ordner konfiguriert"}), 400
        folder_path = Path(demo_folder)
        if not folder_path.is_dir():
            return jsonify({"error": "Ordner nicht gefunden"}), 400

        analyzed_files = _get_analyzed_filenames(cfg)
        demos = sorted(folder_path.glob("*.dem"), key=lambda p: p.stat().st_mtime, reverse=True)
        new_demos = [d for d in demos if d.name not in analyzed_files]

        return jsonify({
            "total": len(demos),
            "analyzed": len(demos) - len(new_demos),
            "new": len(new_demos),
            "new_files": [{"name": d.name, "path": str(d), "size_mb": round(d.stat().st_size / (1024*1024), 1), "date": datetime.fromtimestamp(d.stat().st_mtime).strftime("%Y-%m-%d %H:%M")} for d in new_demos[:20]],
        })

    @app.route("/api/watch-analyze")
    def watch_analyze():
        """SSE endpoint: auto-analyze all new demos in the watch folder."""
        demo_folder = cfg.get("demo_folder", "")
        player_name = cfg.get("player_name", "")
        steam_id = _resolve_steam_id()

        folder_path = Path(demo_folder)
        if not folder_path.is_dir():
            def err():
                yield f"data: {json.dumps({'type': 'error', 'message': 'Ordner nicht gefunden'})}\n\n"
            return Response(err(), mimetype="text/event-stream")

        analyzed_files = _get_analyzed_filenames(cfg)
        demos = sorted(
            [d for d in folder_path.glob("*.dem") if d.name not in analyzed_files],
            key=lambda p: p.stat().st_mtime,
        )

        if not demos:
            def err():
                yield f"data: {json.dumps({'type': 'done', 'message': 'Keine neuen Demos'})}\n\n"
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
                    _post_discord(cfg.get("discord_webhook", ""), result, cfg)
                    s = result.player_stats
                    yield f"data: {json.dumps({'type': 'result', 'current': i+1, 'total': total, 'filename': demo.name, 'map': result.map_name, 'score': f'{result.score_team1}:{result.score_team2}', 'result_str': result.result_str, 'rating': result.rating, 'kills': s.kills, 'deaths': s.deaths})}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'error_item', 'current': i+1, 'total': total, 'filename': demo.name, 'message': str(e)})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        return Response(stream_with_context(generate()), mimetype="text/event-stream")

    @app.route("/achievements")
    def achievements():
        exports = _get_exports(cfg)
        ach_data = _build_achievements(exports, cfg)
        return render_template("achievements.html", ach=ach_data, config=cfg)

    @app.route("/maps")
    def maps():
        exports = _get_exports(cfg)
        map_stats = _get_map_stats(exports)
        veto = _build_map_veto(map_stats, exports)
        return render_template("maps.html", map_stats=map_stats, veto=veto, config=cfg)

    @app.route("/briefing")
    @app.route("/briefing/<map_name>")
    def briefing(map_name=None):
        exports = _get_exports(cfg)
        map_stats = _get_map_stats(exports)
        brief = _build_briefing(map_name, exports, map_stats)
        return render_template("briefing.html", brief=brief, map_stats=map_stats, config=cfg)

    @app.route("/warmup")
    def warmup():
        exports = _get_exports(cfg)
        map_stats = _get_map_stats(exports)
        wu_data = _build_warmup(exports, map_stats)
        return render_template("warmup.html", wu=wu_data, config=cfg)

    @app.route("/highlights")
    def highlights():
        hl_data = _build_highlights(cfg)
        return render_template("highlights.html", hl=hl_data, config=cfg)

    @app.route("/habits")
    def habits():
        exports = _get_exports(cfg)
        habit_data = _build_habit_tracker(exports)
        return render_template("habits.html", habits=habit_data, config=cfg)

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
            "demo_folder": request.form.get("demo_folder", "").strip(),
            "language": request.form.get("language", "de").strip(),
            "discord_webhook": request.form.get("discord_webhook", "").strip(),
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


def _build_opponent_stats(cfg: dict) -> list[dict]:
    """Aggregate duel statistics across all exports per opponent."""
    vault_path = cfg.get("obsidian_vault_path", "")
    subfolder = cfg.get("coach_subfolder", "CS2-Coach")
    if not vault_path:
        return []

    export_dir = Path(vault_path) / subfolder / "exports"
    if not export_dir.exists():
        return []

    from collections import defaultdict
    opp = defaultdict(lambda: {
        "kills": 0, "deaths": 0, "hs_kills": 0, "hs_deaths": 0,
        "matches": 0, "weapons_used": defaultdict(int), "maps": [],
        "results": [],
    })

    for f in sorted(export_dir.glob("*_coach.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        dm = data.get("duel_matrix", [])
        match_map = data.get("match", {}).get("map", "?")
        match_result = data.get("match", {}).get("result", "?")
        for d in dm:
            name = d.get("name", "?")
            opp[name]["kills"] += d.get("kills", 0)
            opp[name]["deaths"] += d.get("deaths", 0)
            opp[name]["hs_kills"] += d.get("hs_kills", 0)
            opp[name]["hs_deaths"] += d.get("hs_deaths", 0)
            opp[name]["matches"] += 1
            opp[name]["maps"].append(match_map)
            opp[name]["results"].append(match_result)
            for w in d.get("top_weapons", []):
                opp[name]["weapons_used"][w] += 1

    result = []
    for name, s in opp.items():
        total = s["kills"] + s["deaths"]
        if total < 3:
            continue
        kd = round(s["kills"] / max(s["deaths"], 1), 2)
        hs_pct = round(s["hs_kills"] / max(s["kills"], 1) * 100, 1)
        top_weapons = sorted(s["weapons_used"].items(), key=lambda x: -x[1])[:3]
        wins_vs = sum(1 for r in s["results"] if r == "Sieg")
        losses_vs = sum(1 for r in s["results"] if r == "Niederlage")

        # Classify threat level
        if s["deaths"] > s["kills"] + 2 and total >= 6:
            threat = "nemesis"
        elif s["kills"] > s["deaths"] + 2 and total >= 6:
            threat = "victim"
        else:
            threat = "even"

        result.append({
            "name": name, "kills": s["kills"], "deaths": s["deaths"],
            "kd": kd, "hs_pct": hs_pct, "total_duels": total,
            "matches": s["matches"], "threat": threat,
            "top_weapons": [w[0] for w in top_weapons],
            "maps": list(set(s["maps"])),
            "wins_vs": wins_vs, "losses_vs": losses_vs,
        })

    return sorted(result, key=lambda x: -x["total_duels"])


def _build_period_comparison(exports: list[dict]) -> list[dict]:
    """Group exports by month and compute per-period stats for comparison."""
    if not exports:
        return []

    by_month: dict[str, list[dict]] = {}
    for e in exports:
        month = e.get("date", "?")[:7]  # YYYY-MM
        if month and month != "?":
            by_month.setdefault(month, []).append(e)

    periods = []
    for month in sorted(by_month.keys(), reverse=True):
        matches = by_month[month]
        n = len(matches)
        wins = sum(1 for m in matches if m.get("result") == "Sieg")
        losses = sum(1 for m in matches if m.get("result") == "Niederlage")

        avg_rating = round(sum(m.get("rating", 0) for m in matches) / n, 2)
        avg_kd = round(sum(m.get("kd", 0) for m in matches) / n, 2)
        avg_adr = round(sum(m.get("adr", 0) for m in matches) / n, 1)
        avg_hs = round(sum(m.get("hs_pct", 0) for m in matches) / n, 1)
        avg_kast = round(sum(m.get("kast", 0) for m in matches) / n, 1)
        total_kills = sum(m.get("kills", 0) for m in matches)

        periods.append({
            "month": month, "matches": n, "wins": wins, "losses": losses,
            "draws": n - wins - losses,
            "win_rate": round(wins / n * 100, 1),
            "avg_rating": avg_rating, "avg_kd": avg_kd, "avg_adr": avg_adr,
            "avg_hs": avg_hs, "avg_kast": avg_kast, "total_kills": total_kills,
        })

    # Compute deltas vs previous month
    for i in range(len(periods) - 1):
        curr = periods[i]
        prev = periods[i + 1]
        curr["delta_rating"] = round(curr["avg_rating"] - prev["avg_rating"], 2)
        curr["delta_kd"] = round(curr["avg_kd"] - prev["avg_kd"], 2)
        curr["delta_adr"] = round(curr["avg_adr"] - prev["avg_adr"], 1)
        curr["delta_hs"] = round(curr["avg_hs"] - prev["avg_hs"], 1)
        curr["delta_wr"] = round(curr["win_rate"] - prev["win_rate"], 1)

    return periods


def _build_achievements(exports: list[dict], cfg: dict) -> list[dict]:
    """Check which achievements have been unlocked."""
    n = len(exports)
    if not n:
        return []

    total_kills = sum(e.get("kills", 0) for e in exports)
    wins = sum(1 for e in exports if e.get("result") == "Sieg")

    # Win streaks
    max_win_streak = 0
    cur = 0
    for e in reversed(exports):
        if e.get("result") == "Sieg":
            cur += 1
            max_win_streak = max(max_win_streak, cur)
        else:
            cur = 0

    # Per-match records
    max_kills = max(e.get("kills", 0) for e in exports)
    max_rating = max(e.get("rating", 0) for e in exports)
    max_hs = max(e.get("hs_pct", 0) for e in exports)
    max_adr = max(e.get("adr", 0) for e in exports)
    max_kd = max(e.get("kd", 0) for e in exports)

    # Multi-kill check from JSON exports
    has_ace = False
    has_4k = False
    max_clutches_in_match = 0
    vault_path = cfg.get("obsidian_vault_path", "")
    subfolder = cfg.get("coach_subfolder", "CS2-Coach")
    if vault_path:
        export_dir = Path(vault_path) / subfolder / "exports"
        if export_dir.exists():
            for f in export_dir.glob("*_coach.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    p = data.get("player", {})
                    mk = p.get("multikills", {})
                    if mk:
                        if mk.get("5k", 0) > 0:
                            has_ace = True
                        if mk.get("4k", 0) > 0:
                            has_4k = True
                    cl = p.get("clutches", {})
                    if cl:
                        max_clutches_in_match = max(max_clutches_in_match, cl.get("wins", 0))
                except (json.JSONDecodeError, OSError):
                    continue

    # KAST streaks
    kast_70_streak = 0
    kast_70_max = 0
    for e in reversed(exports):
        if e.get("kast", 0) >= 70:
            kast_70_streak += 1
            kast_70_max = max(kast_70_max, kast_70_streak)
        else:
            kast_70_streak = 0

    # Rating > 1.0 streak
    rating_1_streak = 0
    rating_1_max = 0
    for e in reversed(exports):
        if e.get("rating", 0) >= 1.0:
            rating_1_streak += 1
            rating_1_max = max(rating_1_max, rating_1_streak)
        else:
            rating_1_streak = 0

    # Define achievements
    achievements = [
        # Grind
        {"id": "first", "cat": "Grind", "name": "Erste Schritte", "desc": "Erstes Match analysiert", "icon": "play", "unlocked": n >= 1},
        {"id": "five", "cat": "Grind", "name": "Am Ball", "desc": "5 Matches analysiert", "icon": "repeat", "unlocked": n >= 5},
        {"id": "ten", "cat": "Grind", "name": "Dedicated", "desc": "10 Matches analysiert", "icon": "bar-chart-3", "unlocked": n >= 10},
        {"id": "twenty", "cat": "Grind", "name": "Grinder", "desc": "20 Matches analysiert", "icon": "flame", "unlocked": n >= 20},
        {"id": "fifty", "cat": "Grind", "name": "Veteran", "desc": "50 Matches analysiert", "icon": "award", "unlocked": n >= 50},
        {"id": "hundred", "cat": "Grind", "name": "Centurion", "desc": "100 Matches analysiert", "icon": "crown", "unlocked": n >= 100},
        # Aim
        {"id": "k200", "cat": "Aim", "name": "Killer", "desc": "200 Gesamt-Kills", "icon": "crosshair", "unlocked": total_kills >= 200},
        {"id": "k500", "cat": "Aim", "name": "Terminator", "desc": "500 Gesamt-Kills", "icon": "zap", "unlocked": total_kills >= 500},
        {"id": "k1000", "cat": "Aim", "name": "Tausend Tode", "desc": "1000 Gesamt-Kills", "icon": "skull", "unlocked": total_kills >= 1000},
        {"id": "bomb30", "cat": "Aim", "name": "30-Bombe", "desc": "30+ Kills in einem Match", "icon": "bomb", "unlocked": max_kills >= 30},
        {"id": "bomb40", "cat": "Aim", "name": "40-Bombe", "desc": "40+ Kills in einem Match", "icon": "rocket", "unlocked": max_kills >= 40},
        {"id": "hs_god", "cat": "Aim", "name": "Headshot-Maschine", "desc": "60%+ HS in einem Match", "icon": "target", "unlocked": max_hs >= 60},
        {"id": "ace", "cat": "Aim", "name": "Ace!", "desc": "5 Kills in einer Runde", "icon": "star", "unlocked": has_ace},
        {"id": "quad", "cat": "Aim", "name": "Quad Kill", "desc": "4 Kills in einer Runde", "icon": "sparkles", "unlocked": has_4k},
        # Performance
        {"id": "rat12", "cat": "Performance", "name": "Elite", "desc": "Rating 1.20+ in einem Match", "icon": "trending-up", "unlocked": max_rating >= 1.20},
        {"id": "rat13", "cat": "Performance", "name": "MVP", "desc": "Rating 1.30+ in einem Match", "icon": "medal", "unlocked": max_rating >= 1.30},
        {"id": "adr100", "cat": "Performance", "name": "Damage Dealer", "desc": "100+ ADR in einem Match", "icon": "flame", "unlocked": max_adr >= 100},
        {"id": "kd2", "cat": "Performance", "name": "Dominator", "desc": "K/D 2.0+ in einem Match", "icon": "shield", "unlocked": max_kd >= 2.0},
        # Consistency
        {"id": "kast5", "cat": "Consistency", "name": "Konstant", "desc": "5 Spiele in Folge KAST > 70%", "icon": "activity", "unlocked": kast_70_max >= 5},
        {"id": "kast10", "cat": "Consistency", "name": "Fels in der Brandung", "desc": "10 Spiele in Folge KAST > 70%", "icon": "mountain", "unlocked": kast_70_max >= 10},
        {"id": "rat5", "cat": "Consistency", "name": "Formtief? Nein!", "desc": "5 Spiele in Folge Rating > 1.0", "icon": "thumbs-up", "unlocked": rating_1_max >= 5},
        # Wins
        {"id": "win3", "cat": "Siege", "name": "Hot Streak", "desc": "3 Siege in Folge", "icon": "flame", "unlocked": max_win_streak >= 3},
        {"id": "win5", "cat": "Siege", "name": "Unaufhaltbar", "desc": "5 Siege in Folge", "icon": "zap", "unlocked": max_win_streak >= 5},
        {"id": "win10", "cat": "Siege", "name": "Unbesiegbar", "desc": "10 Siege in Folge", "icon": "crown", "unlocked": max_win_streak >= 10},
        {"id": "wins50", "cat": "Siege", "name": "Winner", "desc": "50 Siege gesamt", "icon": "trophy", "unlocked": wins >= 50},
        # Clutch
        {"id": "clutch3", "cat": "Clutch", "name": "Clutch-Koenig", "desc": "3+ Clutch-Wins in einem Match", "icon": "shield-check", "unlocked": max_clutches_in_match >= 3},
    ]

    return achievements


def _build_weapon_stats(cfg: dict) -> list[dict]:
    """Build per-weapon statistics from round_timeline kill/death events."""
    vault_path = cfg.get("obsidian_vault_path", "")
    subfolder = cfg.get("coach_subfolder", "CS2-Coach")
    if not vault_path:
        return []

    export_dir = Path(vault_path) / subfolder / "exports"
    if not export_dir.exists():
        return []

    from collections import defaultdict
    stats = defaultdict(lambda: {"kills": 0, "deaths": 0, "hs": 0, "matches": 0})
    match_weapons: dict[str, set] = defaultdict(set)

    for f in sorted(export_dir.glob("*_coach.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        tl = data.get("round_timeline", [])
        seen = set()
        for r in tl:
            for e in r.get("events", []):
                if e.get("type") == "kill":
                    w = e.get("weapon", "unknown")
                    stats[w]["kills"] += 1
                    stats[w]["hs"] += 1 if e.get("headshot") else 0
                    seen.add(w)
                elif e.get("type") == "death":
                    w = e.get("weapon", "unknown")
                    stats[w]["deaths"] += 1
        for w in seen:
            stats[w]["matches"] += 1

    # Weapon display names and categories
    weapon_meta = {
        "ak47": ("AK-47", "Rifle"), "m4a1_silencer": ("M4A1-S", "Rifle"),
        "m4a1": ("M4A4", "Rifle"), "galilar": ("Galil AR", "Rifle"),
        "famas": ("FAMAS", "Rifle"), "aug": ("AUG", "Rifle"), "sg556": ("SG 553", "Rifle"),
        "awp": ("AWP", "Sniper"), "ssg08": ("Scout", "Sniper"),
        "deagle": ("Desert Eagle", "Pistol"), "usp_silencer": ("USP-S", "Pistol"),
        "glock": ("Glock-18", "Pistol"), "p250": ("P250", "Pistol"),
        "hkp2000": ("P2000", "Pistol"), "elite": ("Dual Berettas", "Pistol"),
        "tec9": ("Tec-9", "Pistol"), "cz75a": ("CZ75-Auto", "Pistol"),
        "fiveseven": ("Five-SeveN", "Pistol"), "revolver": ("R8 Revolver", "Pistol"),
        "mac10": ("MAC-10", "SMG"), "mp5sd": ("MP5-SD", "SMG"),
        "mp7": ("MP7", "SMG"), "mp9": ("MP9", "SMG"), "ump45": ("UMP-45", "SMG"),
        "p90": ("P90", "SMG"), "bizon": ("PP-Bizon", "SMG"),
        "mag7": ("MAG-7", "Heavy"), "nova": ("Nova", "Heavy"),
        "xm1014": ("XM1014", "Heavy"), "sawedoff": ("Sawed-Off", "Heavy"),
        "negev": ("Negev", "Heavy"), "m249": ("M249", "Heavy"),
        "hegrenade": ("HE Grenade", "Utility"), "inferno": ("Molotov/Inc.", "Utility"),
        "molotov": ("Molotov", "Utility"),
        "knife": ("Knife", "Melee"), "knife_t": ("Knife", "Melee"),
    }

    result = []
    for weapon, s in stats.items():
        if weapon in ("world",):
            continue
        name, cat = weapon_meta.get(weapon, (weapon, "Other"))
        kills = s["kills"]
        deaths = s["deaths"]
        kd = round(kills / max(deaths, 1), 2)
        hs_pct = round(s["hs"] / max(kills, 1) * 100, 1)

        # Recommendation
        rec = None
        if cat == "Rifle" and kills >= 10:
            if hs_pct < 35:
                rec = "HS% niedrig — Crosshair auf Kopfhoehe halten"
            elif hs_pct > 55:
                rec = "Starke HS% — gutes Crosshair Placement"
        elif cat == "Sniper" and kills >= 5:
            if kd >= 2.0:
                rec = "Starke AWP-Performance — dominant"
            elif kd < 1.0:
                rec = "K/D unter 1.0 — ueberleg ob Rifle besser passt"
        elif cat == "Pistol" and kills >= 5:
            if hs_pct > 50:
                rec = "Gute Pistol-Praezision"
            elif hs_pct < 30:
                rec = "Pistol-HS% niedrig — uebe Headshots mit USP/Glock"

        result.append({
            "weapon": weapon, "name": name, "category": cat,
            "kills": kills, "deaths": deaths, "kd": kd,
            "hs": s["hs"], "hs_pct": hs_pct, "matches": s["matches"],
            "rec": rec,
        })

    return sorted(result, key=lambda x: -x["kills"])


# ── Goals & Progress ──

GOALS_FILE = "cs2_coach_goals.json"


def _goals_path(cfg: dict) -> Path:
    vault_path = cfg.get("obsidian_vault_path", "")
    subfolder = cfg.get("coach_subfolder", "CS2-Coach")
    if vault_path:
        return Path(vault_path) / subfolder / GOALS_FILE
    return Path(GOALS_FILE)


def _load_goals(cfg: dict) -> list[dict]:
    p = _goals_path(cfg)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_goals(cfg: dict, goals: list[dict]):
    p = _goals_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(goals, indent=2, ensure_ascii=False), encoding="utf-8")


GOAL_METRICS = {
    "adr": {"label": "ADR", "desc": "Average Damage per Round", "key": "adr", "fmt": ".1f", "higher_better": True},
    "hs_pct": {"label": "HS%", "desc": "Headshot-Prozent", "key": "hs_pct", "fmt": ".1f", "higher_better": True},
    "kast": {"label": "KAST%", "desc": "Kill/Assist/Survived/Traded", "key": "kast", "fmt": ".1f", "higher_better": True},
    "rating": {"label": "Rating", "desc": "Gesamtbewertung", "key": "rating", "fmt": ".2f", "higher_better": True},
    "kd": {"label": "K/D", "desc": "Kill/Death-Ratio", "key": "kd", "fmt": ".2f", "higher_better": True},
    "counter_strafe": {"label": "Counter-Strafe%", "desc": "Anteil stehender Schuesse", "key": "counter_strafe", "fmt": ".1f", "higher_better": True},
    "crosshair": {"label": "Crosshair Placement", "desc": "Grad-Abweichung (niedriger = besser)", "key": "crosshair_placement", "fmt": ".1f", "higher_better": False},
    "win_rate": {"label": "Win-Rate%", "desc": "Gewinnrate ueber letzte 10", "key": "_win_rate", "fmt": ".1f", "higher_better": True},
}


def _compute_goal_progress(goals: list[dict], exports: list[dict]) -> list[dict]:
    """Add current value and progress percentage to each goal."""
    if not exports:
        return [dict(g, current=0, pct=0, achieved=False, trend=[]) for g in goals]

    last10 = exports[:10]
    n = len(last10)

    result = []
    for g in goals:
        metric = g.get("metric", "")
        meta = GOAL_METRICS.get(metric, {})
        key = meta.get("key", metric)
        higher_better = meta.get("higher_better", True)
        target = g.get("target", 0)

        if key == "_win_rate":
            wins = sum(1 for e in last10 if e.get("result") == "Sieg")
            current = round(wins / n * 100, 1)
        else:
            current = round(sum(e.get(key, 0) for e in last10) / n, 2)

        # Progress
        if higher_better:
            pct = min(100, max(0, current / max(target, 0.01) * 100))
            achieved = current >= target
        else:
            pct = min(100, max(0, target / max(current, 0.01) * 100))
            achieved = current <= target

        # Trend (last 10 values)
        if key == "_win_rate":
            trend = []
            for i, e in enumerate(reversed(last10)):
                w = sum(1 for x in last10[max(0, len(last10)-i-5):len(last10)-i] if x.get("result") == "Sieg")
                trend.append(round(w / min(5, i + 1) * 100, 1))
        else:
            trend = [e.get(key, 0) for e in reversed(last10)]

        result.append(dict(g, current=current, pct=round(pct, 1), achieved=achieved,
                           trend=trend, meta=meta))

    return result


def _build_personal_records(exports: list[dict]) -> dict:
    """Find personal bests across all exports."""
    if not exports:
        return {}

    best_rating = max(exports, key=lambda e: e.get("rating", 0))
    best_adr = max(exports, key=lambda e: e.get("adr", 0))
    best_kills = max(exports, key=lambda e: e.get("kills", 0))
    best_kd = max(exports, key=lambda e: e.get("kd", 0))
    best_hs = max(exports, key=lambda e: e.get("hs_pct", 0))
    best_kast = max(exports, key=lambda e: e.get("kast", 0))

    # Longest win streak
    max_streak = 0
    cur = 0
    for e in reversed(exports):
        if e.get("result") == "Sieg":
            cur += 1
            max_streak = max(max_streak, cur)
        else:
            cur = 0

    return {
        "rating": {"value": best_rating.get("rating", 0), "map": best_rating.get("map", "?"), "date": best_rating.get("date", "?")},
        "adr": {"value": best_adr.get("adr", 0), "map": best_adr.get("map", "?"), "date": best_adr.get("date", "?")},
        "kills": {"value": best_kills.get("kills", 0), "map": best_kills.get("map", "?"), "date": best_kills.get("date", "?")},
        "kd": {"value": best_kd.get("kd", 0), "map": best_kd.get("map", "?"), "date": best_kd.get("date", "?")},
        "hs_pct": {"value": best_hs.get("hs_pct", 0), "map": best_hs.get("map", "?"), "date": best_hs.get("date", "?")},
        "kast": {"value": best_kast.get("kast", 0), "map": best_kast.get("map", "?"), "date": best_kast.get("date", "?")},
        "win_streak": max_streak,
        "total_kills": sum(e.get("kills", 0) for e in exports),
        "total_matches": len(exports),
    }


def _build_habits(exports: list[dict]) -> list[dict]:
    """Track key habits over the last 20 matches with trend lines."""
    if len(exports) < 3:
        return []

    last20 = list(reversed(exports[:20]))  # oldest first for chart
    n = len(last20)

    habits = []
    habit_defs = [
        ("hs_pct", "Headshot%", "Anteil der Kills durch Kopftreffer", "%", True),
        ("adr", "ADR", "Durchschnittlicher Schaden pro Runde", "", True),
        ("kast", "KAST%", "Runden mit positivem Beitrag", "%", True),
        ("counter_strafe", "Counter-Strafe", "Anteil stehender Schuesse", "%", True),
        ("crosshair_placement", "Crosshair Placement", "Grad-Abweichung zum Gegner", "°", False),
        ("utility_per_round", "Utility/Runde", "Granaten pro Runde", "", True),
    ]

    for key, label, desc, unit, higher_better in habit_defs:
        values = [e.get(key, 0) for e in last20]
        if not any(v > 0 for v in values):
            continue

        avg_all = round(sum(values) / n, 2)
        avg_first = round(sum(values[:n//2]) / max(n//2, 1), 2)
        avg_second = round(sum(values[n//2:]) / max(n - n//2, 1), 2)

        if higher_better:
            diff = avg_second - avg_first
            improving = diff > 0
        else:
            diff = avg_first - avg_second
            improving = diff > 0

        habits.append({
            "key": key, "label": label, "desc": desc, "unit": unit,
            "data_points": values, "avg": avg_all,
            "avg_first": avg_first, "avg_second": avg_second,
            "diff": round(diff, 2), "improving": improving,
            "higher_better": higher_better,
            "labels": [f"{e.get('map', '?')} {e.get('date', '')[-5:]}" for e in last20],
        })

    return habits


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


def _get_analyzed_filenames(cfg: dict) -> set[str]:
    """Get set of demo filenames that have already been analyzed."""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    analyzed = set()
    if export_dir and export_dir.exists():
        for ef in export_dir.glob("*_coach.json"):
            try:
                data = json.loads(ef.read_text(encoding="utf-8"))
                demo_file = data.get("match", {}).get("demo_file", "")
                if demo_file:
                    analyzed.add(demo_file)
            except Exception:
                pass
    return analyzed


def _build_watch_status(cfg: dict) -> dict:
    """Build status info for the folder-watch page."""
    demo_folder = cfg.get("demo_folder", "")
    if not demo_folder:
        return {"has_folder": False}

    folder_path = Path(demo_folder)
    if not folder_path.is_dir():
        return {"has_folder": True, "folder_exists": False, "path": demo_folder}

    demos = sorted(folder_path.glob("*.dem"), key=lambda p: p.stat().st_mtime, reverse=True)
    analyzed_files = _get_analyzed_filenames(cfg)
    new_demos = [d for d in demos if d.name not in analyzed_files]

    recent = []
    for d in demos[:10]:
        recent.append({
            "name": d.name,
            "path": str(d),
            "size_mb": round(d.stat().st_size / (1024 * 1024), 1),
            "date": datetime.fromtimestamp(d.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "analyzed": d.name in analyzed_files,
        })

    return {
        "has_folder": True,
        "folder_exists": True,
        "path": demo_folder,
        "total": len(demos),
        "analyzed": len(demos) - len(new_demos),
        "new": len(new_demos),
        "recent": recent,
    }


def _build_digest(exports: list[dict]) -> dict:
    """Build weekly and monthly digest summaries from export data."""
    if not exports:
        return {"has_data": False, "periods": []}

    from collections import defaultdict

    # Parse dates and group by week and month
    weekly = defaultdict(list)
    monthly = defaultdict(list)
    for e in exports:
        date_str = e.get("date", "")
        if not date_str or date_str == "?":
            continue
        try:
            dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        # ISO week key
        iso = dt.isocalendar()
        wk = f"{iso[0]}-W{iso[1]:02d}"
        weekly[wk].append(e)
        # Month key
        mk = dt.strftime("%Y-%m")
        monthly[mk].append(e)

    def _summarize(matches: list[dict], label: str, key: str) -> dict:
        n = len(matches)
        wins = sum(1 for m in matches if m.get("result") == "Sieg")
        losses = sum(1 for m in matches if m.get("result") == "Niederlage")
        draws = n - wins - losses
        avg_rating = round(sum(m.get("rating", 0) for m in matches) / max(n, 1), 2)
        avg_kd = round(sum(m.get("kd", 0) for m in matches) / max(n, 1), 2)
        avg_adr = round(sum(m.get("adr", 0) for m in matches) / max(n, 1), 1)
        avg_hs = round(sum(m.get("hs_pct", 0) for m in matches) / max(n, 1), 1)
        avg_kast = round(sum(m.get("kast", 0) for m in matches) / max(n, 1), 1)
        total_kills = sum(m.get("kills", 0) for m in matches)
        total_deaths = sum(m.get("deaths", 0) for m in matches)
        win_rate = round(wins / max(n, 1) * 100, 1)

        # Maps played
        from collections import Counter
        map_counts = Counter(m.get("map", "?") for m in matches)
        maps = [{"name": mp, "count": c} for mp, c in map_counts.most_common()]

        # Best and worst match
        best = max(matches, key=lambda m: m.get("rating", 0))
        worst = min(matches, key=lambda m: m.get("rating", 0))

        # Trend: compare first half vs second half
        sorted_m = sorted(matches, key=lambda m: m.get("date", ""))
        half = max(len(sorted_m) // 2, 1)
        first_half = sorted_m[:half]
        second_half = sorted_m[half:]
        trend_rating = round(
            sum(m.get("rating", 0) for m in second_half) / max(len(second_half), 1)
            - sum(m.get("rating", 0) for m in first_half) / max(len(first_half), 1), 2
        )

        return {
            "label": label,
            "key": key,
            "matches": n,
            "wins": wins, "losses": losses, "draws": draws,
            "win_rate": win_rate,
            "avg_rating": avg_rating, "avg_kd": avg_kd, "avg_adr": avg_adr,
            "avg_hs": avg_hs, "avg_kast": avg_kast,
            "total_kills": total_kills, "total_deaths": total_deaths,
            "maps": maps,
            "best": {"map": best.get("map"), "rating": best.get("rating"), "score": best.get("score"), "date": best.get("date")},
            "worst": {"map": worst.get("map"), "rating": worst.get("rating"), "score": worst.get("score"), "date": worst.get("date")},
            "trend_rating": trend_rating,
            "match_list": sorted_m,
        }

    month_names = {
        "01": "Januar", "02": "Februar", "03": "Maerz", "04": "April",
        "05": "Mai", "06": "Juni", "07": "Juli", "08": "August",
        "09": "September", "10": "Oktober", "11": "November", "12": "Dezember",
    }

    periods = []
    # Monthly summaries (most recent first)
    for mk in sorted(monthly.keys(), reverse=True):
        mm = mk[-2:]
        label = f"{month_names.get(mm, mm)} {mk[:4]}"
        periods.append(_summarize(monthly[mk], label, mk))

    # Weekly summaries (most recent first)
    weekly_periods = []
    for wk in sorted(weekly.keys(), reverse=True)[:8]:  # last 8 weeks max
        label = f"KW {wk.split('-W')[1]} / {wk.split('-W')[0]}"
        weekly_periods.append(_summarize(weekly[wk], label, wk))

    return {
        "has_data": True,
        "periods": periods,
        "weekly": weekly_periods,
    }


def _render_digest_markdown(period: dict) -> str:
    """Render a digest period as Obsidian Markdown."""
    p = period
    lines = [
        f"# Digest: {p['label']}",
        "",
        f"**Matches:** {p['matches']} ({p['wins']}W / {p['losses']}L / {p['draws']}D)",
        f"**Win-Rate:** {p['win_rate']}%",
        "",
        "## Durchschnittswerte",
        "",
        f"| Metrik | Wert |",
        f"|--------|------|",
        f"| Rating | {p['avg_rating']} |",
        f"| K/D | {p['avg_kd']} |",
        f"| ADR | {p['avg_adr']} |",
        f"| HS% | {p['avg_hs']}% |",
        f"| KAST% | {p['avg_kast']}% |",
        f"| Kills | {p['total_kills']} |",
        f"| Deaths | {p['total_deaths']} |",
        "",
        "## Maps",
        "",
    ]
    for m in p.get("maps", []):
        lines.append(f"- **{m['name']}**: {m['count']}x gespielt")
    lines += [
        "",
        "## Highlights",
        "",
        f"- **Bestes Match:** {p['best']['map']} ({p['best']['score']}) — Rating {p['best']['rating']}",
        f"- **Schlechtestes Match:** {p['worst']['map']} ({p['worst']['score']}) — Rating {p['worst']['rating']}",
        f"- **Trend:** Rating {'gestiegen' if p['trend_rating'] > 0 else 'gesunken'} ({'+' if p['trend_rating'] > 0 else ''}{p['trend_rating']})",
        "",
        "---",
        f"*Generiert am {datetime.now().strftime('%Y-%m-%d %H:%M')} von CS2 Coach*",
    ]
    return "\n".join(lines)


def _build_economy_iq(exports: list[dict], cfg: dict) -> dict:
    """Aggregate economy data across all exports for the Economy-IQ page."""
    if not exports:
        return {"has_data": False}

    vault_path = cfg.get("obsidian_vault_path", "")
    subfolder = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / subfolder / "exports" if vault_path else None

    cats = {"pistol": [], "eco": [], "force": [], "fullbuy": []}
    cat_meta = {
        "pistol": {"label": "Pistol", "icon": "hand", "desc": "Pistolrunden (Runde 1 & 13)", "color": "#fbbf24"},
        "eco": {"label": "Eco", "icon": "piggy-bank", "desc": "Sparrunden (<$1500 Ausruestung)", "color": "#f87171"},
        "force": {"label": "Force-Buy", "icon": "zap", "desc": "Forcekauf ($1500–$3700)", "color": "#fb923c"},
        "fullbuy": {"label": "Full-Buy", "icon": "shield", "desc": "Vollkauf (>$3700)", "color": "#4ade80"},
    }
    per_match = []  # per-match economy breakdown for trend

    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    for f in sorted(export_dir.glob("*_coach.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        eco = data.get("economy", {})
        if not eco:
            continue
        match_info = data.get("match", {})
        match_entry = {
            "date": match_info.get("date", "?"),
            "map": match_info.get("map", "?"),
            "result": match_info.get("result", "?"),
        }
        for cat_key in cats:
            if cat_key in eco:
                cats[cat_key].append(eco[cat_key])
                match_entry[cat_key] = eco[cat_key]
        per_match.append(match_entry)

    # Aggregate per category
    summary = {}
    for cat_key, entries in cats.items():
        if not entries:
            continue
        total_rounds = sum(e.get("rounds", 0) for e in entries)
        total_kills = sum(e.get("kills", 0) for e in entries)
        total_deaths = sum(e.get("deaths", 0) for e in entries)
        total_won = sum(e.get("rounds_won", 0) for e in entries)
        total_damage = sum(e.get("adr", 0) * e.get("rounds", 1) for e in entries)
        n_matches = len(entries)
        summary[cat_key] = {
            **cat_meta.get(cat_key, {}),
            "total_rounds": total_rounds,
            "total_kills": total_kills,
            "total_deaths": total_deaths,
            "kd": round(total_kills / max(total_deaths, 1), 2),
            "adr": round(total_damage / max(total_rounds, 1), 1),
            "win_rate": round(total_won / max(total_rounds, 1) * 100, 1),
            "rounds_won": total_won,
            "rounds_lost": total_rounds - total_won,
            "matches": n_matches,
        }

    # Best/worst category
    best_cat = max(summary, key=lambda c: summary[c]["win_rate"]) if summary else None
    worst_cat = min(summary, key=lambda c: summary[c]["win_rate"]) if summary else None

    # Per-match trend data for chart (last 20)
    trend = per_match[-20:]

    # Recommendations
    tips = []
    if summary.get("eco") and summary["eco"]["win_rate"] >= 30:
        tips.append({"type": "positive", "text": f"Starke Eco-Runden: {summary['eco']['win_rate']}% Win-Rate — du holst viel aus wenig Equipment raus."})
    elif summary.get("eco") and summary["eco"]["win_rate"] < 15:
        tips.append({"type": "warning", "text": f"Eco-Runden: Nur {summary['eco']['win_rate']}% Win-Rate. Spar lieber konsequent und investiere in Force-Buys."})
    if summary.get("force") and summary["force"]["win_rate"] >= 50:
        tips.append({"type": "positive", "text": f"Force-Buys zahlen sich aus: {summary['force']['win_rate']}% Win-Rate — gute Kaufentscheidungen."})
    elif summary.get("force") and summary["force"]["win_rate"] < 30:
        tips.append({"type": "warning", "text": f"Force-Buys: Nur {summary['force']['win_rate']}% Win-Rate. Ueberlege, ob Full-Save nicht effektiver waere."})
    if summary.get("fullbuy") and summary["fullbuy"]["win_rate"] < 50:
        tips.append({"type": "warning", "text": f"Full-Buy Win-Rate nur {summary['fullbuy']['win_rate']}% — moegliche Ursachen: Utility-Einsatz, Positionierung oder Teamplay."})
    if summary.get("pistol") and summary["pistol"]["win_rate"] >= 60:
        tips.append({"type": "positive", "text": f"Pistolrunden-Spezialist: {summary['pistol']['win_rate']}% Win-Rate — das verschafft dir oekonomische Vorteile."})

    # Overall eco efficiency score (weighted win rate)
    total_rounds_all = sum(s["total_rounds"] for s in summary.values())
    eco_iq = round(sum(s["win_rate"] * s["total_rounds"] for s in summary.values()) / max(total_rounds_all, 1), 1) if summary else 0

    return {
        "has_data": True,
        "summary": summary,
        "best_cat": best_cat,
        "worst_cat": worst_cat,
        "trend": trend,
        "tips": tips,
        "eco_iq": eco_iq,
        "total_rounds": total_rounds_all,
        "total_matches": len(per_match),
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


def _build_achievements(exports: list[dict], cfg: dict) -> dict:
    """Build achievement data from all exports (reads full JSONs for multikills)."""
    if not exports:
        return {"unlocked": [], "locked": [], "total": 0, "earned": 0, "points": 0, "max_points": 0}

    # Read full export JSONs for multikill / round_timeline data
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    full_exports = []
    if export_dir and export_dir.exists():
        for f in sorted(export_dir.glob("*_coach.json")):
            try:
                full_exports.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass

    # Aggregate stats
    n = len(exports)
    total_kills = sum(e["kills"] for e in exports)
    total_deaths = sum(e["deaths"] for e in exports)
    total_assists = sum(e.get("assists", 0) for e in exports)
    total_clutch_wins = sum(e.get("clutch_wins", 0) for e in exports)
    wins = sum(1 for e in exports if e["result"] == "Sieg")
    max_kills = max((e["kills"] for e in exports), default=0)
    max_rating = max((e["rating"] for e in exports), default=0)
    max_adr = max((e["adr"] for e in exports), default=0)
    max_hs = max((e["hs_pct"] for e in exports), default=0)
    maps_played = set(e["map"] for e in exports)

    # Multikills from full exports
    total_3k = sum(d.get("player", {}).get("multikills", {}).get("3k", 0) for d in full_exports)
    total_4k = sum(d.get("player", {}).get("multikills", {}).get("4k", 0) for d in full_exports)
    total_5k = sum(d.get("player", {}).get("multikills", {}).get("5k", 0) for d in full_exports)
    total_2k = sum(d.get("player", {}).get("multikills", {}).get("2k", 0) for d in full_exports)

    # Win/loss streaks
    best_win_streak = 0
    cur = 0
    for e in sorted(exports, key=lambda x: x["date"]):
        if e["result"] == "Sieg":
            cur += 1
            best_win_streak = max(best_win_streak, cur)
        else:
            cur = 0

    # KAST streaks
    kast_70_streak = 0
    best_kast_70 = 0
    for e in sorted(exports, key=lambda x: x["date"]):
        if e.get("kast", 0) >= 70:
            kast_70_streak += 1
            best_kast_70 = max(best_kast_70, kast_70_streak)
        else:
            kast_70_streak = 0

    # Rating above 1.0 streak
    rating_streak = 0
    best_rating_streak = 0
    for e in sorted(exports, key=lambda x: x["date"]):
        if e.get("rating", 0) >= 1.0:
            rating_streak += 1
            best_rating_streak = max(best_rating_streak, rating_streak)
        else:
            rating_streak = 0

    # Has a 13-0 or 0-13?
    has_shutout_win = any(
        d.get("match", {}).get("score_own", 0) >= 13 and d.get("match", {}).get("score_enemy", 0) == 0
        for d in full_exports
    )

    # Flawless match (0 deaths)
    has_flawless = any(e["deaths"] == 0 for e in exports)

    # 30-bomb, 40-bomb
    has_30_bomb = max_kills >= 30
    has_40_bomb = max_kills >= 40

    # Session days (unique dates)
    unique_dates = set(e["date"][:10] for e in exports)
    days_played = len(unique_dates)

    # Define all achievements
    categories = {
        "Erste Schritte": "rocket",
        "Aim": "crosshair",
        "Consistency": "shield-check",
        "Clutch & Impact": "zap",
        "Grind": "flame",
        "Spezial": "sparkles",
    }

    all_achievements = [
        # Erste Schritte
        {"id": "first_match", "name": "Erste Analyse", "desc": "Analysiere dein erstes Match", "cat": "Erste Schritte", "tier": "bronze", "pts": 10, "check": n >= 1},
        {"id": "five_matches", "name": "Warm gelaufen", "desc": "5 Matches analysiert", "cat": "Erste Schritte", "tier": "bronze", "pts": 10, "check": n >= 5},
        {"id": "ten_matches", "name": "Datensammler", "desc": "10 Matches analysiert", "cat": "Erste Schritte", "tier": "silber", "pts": 20, "check": n >= 10},
        {"id": "twenty_five", "name": "Statistik-Nerd", "desc": "25 Matches analysiert", "cat": "Erste Schritte", "tier": "gold", "pts": 30, "check": n >= 25},
        {"id": "fifty_matches", "name": "Datenkoenig", "desc": "50 Matches analysiert", "cat": "Erste Schritte", "tier": "platin", "pts": 50, "check": n >= 50},
        {"id": "hundred_matches", "name": "Analyst", "desc": "100 Matches analysiert", "cat": "Erste Schritte", "tier": "diamant", "pts": 100, "check": n >= 100},
        {"id": "first_win", "name": "Erster Sieg", "desc": "Gewinne dein erstes Match", "cat": "Erste Schritte", "tier": "bronze", "pts": 10, "check": wins >= 1},

        # Aim
        {"id": "thirty_bomb", "name": "30-Bombe", "desc": "30+ Kills in einem Match", "cat": "Aim", "tier": "silber", "pts": 30, "check": has_30_bomb},
        {"id": "forty_bomb", "name": "40-Bombe", "desc": "40+ Kills in einem Match", "cat": "Aim", "tier": "diamant", "pts": 80, "check": has_40_bomb},
        {"id": "hs_machine", "name": "Headshot-Maschine", "desc": "60%+ HS in einem Match", "cat": "Aim", "tier": "gold", "pts": 40, "check": max_hs >= 60},
        {"id": "triple_kill", "name": "Triple Kill", "desc": "Erste 3K-Runde", "cat": "Aim", "tier": "bronze", "pts": 10, "check": total_3k >= 1},
        {"id": "triple_master", "name": "Triple-Meister", "desc": "10 3K-Runden insgesamt", "cat": "Aim", "tier": "silber", "pts": 25, "check": total_3k >= 10},
        {"id": "quad_kill", "name": "Quad Kill", "desc": "Erste 4K-Runde", "cat": "Aim", "tier": "silber", "pts": 25, "check": total_4k >= 1},
        {"id": "quad_master", "name": "Quad-Meister", "desc": "5 4K-Runden insgesamt", "cat": "Aim", "tier": "gold", "pts": 50, "check": total_4k >= 5},
        {"id": "ace", "name": "ACE!", "desc": "Alle 5 Gegner in einer Runde eliminiert", "cat": "Aim", "tier": "platin", "pts": 75, "check": total_5k >= 1},
        {"id": "kill_century", "name": "100 Kills", "desc": "Insgesamt 100 Kills", "cat": "Aim", "tier": "bronze", "pts": 10, "check": total_kills >= 100},
        {"id": "kill_500", "name": "500 Kills", "desc": "Insgesamt 500 Kills", "cat": "Aim", "tier": "silber", "pts": 25, "check": total_kills >= 500},
        {"id": "kill_1000", "name": "1000 Kills", "desc": "Insgesamt 1000 Kills", "cat": "Aim", "tier": "gold", "pts": 50, "check": total_kills >= 1000},

        # Consistency
        {"id": "kast_70_5", "name": "Konsistent", "desc": "5 Spiele in Folge KAST > 70%", "cat": "Consistency", "tier": "silber", "pts": 30, "check": best_kast_70 >= 5},
        {"id": "kast_70_10", "name": "Fels in der Brandung", "desc": "10 Spiele in Folge KAST > 70%", "cat": "Consistency", "tier": "gold", "pts": 50, "check": best_kast_70 >= 10},
        {"id": "rating_1_5", "name": "Ueberdurchschnittlich", "desc": "5 Spiele in Folge Rating >= 1.0", "cat": "Consistency", "tier": "silber", "pts": 30, "check": best_rating_streak >= 5},
        {"id": "rating_1_10", "name": "Leistungstraeger", "desc": "10 Spiele in Folge Rating >= 1.0", "cat": "Consistency", "tier": "gold", "pts": 50, "check": best_rating_streak >= 10},
        {"id": "rating_high", "name": "MVP", "desc": "Rating 1.30+ in einem Match", "cat": "Consistency", "tier": "gold", "pts": 40, "check": max_rating >= 1.30},
        {"id": "adr_100", "name": "Damage Dealer", "desc": "ADR 100+ in einem Match", "cat": "Consistency", "tier": "silber", "pts": 25, "check": max_adr >= 100},

        # Clutch & Impact
        {"id": "first_clutch", "name": "Clutch-Spieler", "desc": "Gewinne deinen ersten Clutch", "cat": "Clutch & Impact", "tier": "bronze", "pts": 15, "check": total_clutch_wins >= 1},
        {"id": "clutch_5", "name": "Nervenstark", "desc": "5 Clutches insgesamt gewonnen", "cat": "Clutch & Impact", "tier": "silber", "pts": 30, "check": total_clutch_wins >= 5},
        {"id": "clutch_10", "name": "Clutch-Koenig", "desc": "10 Clutches insgesamt gewonnen", "cat": "Clutch & Impact", "tier": "gold", "pts": 50, "check": total_clutch_wins >= 10},
        {"id": "clutch_25", "name": "Clutch-Legende", "desc": "25 Clutches insgesamt gewonnen", "cat": "Clutch & Impact", "tier": "platin", "pts": 75, "check": total_clutch_wins >= 25},
        {"id": "multikill_50", "name": "Multi-Kill-Maschine", "desc": "50 Multi-Kill-Runden (2K+)", "cat": "Clutch & Impact", "tier": "gold", "pts": 40, "check": (total_2k + total_3k + total_4k + total_5k) >= 50},
        {"id": "shutout", "name": "Shutout", "desc": "Gewinne 13:0", "cat": "Clutch & Impact", "tier": "platin", "pts": 60, "check": has_shutout_win},

        # Grind
        {"id": "win_streak_3", "name": "Auf dem Weg", "desc": "3 Siege in Folge", "cat": "Grind", "tier": "bronze", "pts": 15, "check": best_win_streak >= 3},
        {"id": "win_streak_5", "name": "Heisse Phase", "desc": "5 Siege in Folge", "cat": "Grind", "tier": "silber", "pts": 30, "check": best_win_streak >= 5},
        {"id": "win_streak_10", "name": "Unaufhaltbar", "desc": "10 Siege in Folge", "cat": "Grind", "tier": "platin", "pts": 60, "check": best_win_streak >= 10},
        {"id": "five_maps", "name": "Kartograph", "desc": "5 verschiedene Maps gespielt", "cat": "Grind", "tier": "silber", "pts": 20, "check": len(maps_played) >= 5},
        {"id": "seven_maps", "name": "Globetrotter", "desc": "7 verschiedene Maps gespielt", "cat": "Grind", "tier": "gold", "pts": 35, "check": len(maps_played) >= 7},
        {"id": "days_7", "name": "Wochenspieler", "desc": "An 7 verschiedenen Tagen gespielt", "cat": "Grind", "tier": "silber", "pts": 20, "check": days_played >= 7},
        {"id": "days_30", "name": "Monatsspieler", "desc": "An 30 verschiedenen Tagen gespielt", "cat": "Grind", "tier": "gold", "pts": 40, "check": days_played >= 30},

        # Spezial
        {"id": "flawless", "name": "Unverwundbar", "desc": "0 Tode in einem Match", "cat": "Spezial", "tier": "diamant", "pts": 100, "check": has_flawless},
        {"id": "assist_king", "name": "Team-Player", "desc": "100 Assists insgesamt", "cat": "Spezial", "tier": "silber", "pts": 25, "check": total_assists >= 100},
    ]

    # Tier styling
    tier_colors = {
        "bronze": {"bg": "rgba(205,127,50,0.12)", "border": "rgba(205,127,50,0.3)", "color": "#cd7f32"},
        "silber": {"bg": "rgba(192,192,192,0.12)", "border": "rgba(192,192,192,0.3)", "color": "#c0c0c0"},
        "gold": {"bg": "rgba(255,215,0,0.12)", "border": "rgba(255,215,0,0.3)", "color": "#ffd700"},
        "platin": {"bg": "rgba(110,200,230,0.12)", "border": "rgba(110,200,230,0.3)", "color": "#6ec8e6"},
        "diamant": {"bg": "rgba(185,142,255,0.12)", "border": "rgba(185,142,255,0.3)", "color": "#b98eff"},
    }

    unlocked = []
    locked = []
    for a in all_achievements:
        a["tier_style"] = tier_colors.get(a["tier"], tier_colors["bronze"])
        if a["check"]:
            unlocked.append(a)
        else:
            locked.append(a)

    total_pts = sum(a["pts"] for a in all_achievements)
    earned_pts = sum(a["pts"] for a in unlocked)

    return {
        "unlocked": unlocked,
        "locked": locked,
        "total": len(all_achievements),
        "earned": len(unlocked),
        "points": earned_pts,
        "max_points": total_pts,
        "categories": categories,
        "tier_colors": tier_colors,
        # Stats for progress display
        "stats": {
            "matches": n,
            "kills": total_kills,
            "wins": wins,
            "clutch_wins": total_clutch_wins,
            "best_streak": best_win_streak,
            "maps": len(maps_played),
            "days": days_played,
            "multikills_3k": total_3k,
            "multikills_4k": total_4k,
            "multikills_5k": total_5k,
        },
    }


def _build_habit_tracker(exports: list[dict]) -> dict:
    """Build habit tracking data from exports — trends of key behavioral metrics."""
    if len(exports) < 2:
        return {"has_data": False, "habits": [], "data_points": []}

    # Sort chronologically (exports come newest-first)
    chronological = sorted(exports, key=lambda x: x["date"])

    # Define habits to track
    habit_defs = [
        {
            "id": "hs_pct", "name": "Headshot %", "key": "hs_pct", "unit": "%",
            "good_direction": "up", "target": 50,
            "tip_low": "Crosshair hoeher halten. Prefire-Maps spielen.",
            "tip_high": "Starke Headshots — halte das Niveau.",
        },
        {
            "id": "kast", "name": "KAST %", "key": "kast", "unit": "%",
            "good_direction": "up", "target": 70,
            "tip_low": "Mehr Trades, Assists und Survival-Runden. Nicht sinnlos sterben.",
            "tip_high": "Du bist konstant involviert — weiter so.",
        },
        {
            "id": "utility", "name": "Utility/Runde", "key": "utility_per_round", "unit": "",
            "good_direction": "up", "target": 2.0,
            "tip_low": "Kaufe Nades und wirf sie! 2-3 Standard-Lineups pro Map lernen.",
            "tip_high": "Gute Utility-Nutzung.",
        },
        {
            "id": "counter_strafe", "name": "Counter-Strafe", "key": "counter_strafe", "unit": "%",
            "good_direction": "up", "target": 85,
            "tip_low": "Uebe Counter-Strafing auf YPRAC Movement Maps.",
            "tip_high": "Dein Counter-Strafing ist stark.",
        },
        {
            "id": "crosshair", "name": "Crosshair Placement", "key": "crosshair_placement", "unit": "°",
            "good_direction": "down", "target": 8,
            "tip_low": "Crosshair auf Kopfhoehe der Angles halten. Prefire-Maps ueben.",
            "tip_high": "Exzellentes Crosshair Placement.",
        },
        {
            "id": "survival", "name": "Survival Rate", "key": "survival_rate", "unit": "%",
            "good_direction": "up", "target": 40,
            "tip_low": "Weniger unnoetige Peeks. Lerne wann du dich zurueckziehen solltest.",
            "tip_high": "Du ueberlebst haeufig — gutes Positionsspiel.",
        },
        {
            "id": "opening_death_rate", "name": "Opening Death Rate", "key": "_opening_death_rate", "unit": "%",
            "good_direction": "down", "target": 15,
            "tip_low": "Zu oft erster Tod. Weniger aggressive Dry-Peeks, mehr Utility vorher.",
            "tip_high": "Du stirbst selten als Erster — gutes Timing.",
        },
        {
            "id": "adr", "name": "ADR", "key": "adr", "unit": "",
            "good_direction": "up", "target": 80,
            "tip_low": "Mehr Fights nehmen, mehr Impact-Damage pro Runde.",
            "tip_high": "Starker Damage-Output.",
        },
    ]

    habits_out = []
    for hd in habit_defs:
        values = []
        labels = []
        for e in chronological:
            if hd["key"] == "_opening_death_rate":
                total_rounds = e.get("deaths", 1) + (e.get("kills", 0) // 2)  # rough approx
                opening_deaths = e.get("opening_deaths", 0) or e.get("deaths_early", 0)
                total_rounds = max(e.get("kills", 0) + e.get("deaths", 0), 1)
                val = round(opening_deaths / max(total_rounds, 1) * 100, 1) if total_rounds else 0
            else:
                val = e.get(hd["key"], 0) or 0
            values.append(round(val, 1))
            labels.append(e["date"][:10])

        if not values:
            continue

        current = values[-1]
        avg_all = round(sum(values) / len(values), 1)
        avg_last5 = round(sum(values[-5:]) / len(values[-5:]), 1) if len(values) >= 5 else avg_all
        avg_first5 = round(sum(values[:5]) / len(values[:5]), 1) if len(values) >= 5 else avg_all

        # Trend: compare last 5 to first 5
        if len(values) >= 5:
            trend_val = round(avg_last5 - avg_first5, 1)
        else:
            trend_val = 0

        # Is this improving?
        if hd["good_direction"] == "up":
            improving = trend_val > 0.5
            worsening = trend_val < -0.5
            at_target = current >= hd["target"]
        else:
            improving = trend_val < -0.5
            worsening = trend_val > 0.5
            at_target = current <= hd["target"]

        tip = hd["tip_high"] if at_target else hd["tip_low"]

        habits_out.append({
            "id": hd["id"],
            "name": hd["name"],
            "unit": hd["unit"],
            "current": current,
            "avg": avg_all,
            "avg_last5": avg_last5,
            "target": hd["target"],
            "at_target": at_target,
            "trend": trend_val,
            "improving": improving,
            "worsening": worsening,
            "good_direction": hd["good_direction"],
            "tip": tip,
            "data_points": values,
            "labels": labels,
        })

    return {
        "has_data": True,
        "habits": habits_out,
        "match_count": len(chronological),
    }


def _build_highlights(cfg: dict) -> dict:
    """Build round highlights across all matches from full export JSONs."""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False, "matches": [], "totals": {}}

    all_matches = []
    totals = {"hero": 0, "impact": 0, "invisible": 0, "survivor": 0, "entry": 0, "eco_hero": 0, "total_rounds": 0}

    for f in sorted(export_dir.glob("*_coach.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        match = data.get("match", {})
        player = data.get("player", {})
        timeline = data.get("round_timeline", [])
        economy = data.get("economy", {})

        if not timeline:
            continue

        # Determine eco/pistol round numbers from economy data
        eco_rounds = set()
        pistol_rounds = {1, match.get("total_rounds", 24) // 2 + 1} if match.get("total_rounds") else {1, 13}

        classified = []
        for rd in timeline:
            rnum = rd.get("round", 0)
            kills = rd.get("player_kills", 0)
            died = rd.get("player_died", False)
            won = rd.get("won", False)
            early = rd.get("died_early", False)
            events = rd.get("events", [])
            side = rd.get("side", "?")

            kill_events = [e for e in events if e.get("type") == "kill"]
            death_events = [e for e in events if e.get("type") == "death"]

            # First kill in round? (check if first event is a kill by player)
            is_entry = len(kill_events) > 0 and events and events[0].get("type") == "kill"

            # Classify
            tags = []
            if kills >= 3 and won:
                tags.append("hero")
                totals["hero"] += 1
            if kills >= 1 and is_entry and won:
                tags.append("impact")
                totals["impact"] += 1
            if kills == 0 and died and not won:
                tags.append("invisible")
                totals["invisible"] += 1
            if not died and won:
                tags.append("survivor")
                totals["survivor"] += 1
            if is_entry:
                tags.append("entry")
                totals["entry"] += 1
            if kills >= 1 and rnum in pistol_rounds:
                tags.append("eco_hero")
                totals["eco_hero"] += 1

            totals["total_rounds"] += 1

            # Weapon info from kill events
            weapons = [k.get("weapon", "?") for k in kill_events]
            headshots = sum(1 for k in kill_events if k.get("headshot", False))

            classified.append({
                "round": rnum,
                "side": side,
                "won": won,
                "kills": kills,
                "died": died,
                "early": early,
                "tags": tags,
                "weapons": weapons,
                "headshots": headshots,
                "events": events,
            })

        # Match-level highlights
        hero_rounds = [r for r in classified if "hero" in r["tags"]]
        impact_rounds = [r for r in classified if "impact" in r["tags"]]
        invisible_rounds = [r for r in classified if "invisible" in r["tags"]]

        all_matches.append({
            "date": match.get("date", "?"),
            "map": match.get("map", "?"),
            "score": f"{match.get('score_own', '?')}:{match.get('score_enemy', '?')}",
            "result": match.get("result", "?"),
            "rating": player.get("rating", 0),
            "kills": player.get("kills", 0),
            "deaths": player.get("deaths", 0),
            "rounds": classified,
            "hero_count": len(hero_rounds),
            "impact_count": len(impact_rounds),
            "invisible_count": len(invisible_rounds),
            "filename": f.name,
        })

    return {
        "has_data": bool(all_matches),
        "matches": all_matches[:20],  # last 20 matches
        "totals": totals,
    }


def _build_map_veto(map_stats: list[dict], exports: list[dict]) -> dict:
    """Build map veto recommendations from map stats."""
    if not map_stats:
        return {"has_data": False, "picks": [], "bans": [], "maps": []}

    # Enrich with side splits and trends
    enriched = []
    for ms in map_stats:
        map_name = ms["map"]
        map_exports = [e for e in exports if e.get("map") == map_name]

        # CT/T side performance
        ct_kills = sum(e.get("ct_kills", 0) for e in map_exports)
        ct_deaths = sum(e.get("ct_deaths", 0) for e in map_exports)
        t_kills = sum(e.get("t_kills", 0) for e in map_exports)
        t_deaths = sum(e.get("t_deaths", 0) for e in map_exports)
        ct_kd = round(ct_kills / max(ct_deaths, 1), 2)
        t_kd = round(t_kills / max(t_deaths, 1), 2)

        # Recent form (last 5 on this map)
        recent = map_exports[:5]
        recent_wins = sum(1 for e in recent if e.get("result") == "Sieg")
        recent_rating = round(sum(e.get("rating", 0) for e in recent) / max(len(recent), 1), 2)

        # Compute a composite score for ranking
        # Weight: 40% win_rate, 30% rating, 15% recent form, 15% K/D
        composite = (
            ms["win_rate"] * 0.4
            + ms["avg_rating"] * 30 * 0.3
            + (recent_wins / max(len(recent), 1) * 100) * 0.15
            + min(ms["avg_kd"], 2.0) * 50 * 0.15
        )

        # Recommendation text
        if ms["win_rate"] >= 55 and ms["avg_rating"] >= 1.0:
            rec = "Starke Map — Pick empfohlen"
            rec_type = "pick"
        elif ms["win_rate"] >= 50 and ms["avg_rating"] >= 0.9:
            rec = "Solide Map — spielbar"
            rec_type = "neutral"
        elif ms["win_rate"] < 40 or ms["avg_rating"] < 0.8:
            rec = "Schwache Map — Ban empfohlen"
            rec_type = "ban"
        else:
            rec = "Durchschnittlich — nur bei Bedarf"
            rec_type = "neutral"

        stronger_side = "CT" if ct_kd > t_kd else ("T" if t_kd > ct_kd else "Ausgeglichen")

        enriched.append({
            **ms,
            "ct_kd": ct_kd,
            "t_kd": t_kd,
            "ct_kills": ct_kills,
            "ct_deaths": ct_deaths,
            "t_kills": t_kills,
            "t_deaths": t_deaths,
            "stronger_side": stronger_side,
            "recent_wins": recent_wins,
            "recent_total": len(recent),
            "recent_rating": recent_rating,
            "composite": composite,
            "recommendation": rec,
            "rec_type": rec_type,
        })

    # Sort by composite score
    ranked = sorted(enriched, key=lambda x: -x["composite"])

    # Top picks and bans
    picks = [m for m in ranked if m["rec_type"] == "pick"]
    bans = [m for m in ranked if m["rec_type"] == "ban"]

    return {
        "has_data": True,
        "maps": ranked,
        "picks": picks,
        "bans": bans,
    }


def _build_warmup(exports: list[dict], map_stats: list[dict]) -> dict:
    """Build personalized warm-up protocol based on recent weaknesses."""
    if len(exports) < 3:
        return {"has_data": False, "exercises": [], "focus": "", "duration": 0}

    last5 = exports[:5]
    avg = lambda key: round(sum(e.get(key, 0) for e in last5) / len(last5), 1)

    avg_hs = avg("hs_pct")
    avg_cs = avg("counter_strafe")
    avg_xh = avg("crosshair_placement")
    avg_util = avg("utility_per_round")
    avg_adr = avg("adr")
    avg_kast = avg("kast")
    avg_survival = avg("survival_rate")

    # Identify weaknesses and build exercises
    exercises = []

    # Aim / HS%
    if avg_hs < 45:
        severity = "hoch" if avg_hs < 35 else "mittel"
        exercises.append({
            "id": "aim", "name": "Aim Training", "duration": 5,
            "icon": "crosshair",
            "severity": severity,
            "metric": f"HS% = {avg_hs}%",
            "target": "Ziel: > 45%",
            "steps": [
                "Aim Botz: 100 Kills nur Kopf (AK + M4)",
                "Fokus auf One-Taps, nicht Spray",
                "Langsam anfangen, dann Speed steigern",
            ],
            "workshop": [
                {"name": "Aim Botz", "desc": "Klassisches Aim-Training mit Bots"},
                {"name": "Fast Aim / Reflex Training", "desc": "Schnelle Zielerfassung"},
            ],
        })

    # Counter-Strafe
    if avg_cs < 82:
        severity = "hoch" if avg_cs < 70 else "mittel"
        exercises.append({
            "id": "counterstafe", "name": "Counter-Strafe", "duration": 5,
            "icon": "move",
            "severity": severity,
            "metric": f"Counter-Strafe = {avg_cs}%",
            "target": "Ziel: > 85%",
            "steps": [
                "YPRAC Movement Map: Strafe-Shoot Drills",
                "A-D-Shoot Rhythmus verinnerlichen",
                "Bewusst vor jedem Shot stoppen",
            ],
            "workshop": [
                {"name": "YPRAC Movement", "desc": "Counter-Strafe & Movement Drills"},
                {"name": "Strafe Training", "desc": "A-D-Shoot Mechanik ueben"},
            ],
        })

    # Crosshair Placement
    if avg_xh > 10:
        severity = "hoch" if avg_xh > 18 else "mittel"
        exercises.append({
            "id": "crosshair", "name": "Crosshair Placement", "duration": 5,
            "icon": "target",
            "severity": severity,
            "metric": f"Crosshair = {avg_xh}°",
            "target": "Ziel: < 8°",
            "steps": [
                "Prefire-Map deiner haeufigsten Map laden",
                "Auf Kopfhoehe durch die Map laufen",
                "Jeden Winkel bewusst pre-aimen",
            ],
            "workshop": [
                {"name": "Prefire Maps", "desc": "Prefire-Practice fuer jede Map"},
                {"name": "YPRAC Prefire", "desc": "Gezielte Prefire-Uebungen"},
            ],
        })

    # Utility
    if avg_util < 1.5:
        severity = "hoch" if avg_util < 1.0 else "mittel"
        exercises.append({
            "id": "utility", "name": "Utility Training", "duration": 5,
            "icon": "flame",
            "severity": severity,
            "metric": f"Utility = {avg_util}/Runde",
            "target": "Ziel: > 2.0/Runde",
            "steps": [
                "3 Smoke-Lineups fuer deine beste Map lernen",
                "2 Flash-Spots fuer Entry/Retake",
                "1 Molotov-Lineup pro Map-Seite",
            ],
            "workshop": [
                {"name": "Smoke/Flash Practice", "desc": "Lineup-Training"},
                {"name": "YPRAC Utility", "desc": "Utility-Uebungen pro Map"},
            ],
        })

    # Spray Control (if ADR is low, spray might be an issue)
    if avg_adr < 75:
        exercises.append({
            "id": "spray", "name": "Spray Control", "duration": 5,
            "icon": "zap",
            "severity": "mittel",
            "metric": f"ADR = {avg_adr}",
            "target": "Ziel: > 80 ADR",
            "steps": [
                "Recoil Master: AK-47 Spray 50x",
                "Recoil Master: M4A4/M4A1-S Spray 50x",
                "Dann: Spray-Transfer auf 2 Ziele",
            ],
            "workshop": [
                {"name": "Recoil Master", "desc": "Spray-Pattern Training"},
            ],
        })

    # If survival is very low, add positioning exercise
    if avg_survival < 30:
        exercises.append({
            "id": "positioning", "name": "Positioning Review", "duration": 3,
            "icon": "shield",
            "severity": "mittel",
            "metric": f"Survival = {avg_survival}%",
            "target": "Ziel: > 35%",
            "steps": [
                "Letzte 2 Demos kurz reviewen: Wo bist du gestorben?",
                "Alternative Positionen finden",
                "Regel: Immer einen Rueckzugsweg haben",
            ],
            "workshop": [],
        })

    # Always add a quick deathmatch warm-up
    exercises.append({
        "id": "dm", "name": "Deathmatch", "duration": 5,
        "icon": "swords",
        "severity": "standard",
        "metric": "Aufwaermen",
        "target": "5 Minuten FFA DM",
        "steps": [
            "FFA Deathmatch mit AK/M4",
            "Nur Kopf zielen, nicht sprayen",
            "Fokus: Crosshair auf Kopfhoehe halten",
        ],
        "workshop": [],
    })

    total_duration = sum(ex["duration"] for ex in exercises)

    # Determine focus area
    weaknesses = []
    if avg_hs < 45:
        weaknesses.append(("Aim", avg_hs, "HS%"))
    if avg_cs < 82:
        weaknesses.append(("Counter-Strafe", avg_cs, "%"))
    if avg_xh > 10:
        weaknesses.append(("Crosshair Placement", avg_xh, "°"))
    if avg_util < 1.5:
        weaknesses.append(("Utility", avg_util, "/R"))

    focus = weaknesses[0][0] if weaknesses else "Allgemeines Aufwaermen"

    # Next map suggestion (most played recently)
    from collections import Counter
    recent_maps = Counter(e["map"] for e in last5)
    likely_map = recent_maps.most_common(1)[0][0] if recent_maps else ""

    # Map-specific tip
    map_tip = ""
    for ms in map_stats:
        if ms["map"] == likely_map:
            if ms["win_rate"] < 45:
                map_tip = f"Deine Win-Rate auf {likely_map} ist nur {ms['win_rate']}% — ueberleg ob du veto'st."
            elif ms["avg_rating"] < 0.85:
                map_tip = f"Dein Rating auf {likely_map} ist {ms['avg_rating']} — Prefire-Map fuer {likely_map} ueben."
            else:
                map_tip = f"{likely_map} ist eine deiner staerkeren Maps ({ms['win_rate']}% WR, {ms['avg_rating']} Rating)."
            break

    return {
        "has_data": True,
        "exercises": exercises,
        "focus": focus,
        "duration": total_duration,
        "weaknesses": weaknesses,
        "likely_map": likely_map,
        "map_tip": map_tip,
        "stats": {
            "hs": avg_hs, "cs": avg_cs, "xh": avg_xh,
            "util": avg_util, "adr": avg_adr, "kast": avg_kast,
            "survival": avg_survival,
        },
    }


def _build_briefing(map_name: str | None, exports: list[dict], map_stats: list[dict]) -> dict:
    """Build pre-match briefing for a specific map."""
    available_maps = sorted(set(e["map"] for e in exports))
    if not available_maps:
        return {"has_data": False, "map": None, "maps": []}

    if not map_name:
        map_name = available_maps[0]

    map_exports = [e for e in exports if e["map"] == map_name]
    if not map_exports:
        return {"has_data": False, "map": map_name, "maps": available_maps}

    # Find map stats
    ms = None
    for s in map_stats:
        if s["map"] == map_name:
            ms = s
            break

    if not ms:
        return {"has_data": False, "map": map_name, "maps": available_maps}

    # Recent matches on this map
    recent = map_exports[:5]
    n = len(recent)

    avg = lambda key: round(sum(e.get(key, 0) for e in recent) / n, 1)

    avg_rating = avg("rating")
    avg_kd = avg("kd")
    avg_adr = avg("adr")
    avg_hs = avg("hs_pct")
    avg_util = avg("utility_per_round")
    avg_xh = avg("crosshair_placement")
    avg_survival = avg("survival_rate")

    # Side performance
    ct_kills = sum(e.get("ct_kills", 0) for e in map_exports)
    ct_deaths = sum(e.get("ct_deaths", 0) for e in map_exports)
    t_kills = sum(e.get("t_kills", 0) for e in map_exports)
    t_deaths = sum(e.get("t_deaths", 0) for e in map_exports)
    ct_kd = round(ct_kills / max(ct_deaths, 1), 2)
    t_kd = round(t_kills / max(t_deaths, 1), 2)
    weaker_side = "CT" if ct_kd < t_kd else "T"

    # Opening deaths
    opening_deaths = sum(e.get("opening_deaths", 0) or e.get("deaths_early", 0) for e in recent)
    opening_kills = sum(e.get("opening_kills", 0) for e in recent)
    total_rounds = sum(1 for _ in recent)  # approximate

    # Build reminders
    reminders = []

    # Win rate reminder
    if ms["win_rate"] < 45:
        reminders.append({"type": "warning", "text": f"Schwache Win-Rate ({ms['win_rate']}%). Spiel diszipliniert, keine Hero-Plays."})
    elif ms["win_rate"] >= 55:
        reminders.append({"type": "positive", "text": f"Starke Map fuer dich ({ms['win_rate']}% WR). Selbstbewusst spielen."})

    # Side weakness
    if ct_kd < 0.8:
        reminders.append({"type": "warning", "text": f"CT-Side schwach (K/D {ct_kd}). Passiver spielen, Retakes ueben."})
    if t_kd < 0.8:
        reminders.append({"type": "warning", "text": f"T-Side schwach (K/D {t_kd}). Mehr Utility vor dem Entry nutzen."})

    # Opening deaths
    if opening_deaths > opening_kills and opening_deaths > 0:
        reminders.append({"type": "warning", "text": f"Zu viele Opening Deaths. Nicht als Erster dry-peeken."})

    # Utility
    if avg_util < 1.5:
        reminders.append({"type": "tip", "text": f"Utility nur {avg_util}/Runde. Kauf Nades und wirf sie ALLE."})

    # Crosshair
    if avg_xh > 12:
        reminders.append({"type": "tip", "text": f"Crosshair Placement {avg_xh}° — halte das Fadenkreuz auf Kopfhoehe."})

    # Survival
    if avg_survival < 30:
        reminders.append({"type": "tip", "text": f"Survival nur {avg_survival}%. Nicht overpeeken, Rueckzugsweg planen."})

    # HS%
    if avg_hs < 35:
        reminders.append({"type": "tip", "text": f"HS% nur {avg_hs}% auf {map_name}. Bewusst auf den Kopf zielen."})

    # Recent form
    recent_wins = sum(1 for e in recent if e["result"] == "Sieg")
    recent_losses = sum(1 for e in recent if e["result"] == "Niederlage")
    if recent_losses >= 3:
        reminders.append({"type": "warning", "text": f"Letzte {n}: {recent_wins}W/{recent_losses}L — schwache Form. Fokus auf Basics."})
    elif recent_wins >= 3:
        reminders.append({"type": "positive", "text": f"Letzte {n}: {recent_wins}W/{recent_losses}L — gute Form! Bleib dran."})

    # Always add a positive/neutral one
    if avg_rating >= 1.0:
        reminders.append({"type": "positive", "text": f"Dein Rating auf {map_name} ist {avg_rating} — du performst gut hier."})

    # Recent match results for display
    recent_results = []
    for e in recent:
        recent_results.append({
            "date": e["date"][:10],
            "score": e.get("score", "?"),
            "result": e["result"],
            "rating": e["rating"],
            "kills": e["kills"],
            "deaths": e["deaths"],
        })

    return {
        "has_data": True,
        "map": map_name,
        "maps": available_maps,
        "stats": ms,
        "ct_kd": ct_kd,
        "t_kd": t_kd,
        "weaker_side": weaker_side,
        "reminders": reminders,
        "recent": recent_results,
        "avg": {
            "rating": avg_rating, "kd": avg_kd, "adr": avg_adr,
            "hs": avg_hs, "util": avg_util, "xh": avg_xh,
            "survival": avg_survival,
        },
    }
