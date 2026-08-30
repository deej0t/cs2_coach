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
from ..ai_chat import (
    build_player_context, stream_gemini, stream_ollama, check_ollama_status,
    call_gemini, call_ollama, build_practice_context, PRACTICE_PLAN_SYSTEM,
)
from ..practice import (
    build_practice_data, generate_practice_cfg, generate_retake_cfg,
    generate_spray_cfg, generate_challenge_cfg, generate_utility_cfg,
    generate_server_cfg, generate_warmup_cfg, _load_positions,
)
from .i18n import make_translator

CONFIG_PATH = Path(os.environ.get("CS2COACH_CONFIG", "")) if os.environ.get("CS2COACH_CONFIG") else Path(__file__).parent.parent.parent / "config.yaml"
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


_inventory_cache: dict = {}  # steam_id -> (data, timestamp)
_INVENTORY_CACHE_TTL = 600  # 10 min


def _fetch_steam_inventory(steam_id: str) -> dict:
    """Fetch CS2 inventory from Steam Community API. Returns grouped items."""
    if not steam_id:
        return {"all_items": [], "total": 0, "groups": {}}

    # Check cache
    if steam_id in _inventory_cache:
        data, ts = _inventory_cache[steam_id]
        if time.time() - ts < _INVENTORY_CACHE_TTL:
            return data

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": f"https://steamcommunity.com/profiles/{steam_id}/inventory/",
    }

    all_assets = []
    all_descs: dict[str, dict] = {}
    last_assetid = None

    try:
        import requests
        for _ in range(20):  # max 20 pages
            url = f"https://steamcommunity.com/inventory/{steam_id}/730/2"
            params: dict = {"l": "german", "count": 75}
            if last_assetid:
                params["start_assetid"] = last_assetid

            r = requests.get(url, params=params, headers=headers, timeout=15)
            if r.status_code != 200:
                break

            page = r.json()
            if not page.get("success"):
                break

            for a in page.get("assets", []):
                all_assets.append(a)
            for d in page.get("descriptions", []):
                key = f"{d['classid']}_{d['instanceid']}"
                all_descs[key] = d

            if not page.get("more_items"):
                break
            last_assetid = page.get("last_assetid")
            time.sleep(0.5)
    except Exception:
        if steam_id in _inventory_cache:
            return _inventory_cache[steam_id][0]
        return {"all_items": [], "total": 0, "groups": {}}

    # Build item list
    items = []
    for asset in all_assets:
        key = f"{asset['classid']}_{asset['instanceid']}"
        desc = all_descs.get(key, {})
        name = desc.get("market_hash_name", desc.get("name", "?"))
        item_type = desc.get("type", "Sonstiges")
        # Extract rarity color from tags
        rarity_color = ""
        for tag in desc.get("tags", []):
            if tag.get("category") == "Rarity":
                rarity_color = tag.get("color", "")
                break
        # Extract category
        category = ""
        for tag in desc.get("tags", []):
            if tag.get("category") == "Type":
                category = tag.get("localized_tag_name", "")
                break
        icon = desc.get("icon_url", "")
        items.append({
            "name": name,
            "type": item_type,
            "category": category or item_type.split("(")[0].strip() if "(" in item_type else item_type,
            "rarity_color": f"#{rarity_color}" if rarity_color else "",
            "tradable": desc.get("tradable", 0),
            "marketable": desc.get("marketable", 0),
            "icon": f"https://community.akamai.steamstatic.com/economy/image/{icon}" if icon else "",
        })

    # Group by category
    groups: dict[str, list] = {}
    for item in sorted(items, key=lambda x: (x["category"], x["name"])):
        cat = item["category"]
        if cat not in groups:
            groups[cat] = []
        groups[cat].append(item)

    result_data = {"all_items": items, "total": len(items), "groups": groups}
    _inventory_cache[steam_id] = (result_data, time.time())
    return result_data


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
    def inject_globals():
        sid = _resolve_steam_id()
        lang = cfg.get("language", "de")
        return {
            "steam_avatar_url": _fetch_steam_avatar(sid) if sid else "",
            "_": make_translator(lang),
            "lang": lang,
        }

    @app.route("/", methods=["GET", "POST"])
    def index():
        exports = _get_exports(cfg)
        map_stats = _get_map_stats(exports)
        dashboard = _build_dashboard_data(exports, map_stats)
        return render_template("index.html", exports=exports, map_stats=map_stats,
                               dashboard=dashboard, config=cfg)

    @app.route("/analyze", methods=["GET", "POST"])
    def analyze():
        if request.method == "GET":
            return redirect(url_for("index"))
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
            json_filename = None
            if vault_path:
                md_path, json_filename = export_match(result, report, vault_path, subfolder)
                obsidian_path = str(md_path)

            # Discord webhook (file upload route)
            _post_discord(cfg.get("discord_webhook", ""), result, cfg)

            if json_filename:
                return redirect(url_for("view_export", filename=json_filename))

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

    @app.route("/analyze-path", methods=["GET", "POST"])
    def analyze_path():
        """Analyze a demo from a local file path (no upload needed)."""
        if request.method == "GET":
            return redirect(url_for("index"))
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
            json_filename = None
            if vault_path:
                md_path, json_filename = export_match(result, report, vault_path, subfolder)
                obsidian_path = str(md_path)

            # Discord webhook (path-based route)
            _post_discord(cfg.get("discord_webhook", ""), result, cfg)

            if json_filename:
                return redirect(url_for("view_export", filename=json_filename))

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

        demos = sorted(folder_path.glob("*.dem"), key=lambda p: p.name, reverse=True)
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

        from cs2_coach.parser import _read_demo_timestamp
        result = []
        for d in demos:
            date_str = _read_demo_timestamp(d).strftime("%Y-%m-%d %H:%M")
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
        insights = _build_session_insights(export_list)
        return render_template("sessions.html", sessions=session_data, insights=insights, config=cfg)

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
        strength = _build_opponent_strength(cfg)
        return render_template("opponents.html", opponents=opponent_data, ostr=strength, config=cfg)

    @app.route("/compare")
    def compare():
        export_list = _get_exports(cfg)
        periods = _build_period_comparison(export_list)
        achievements = _build_compare_achievements(export_list, cfg)
        return render_template("compare.html", periods=periods,
                               achievements=achievements, exports=export_list, config=cfg)

    @app.route("/economy")
    def economy():
        export_list = _get_exports(cfg)
        eco_data = _build_economy_iq(export_list, cfg)
        return render_template("economy.html", eco=eco_data, config=cfg)

    @app.route("/utility")
    def utility():
        util_data = _build_utility_analysis(cfg)
        return render_template("utility.html", util=util_data, config=cfg)

    @app.route("/calendar")
    def calendar():
        cal_data = _build_calendar_data(cfg)
        return render_template("calendar.html", cal=cal_data, config=cfg)

    @app.route("/duels")
    def duels():
        duel_data = _build_duel_analysis(cfg)
        return render_template("duels.html", da=duel_data, config=cfg)

    @app.route("/sides")
    def sides():
        side_data = _build_side_analysis(cfg)
        return render_template("sides.html", sa=side_data, config=cfg)

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

    @app.route("/digest/discord", methods=["POST"])
    def digest_discord():
        webhook_url = cfg.get("discord_webhook", "")
        if not webhook_url:
            flash("Kein Discord-Webhook konfiguriert (Einstellungen).", "error")
            return redirect(url_for("digest"))
        export_list = _get_exports(cfg)
        digest_data = _build_digest(export_list)
        period_key = request.form.get("period", "")
        period = None
        for source in [digest_data.get("periods", []), digest_data.get("weekly", [])]:
            for p in source:
                if p["key"] == period_key:
                    period = p
                    break
        if not period:
            flash("Zeitraum nicht gefunden.", "error")
            return redirect(url_for("digest"))
        try:
            p = period
            result_emoji = ":green_circle:" if p["win_rate"] >= 55 else (":red_circle:" if p["win_rate"] < 45 else ":yellow_circle:")
            player = cfg.get("player_name", "Spieler")
            delta_text = ""
            if p.get("delta"):
                d = p["delta"]
                parts = []
                for key, label in [("rating", "Rating"), ("win_rate", "WR"), ("kd", "K/D")]:
                    v = d.get(key, 0)
                    if v != 0:
                        sign = "+" if v > 0 else ""
                        parts.append(f"{label} {sign}{v}")
                if parts:
                    delta_text = f"\n📊 vs. Vorperiode: {' | '.join(parts)}"
            embed = {
                "title": f"{result_emoji} Digest: {p['label']}",
                "color": 0x4ade80 if p["win_rate"] >= 55 else (0xf87171 if p["win_rate"] < 45 else 0xfbbf24),
                "description": f"**{p['matches']}** Matches — {p['wins']}W / {p['losses']}L{delta_text}",
                "fields": [
                    {"name": "Win-Rate", "value": f"**{p['win_rate']}%**", "inline": True},
                    {"name": "Rating", "value": f"**{p['avg_rating']}**", "inline": True},
                    {"name": "K/D", "value": f"**{p['avg_kd']}**", "inline": True},
                    {"name": "ADR", "value": f"**{p['avg_adr']}**", "inline": True},
                    {"name": "HS%", "value": f"{p['avg_hs']}%", "inline": True},
                    {"name": "KAST%", "value": f"{p['avg_kast']}%", "inline": True},
                ],
                "footer": {"text": f"{player} • CS2 Coach Digest"},
                "timestamp": datetime.now().isoformat(),
            }
            if p.get("best"):
                embed["fields"].append({"name": "Bestes Match", "value": f"{p['best']['map']} ({p['best']['score']}) — {p['best']['rating']}", "inline": False})
            payload = json.dumps({"embeds": [embed]}).encode("utf-8")
            req = urllib.request.Request(
                webhook_url, data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "CS2-Coach/1.0"},
            )
            urllib.request.urlopen(req, timeout=5)
            flash(f"Digest an Discord gesendet: {p['label']}", "success")
        except Exception as exc:
            flash(f"Discord-Fehler: {exc}", "error")
        return redirect(url_for("digest"))

    @app.route("/exports")
    def exports():
        export_list = _get_exports(cfg)
        # Build unique players list across all exports
        players_seen = {}
        for e in export_list:
            for sid, p in e.get("player_stats_map", {}).items():
                if sid not in players_seen:
                    players_seen[sid] = p.get("name", sid)
        all_players = sorted(players_seen.items(), key=lambda x: x[1].lower())
        return render_template("exports.html", exports=export_list,
                               all_players=all_players, config=cfg)

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
        # Resolve demo path for re-analysis with different player
        demo_file = data.get("match", {}).get("demo_file", "")
        demo_path = ""
        if demo_file:
            demo_folder = cfg.get("demo_folder", "")
            if demo_folder:
                candidate = Path(demo_folder) / demo_file
                if candidate.exists():
                    demo_path = str(candidate)
        # Build player averages for trend comparison
        all_exports = _get_exports(cfg)
        avg = {}
        if len(all_exports) >= 2:
            n = len(all_exports)
            avg = {
                "kd": round(sum(e.get("kd", 0) for e in all_exports) / n, 2),
                "adr": round(sum(e.get("adr", 0) for e in all_exports) / n, 1),
                "rating": round(sum(e.get("rating", 0) for e in all_exports) / n, 2),
                "kast": round(sum(e.get("kast", 0) for e in all_exports) / n, 1),
                "hs_pct": round(sum(e.get("hs_pct", 0) for e in all_exports) / n, 1),
            }
        return render_template("export_detail.html", data=data, analysis=analysis,
                               filename=filename, demo_path=demo_path,
                               player_avg=avg,
                               kill_map_data=_build_kill_map_data_from_export(data),
                               config=cfg)

    @app.route("/share/<path:filename>")
    def share_card(filename):
        vault_path = cfg.get("obsidian_vault_path", "")
        subfolder = cfg.get("coach_subfolder", "CS2-Coach")
        export_dir = Path(vault_path) / subfolder / "exports"
        filepath = export_dir / filename

        if not filepath.resolve().is_relative_to(export_dir.resolve()) or not filepath.exists():
            flash("Export nicht gefunden.", "error")
            return redirect(url_for("exports"))

        data = json.loads(filepath.read_text(encoding="utf-8"))
        return render_template("share.html", data=data)

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
        demos = sorted(folder_path.glob("*.dem"), key=lambda p: p.name, reverse=True)
        new_demos = [d for d in demos if d.name not in analyzed_files]

        def _demo_date(d):
            from cs2_coach.parser import _read_demo_timestamp
            return _read_demo_timestamp(d).strftime("%Y-%m-%d %H:%M")

        return jsonify({
            "total": len(demos),
            "analyzed": len(demos) - len(new_demos),
            "new": len(new_demos),
            "new_files": [{"name": d.name, "path": str(d), "size_mb": round(d.stat().st_size / (1024*1024), 1), "date": _demo_date(d)} for d in new_demos[:20]],
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
            key=lambda p: p.name,
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

    @app.route("/sharecode")
    def sharecode():
        return render_template("sharecode.html", config=cfg)

    @app.route("/api/sharecode-decode", methods=["POST"])
    def sharecode_decode():
        """Decode a share code and return match info."""
        from ..sharecode import decode_sharecode, validate_sharecode
        code = request.form.get("code", "").strip()
        if not code:
            return jsonify({"error": "Kein Share-Code angegeben"}), 400
        if not validate_sharecode(code):
            return jsonify({"error": "Ungueltiges Format. Erwartet: CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx"}), 400
        try:
            info = decode_sharecode(code)
            return jsonify({"ok": True, **info})
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/sharecode-analyze")
    def sharecode_analyze():
        """SSE endpoint: find demo locally via share code match_id and analyze it."""
        from ..sharecode import decode_sharecode, validate_sharecode, find_demo_by_match_id, find_cs2_replays_folders
        code = request.args.get("code", "").strip()
        player_name = request.args.get("player", "") or cfg.get("player_name", "")
        steam_id = request.args.get("steamid", "") or _resolve_steam_id()
        demo_folder = cfg.get("demo_folder", "")

        if not validate_sharecode(code):
            def err():
                yield f"data: {json.dumps({'type': 'error', 'message': 'Ungueltiger Share-Code'})}\n\n"
            return Response(err(), mimetype="text/event-stream")

        def generate():
            yield f"data: {json.dumps({'type': 'status', 'message': 'Share-Code wird dekodiert...'})}\n\n"
            try:
                info = decode_sharecode(code)
                match_id = info["match_id"]
                yield f"data: {json.dumps({'type': 'decoded', 'match_id': match_id, 'outcome_id': info['outcome_id'], 'token': info['token']})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': f'Dekodierung fehlgeschlagen: {e}'})}\n\n"
                return

            yield f"data: {json.dumps({'type': 'status', 'message': f'Suche Demo lokal (Match-ID: {match_id})...'})}\n\n"

            # Build search directories
            search_dirs = []
            if demo_folder and Path(demo_folder).is_dir():
                search_dirs.append(Path(demo_folder))
            for rdir in find_cs2_replays_folders():
                if rdir not in search_dirs:
                    search_dirs.append(rdir)

            dem_path = find_demo_by_match_id(match_id, search_dirs)

            if not dem_path:
                dirs_str = ", ".join(str(d) for d in search_dirs) if search_dirs else "(kein Ordner konfiguriert)"
                yield f"data: {json.dumps({'type': 'error', 'message': f'Demo nicht lokal gefunden (Match-ID: {match_id}). Durchsuchte Ordner: {dirs_str}. Lade die Demo zuerst in CS2 herunter: Persoenliches Spiellog > Match auswaehlen > Download.'})}\n\n"
                return

            yield f"data: {json.dumps({'type': 'status', 'message': f'Demo gefunden: {dem_path.name}'})}\n\n"
            yield f"data: {json.dumps({'type': 'status', 'message': 'Demo wird analysiert...'})}\n\n"
            try:
                result = parse_demo(str(dem_path), player_name, steam_id)
                report = generate_report(result)
                vault_path = cfg.get("obsidian_vault_path", "")
                sub = cfg.get("coach_subfolder", "CS2-Coach")
                if vault_path:
                    export_match(result, report, vault_path, sub)

                s = result.player_stats
                yield f"data: {json.dumps({'type': 'result', 'map': result.map_name, 'score': f'{result.score_team1}:{result.score_team2}', 'result_str': result.result_str, 'rating': result.rating, 'kills': s.kills, 'deaths': s.deaths, 'kd': round(s.kd_ratio, 2), 'adr': round(s.adr, 1), 'player': s.name})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': f'Analyse fehlgeschlagen: {e}'})}\n\n"
                return

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        return Response(stream_with_context(generate()), mimetype="text/event-stream")

    @app.route("/api/auto-sync")
    def auto_sync():
        """SSE endpoint: fetch new matches via Steam API, find local demos, analyze.

        Workflow:
        1. Fetch new share codes via GetNextMatchSharingCode
        2. Match share code match_ids to local demo files (CS2 replays folder)
        3. If Steam logged in: download missing demos from GCPD
        4. Also scan demo_folder for any unanalyzed demos
        5. Analyze all found demos
        """
        from ..sharecode import (fetch_all_new_codes, find_cs2_replays_folders,
            load_steam_session, fetch_gcpd_demo_urls, download_demo_bz2)
        api_key = cfg.get("steam_api_key", "")
        auth_token = cfg.get("cs2_auth_token", "")
        steam_id = _resolve_steam_id()
        last_code = cfg.get("last_sharecode", "")
        player_name = cfg.get("player_name", "")
        demo_folder = cfg.get("demo_folder", "")

        if not api_key or not auth_token or not steam_id:
            def err():
                missing = []
                if not api_key:
                    missing.append("Steam API Key")
                if not auth_token:
                    missing.append("CS2 Auth Token")
                if not steam_id:
                    missing.append("Steam ID")
                yield f"data: {json.dumps({'type': 'error', 'message': 'Fehlende Konfiguration: ' + ', '.join(missing) + '. Bitte in Einstellungen eintragen.'})}\n\n"
            return Response(err(), mimetype="text/event-stream")

        if not last_code:
            def err():
                yield f"data: {json.dumps({'type': 'error', 'message': 'Kein Start-Code gesetzt. Bitte zuerst einen Match Share-Code manuell analysieren oder unter Share-Code einfuegen.'})}\n\n"
            return Response(err(), mimetype="text/event-stream")

        def _get_analyzed_demos() -> set[str]:
            """Return set of demo filenames already analyzed (from exports)."""
            vault_path = cfg.get("obsidian_vault_path", "")
            subfolder = cfg.get("coach_subfolder", "CS2-Coach")
            if not vault_path:
                return set()
            export_dir = Path(vault_path) / subfolder / "exports"
            if not export_dir.exists():
                return set()
            analyzed = set()
            for f in export_dir.glob("*_coach.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    src = data.get("match", {}).get("demo_file", "")
                    if src:
                        analyzed.add(Path(src).name)
                except (json.JSONDecodeError, OSError):
                    pass
            return analyzed

        def generate():
            import time as _time

            analyzed = _get_analyzed_demos()
            success_count = 0
            demos_to_analyze = []  # (label, path)
            latest_code = last_code

            # Phase 1: Check Steam API for new matches (signal only — share code
            # match_ids use a different ID space than demo filenames/GCPD)
            yield f"data: {json.dumps({'type': 'status', 'message': 'Pruefe auf neue Matches via Steam API...'})}\n\n"

            new_codes = []
            try:
                new_codes = fetch_all_new_codes(api_key, steam_id, auth_token, last_code)
            except Exception as e:
                yield f"data: {json.dumps({'type': 'status', 'message': f'Steam API: {e}'})}\n\n"

            if new_codes:
                yield f"data: {json.dumps({'type': 'status', 'message': f'{len(new_codes)} neue Matches erkannt!'})}\n\n"
                latest_code = new_codes[-1]
            else:
                yield f"data: {json.dumps({'type': 'status', 'message': 'Keine neuen Codes via API.'})}\n\n"

            # Phase 2: Download all unanalyzed demos from GCPD (if Steam logged in)
            steam_session = load_steam_session()
            if steam_session:
                yield f"data: {json.dumps({'type': 'status', 'message': 'Steam-Session aktiv — pruefe GCPD fuer herunterladbare Demos...'})}\n\n"

                gcpd_msgs = []
                gcpd_demos = fetch_gcpd_demo_urls(
                    steam_session, steam_id,
                    on_status=lambda msg: gcpd_msgs.append(msg),
                )
                for msg in gcpd_msgs:
                    yield f"data: {json.dumps({'type': 'status', 'message': msg})}\n\n"

                # Determine download destination
                dl_folder = demo_folder if demo_folder and Path(demo_folder).is_dir() else None
                if not dl_folder:
                    replay_dirs = find_cs2_replays_folders()
                    if replay_dirs:
                        dl_folder = str(replay_dirs[0])
                    else:
                        dl_folder = str(Path(__file__).resolve().parent.parent.parent / "demos")
                        Path(dl_folder).mkdir(exist_ok=True)

                downloaded_count = 0
                for demo_info in gcpd_demos:
                    # Check if already analyzed (match by reservation ID in filename)
                    dem_prefix = f"match730_{str(demo_info['match_id']).zfill(21)}"
                    if any(dem_prefix in a for a in analyzed):
                        continue

                    dl_msgs = []
                    dem_path = download_demo_bz2(
                        demo_info["url"], dl_folder,
                        match_date=demo_info.get("date", ""),
                        on_status=lambda msg: dl_msgs.append(msg),
                    )
                    for msg in dl_msgs:
                        yield f"data: {json.dumps({'type': 'status', 'message': msg})}\n\n"

                    if dem_path and dem_path.name not in analyzed:
                        demos_to_analyze.append(("GCPD", dem_path))
                        downloaded_count += 1

                if downloaded_count:
                    yield f"data: {json.dumps({'type': 'status', 'message': f'{downloaded_count} Demos von Valve heruntergeladen!'})}\n\n"
                elif gcpd_demos:
                    yield f"data: {json.dumps({'type': 'status', 'message': 'Alle GCPD-Demos bereits vorhanden oder analysiert.'})}\n\n"

            # Phase 3: Scan local demo folders for unanalyzed demos
            search_dirs = []
            if demo_folder and Path(demo_folder).is_dir():
                search_dirs.append(Path(demo_folder))
            for rdir in find_cs2_replays_folders():
                if rdir not in search_dirs:
                    search_dirs.append(rdir)

            already_queued = {p.name for _, p in demos_to_analyze}
            for d in search_dirs:
                for f in sorted(d.glob("*.dem"), key=lambda p: p.stat().st_mtime, reverse=True):
                    if f.stat().st_size > 1_000_000 and f.name not in analyzed and f.name not in already_queued:
                        demos_to_analyze.append(("lokal", f))
                        already_queued.add(f.name)

            local_count = sum(1 for src, _ in demos_to_analyze if src == "lokal")
            if local_count:
                yield f"data: {json.dumps({'type': 'status', 'message': f'{local_count} unanalysierte Demos im lokalen Ordner gefunden!'})}\n\n"

            # Phase 4: Analyze all found demos
            # Phase 4: Analyze all found demos
            total_to_analyze = len(demos_to_analyze)

            if total_to_analyze == 0:
                yield f"data: {json.dumps({'type': 'done', 'message': 'Keine neuen Demos zum Analysieren gefunden.', 'count': 0})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'init', 'total': total_to_analyze, 'message': f'{total_to_analyze} Demos werden analysiert...'})}\n\n"

            for idx, (source, dem_path) in enumerate(demos_to_analyze):
                current = idx + 1
                yield f"data: {json.dumps({'type': 'status', 'message': f'Analysiere {current}/{total_to_analyze}: {dem_path.name} ({source})...'})}\n\n"
                try:
                    result = parse_demo(str(dem_path), player_name, steam_id)
                    report = generate_report(result)
                    vault_path = cfg.get("obsidian_vault_path", "")
                    sub = cfg.get("coach_subfolder", "CS2-Coach")
                    if vault_path:
                        export_match(result, report, vault_path, sub)
                    _post_discord(cfg.get("discord_webhook", ""), result, cfg)

                    s = result.player_stats
                    result_str = result.result_str
                    yield f"data: {json.dumps({'type': 'result', 'current': current, 'total': total_to_analyze, 'filename': dem_path.name, 'source': source, 'map': result.map_name, 'score': f'{result.score_team1}:{result.score_team2}', 'result_str': result_str, 'rating': result.rating, 'kills': s.kills, 'deaths': s.deaths})}\n\n"
                    success_count += 1
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'error_item', 'current': current, 'total': total_to_analyze, 'filename': dem_path.name, 'message': str(e)})}\n\n"

            # Save the latest share code for next sync
            if latest_code != last_code:
                cfg["last_sharecode"] = latest_code
                try:
                    import yaml as _yaml
                    config_data = dict(cfg)
                    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                        f.write("# CS2-Coach Konfiguration\n")
                        _yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)
                except Exception:
                    pass

            if total_to_analyze > 0:
                yield f"data: {json.dumps({'type': 'done', 'message': f'{success_count} Matches analysiert', 'count': success_count})}\n\n"

        return Response(stream_with_context(generate()), mimetype="text/event-stream")

    @app.route("/api/set-last-sharecode", methods=["POST"])
    def set_last_sharecode():
        """Set the starting share code for auto-sync."""
        code = request.form.get("code", "").strip()
        from ..sharecode import validate_sharecode
        if not code or not validate_sharecode(code):
            return jsonify({"error": "Ungueltiger Share-Code"}), 400
        cfg["last_sharecode"] = code
        try:
            import yaml as _yaml
            config_data = dict(cfg)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write("# CS2-Coach Konfiguration\n")
                _yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)
        except Exception:
            pass
        return jsonify({"ok": True, "code": code})

    @app.route("/api/steam-login", methods=["POST"])
    def steam_login_api():
        """Authenticate to Steam for automatic demo downloads."""
        from ..sharecode import steam_login, Steam2FARequired, SteamEmailCodeRequired, SteamLoginError
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        twofactor = request.form.get("twofactor", "").strip()
        email_code = request.form.get("email_code", "").strip()

        if not username or not password:
            return jsonify({"error": "Benutzername und Passwort erforderlich."}), 400

        try:
            steam_login(username, password, twofactor_code=twofactor, email_code=email_code)
            # Save username in config (password is NOT saved)
            cfg["steam_username"] = username
            try:
                import yaml as _yaml
                config_data = dict(cfg)
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    f.write("# CS2-Coach Konfiguration\n")
                    _yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)
            except Exception:
                pass
            return jsonify({"ok": True, "message": "Steam-Login erfolgreich!"})
        except Steam2FARequired:
            return jsonify({"error": "Steam Guard 2FA-Code erforderlich.", "need_2fa": True}), 401
        except SteamEmailCodeRequired:
            return jsonify({"error": "Steam Guard E-Mail-Code erforderlich. Pruefe deine E-Mails.", "need_email": True}), 401
        except SteamLoginError as e:
            return jsonify({"error": str(e)}), 401
        except Exception as e:
            return jsonify({"error": f"Login fehlgeschlagen: {e}"}), 401

    @app.route("/api/steam-logout", methods=["POST"])
    def steam_logout_api():
        """Clear stored Steam session."""
        from ..sharecode import clear_steam_session
        clear_steam_session()
        return jsonify({"ok": True})

    @app.route("/api/steam-status")
    def steam_status_api():
        """Check if Steam session is active."""
        from ..sharecode import is_steam_logged_in
        return jsonify({"logged_in": is_steam_logged_in(), "username": cfg.get("steam_username", "")})

    @app.route("/api/steam-gcpd-debug")
    def steam_gcpd_debug():
        """Debug endpoint: show raw GCPD AJAX response for diagnosing demo download."""
        from ..sharecode import load_steam_session
        import re as _re

        session = load_steam_session()
        if not session:
            return jsonify({"error": "Nicht eingeloggt"}), 401

        steam_id = _resolve_steam_id()
        tab = request.args.get("tab", "matchhistorypremier")

        base_url = "https://steamcommunity.com"
        profile_url = f"{base_url}/profiles/{steam_id}/gcpd/730" if steam_id else f"{base_url}/my/gcpd/730"
        page_url = f"{profile_url}?tab={tab}"

        try:
            resp = session.get(page_url, timeout=20, allow_redirects=True)
            html = resp.text
            gcpd_base = resp.url.split("?")[0]

            result = {
                "initial_url": page_url,
                "final_url": resp.url,
                "status": resp.status_code,
                "page_size": len(html),
                "found_personal_data": None,
                "continue_token": None,
                "session_id": None,
                "initial_demo_urls": [],
                "ajax_response_preview": None,
                "ajax_demo_urls": [],
                "ajax_status": None,
            }

            # Extract key variables from initial page
            fpd = _re.search(r"g_bFoundPersonalData\s*=\s*(\d+)", html)
            if fpd:
                result["found_personal_data"] = int(fpd.group(1))

            cont_m = _re.search(r"g_sGcContinueToken\s*=\s*['\"]([^'\"]*)['\"]", html)
            sess_m = _re.search(r"g_sessionID\s*=\s*['\"]([^'\"]*)['\"]", html)

            # Fallback: get sessionid from cookies if not in page JS
            result["cookie_sessionid"] = session.cookies.get("sessionid", domain="steamcommunity.com")

            if cont_m:
                result["continue_token"] = cont_m.group(1)
            if sess_m:
                result["session_id"] = sess_m.group(1)

            # Check for demo URLs in initial page
            from ..sharecode import _GCPD_DEMO_URL_RE
            for m in _GCPD_DEMO_URL_RE.finditer(html):
                result["initial_demo_urls"].append(m.group(1))

            # Try AJAX call
            if result["continue_token"] and result["session_id"]:
                ajax_url = (
                    f"{gcpd_base}?tab={tab}"
                    f"&continue_token={result['continue_token']}"
                    f"&sessionid={result['session_id']}"
                    f"&ajax=1"
                )
                aresp = session.get(
                    ajax_url, timeout=20,
                    headers={
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": f"{gcpd_base}?tab={tab}",
                        "Accept": "text/html, */*; q=0.01",
                    },
                )
                result["ajax_status"] = aresp.status_code
                ajax_text = aresp.text
                result["ajax_response_length"] = len(ajax_text)

                # Parse JSON response and extract HTML
                search_text = ajax_text
                try:
                    jdata = aresp.json()
                    result["ajax_is_json"] = True
                    result["ajax_json_keys"] = list(jdata.keys()) if isinstance(jdata, dict) else "not_dict"
                    if isinstance(jdata, dict):
                        html_part = jdata.get("html", jdata.get("results_html", ""))
                        if html_part:
                            search_text = html_part
                        result["ajax_continue_token"] = jdata.get("continue_token", "")
                except ValueError:
                    result["ajax_is_json"] = False

                result["ajax_response_preview"] = search_text[:2000]

                for m in _GCPD_DEMO_URL_RE.finditer(search_text):
                    result["ajax_demo_urls"].append(m.group(1))

            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

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
        mastery = _build_map_mastery(exports, map_stats)
        return render_template("maps.html", map_stats=map_stats, veto=veto, mastery=mastery, config=cfg)

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

    @app.route("/nemesis")
    def nemesis():
        nem_data = _build_nemesis(cfg)
        return render_template("nemesis.html", nem=nem_data, config=cfg)

    @app.route("/scout")
    def scout():
        scout_data = _build_scout_data(cfg)
        return render_template("scout.html", scout=scout_data, config=cfg)

    @app.route("/practice")
    def practice():
        pdata = build_practice_data(cfg)
        # Auto-detect CS2 cfg path from demo_folder
        demo_folder = cfg.get("demo_folder", "")
        default_cfg_path = ""
        if demo_folder:
            csgo_cfg = Path(demo_folder).parent / "cfg"
            if csgo_cfg.exists():
                default_cfg_path = str(csgo_cfg)
        pdata["default_cfg_path"] = cfg.get("practice_cfg_path", "") or default_cfg_path
        return render_template("practice.html", pdata=pdata, config=cfg)

    @app.route("/practice/generate", methods=["POST"])
    def practice_generate():
        """Generate practice .cfg files into docker/cfg/coach/ + custom path."""
        positions = _load_positions(cfg)
        if not positions:
            flash("Keine Positionsdaten vorhanden.", "error")
            return redirect(url_for("practice"))

        # Primary output: docker/cfg/ (or practice_cfg_path if set)
        practice_base = cfg.get("practice_cfg_path", "")
        if practice_base and Path(practice_base).is_dir():
            docker_cfg = Path(practice_base)
        else:
            docker_cfg = Path(__file__).parent.parent.parent / "docker" / "cfg"
        coach_dir = docker_cfg / "coach"
        coach_dir.mkdir(parents=True, exist_ok=True)

        # Get custom output path from form
        custom_path = request.form.get("cfg_path", "").strip()
        if custom_path:
            cfg["practice_cfg_path"] = custom_path
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write("# CS2-Coach Konfiguration\n")
                yaml.dump(dict(cfg), f, default_flow_style=False, allow_unicode=True)

        # Resolve custom output coach subfolder
        output_coach = None
        if custom_path:
            p = Path(custom_path)
            if p.exists() and p.is_dir():
                output_coach = p / "coach"
                output_coach.mkdir(exist_ok=True)

        def _write(dir_path, filename, content):
            """Write cfg to coach_dir and optionally output_coach."""
            (dir_path / filename).write_text(content, encoding="utf-8")
            if output_coach:
                (output_coach / filename).write_text(content, encoding="utf-8")

        generated = []
        map_names = []
        for map_name, pos_data in positions.items():
            if len(pos_data["kills"]) + len(pos_data["deaths"]) < 5:
                continue
            mn = map_name.lower()
            map_names.append(map_name)

            # 5 modes per map
            _write(coach_dir, f"practice_{mn}.cfg",
                   generate_practice_cfg(map_name, pos_data))
            _write(coach_dir, f"retake_{mn}.cfg",
                   generate_retake_cfg(map_name, pos_data))
            _write(coach_dir, f"spray_{mn}.cfg",
                   generate_spray_cfg(map_name, pos_data))
            _write(coach_dir, f"challenge_{mn}.cfg",
                   generate_challenge_cfg(map_name, pos_data))
            generated.extend([
                f"practice_{mn}", f"retake_{mn}",
                f"spray_{mn}", f"challenge_{mn}",
            ])

        # Utility (map-independent)
        _write(coach_dir, "utility.cfg", generate_utility_cfg())
        generated.append("utility")

        # Main practice.cfg — menu with all modes
        main_lines = [
            '// CS2 Coach — Practice Loader',
            '// exec coach/practice', '',
            'sv_cheats 1', 'mp_warmup_end', 'mp_freezetime 0',
            'mp_roundtime 60', 'mp_roundtime_defuse 60',
            'mp_buy_anywhere 1', 'mp_buytime 9999',
            'mp_startmoney 65535', 'mp_maxmoney 65535',
            'mp_free_armor 1', 'sv_infinite_ammo 1',
            'mp_autoteambalance 0', 'mp_limitteams 0',
            'bot_quota 0', 'bot_kick', '',
            'echo ""',
            'echo "══════════════════════════════════════"',
            'echo " CS2 Coach Practice Server"', 'echo ""',
        ]
        for mn in sorted(map_names):
            mnl = mn.lower()
            main_lines.append(f'echo " {mn}:"')
            main_lines.append(f'echo "   exec coach/practice_{mnl}   (Prefire)"')
            main_lines.append(f'echo "   exec coach/retake_{mnl}     (Retake)"')
            main_lines.append(f'echo "   exec coach/spray_{mnl}      (Spray)"')
            main_lines.append(f'echo "   exec coach/challenge_{mnl}  (Challenge)"')
        main_lines.extend([
            'echo ""',
            'echo " Global:"',
            'echo "   exec coach/utility   (Grenades)"',
            'echo "   exec coach/warmup    (Warmup)"',
            'echo "══════════════════════════════════════"',
            'echo ""',
        ])
        _write(coach_dir, "practice.cfg", "\n".join(main_lines))
        generated.append("practice")

        # Warmup config
        _write(coach_dir, "warmup.cfg", generate_warmup_cfg(positions))
        generated.append("warmup")

        # Server.cfg for Docker container (goes to docker/cfg/ root)
        (docker_cfg / "server.cfg").write_text(
            generate_server_cfg(), encoding="utf-8")
        generated.append("server")

        dest = str(output_coach or coach_dir)
        flash(f"{len(generated)} Configs gespeichert → {dest}", "success")
        return redirect(url_for("practice"))

    @app.route("/api/practice-plan", methods=["POST"])
    def api_practice_plan():
        """AI-driven config adjustment — analyzes data, regenerates cfgs."""
        pdata = build_practice_data(cfg)
        if not pdata.get("has_data"):
            return jsonify({"error": "Keine Daten"}), 400

        provider = cfg.get("ai_provider", "ollama")
        context = PRACTICE_PLAN_SYSTEM + "\n\n" + build_practice_context(pdata["maps"])
        prompt = (
            "Analysiere meine Spielerdaten und erstelle map_overrides "
            "um meine Practice-Server-Configs individuell anzupassen."
        )

        try:
            if provider == "gemini":
                api_key = cfg.get("gemini_api_key", "")
                if not api_key:
                    return jsonify({"error": "Kein Gemini API Key"}), 400
                model = cfg.get("ai_model", "") or "gemini-2.0-flash"
                raw = call_gemini(prompt, context, api_key, model)
            else:
                ollama_url = cfg.get("ollama_url", "http://192.168.188.71:11434")
                model = cfg.get("ai_model", "") or "llama3.1:8b"
                raw = call_ollama(prompt, context, ollama_url, model)

            if raw.startswith("**Fehler:**"):
                return jsonify({"error": raw}), 500

            # Parse JSON from response (strip markdown fences if present)
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
            cleaned = cleaned.strip()

            plan = json.loads(cleaned)

            # ── Apply overrides: regenerate cfgs and write to disk ──
            map_overrides = plan.get("map_overrides", {})
            positions = _load_positions(cfg)
            changed_cfgs = []

            if map_overrides and positions:
                # Case-insensitive map name lookup
                pos_lookup = {k.lower(): (k, v) for k, v in positions.items()}

                docker_cfg = Path(__file__).parent.parent.parent / "docker" / "cfg"
                coach_dir = docker_cfg / "coach"
                coach_dir.mkdir(parents=True, exist_ok=True)

                custom_path = cfg.get("practice_cfg_path", "")
                output_coach = None
                if custom_path:
                    p = Path(custom_path)
                    if p.exists() and p.is_dir():
                        output_coach = p / "coach"
                        output_coach.mkdir(exist_ok=True)

                def _write(filename, content):
                    (coach_dir / filename).write_text(content, encoding="utf-8")
                    if output_coach:
                        (output_coach / filename).write_text(content, encoding="utf-8")

                normalized_overrides = {}
                for ai_map_name, modes in map_overrides.items():
                    match = pos_lookup.get(ai_map_name.lower())
                    if not match:
                        continue
                    map_name, pos_data = match
                    mn = map_name.lower()
                    normalized_overrides[map_name] = modes

                    if "prefire" in modes:
                        _write(f"practice_{mn}.cfg",
                               generate_practice_cfg(map_name, pos_data,
                                                     overrides=modes["prefire"]))
                        changed_cfgs.append(f"practice_{mn}")
                    if "retake" in modes:
                        _write(f"retake_{mn}.cfg",
                               generate_retake_cfg(map_name, pos_data,
                                                   overrides=modes["retake"]))
                        changed_cfgs.append(f"retake_{mn}")
                    if "spray" in modes:
                        _write(f"spray_{mn}.cfg",
                               generate_spray_cfg(map_name, pos_data,
                                                  overrides=modes["spray"]))
                        changed_cfgs.append(f"spray_{mn}")
                    if "challenge" in modes:
                        _write(f"challenge_{mn}.cfg",
                               generate_challenge_cfg(map_name, pos_data,
                                                      overrides=modes["challenge"]))
                        changed_cfgs.append(f"challenge_{mn}")

                plan["map_overrides"] = normalized_overrides
            plan["changed_cfgs"] = changed_cfgs
            return jsonify(plan)
        except json.JSONDecodeError:
            return jsonify({
                "analysis": raw[:500],
                "map_overrides": {},
                "focus_mode": {},
                "routine": [],
                "total_time": "?",
                "tips": [],
                "changed_cfgs": [],
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/momentum")
    def momentum():
        mom_data = _build_momentum(cfg)
        return render_template("momentum.html", mom=mom_data, config=cfg)

    @app.route("/teammates")
    def teammates():
        tm_data = _build_teammates(cfg)
        return render_template("teammates.html", tm=tm_data, config=cfg)

    @app.route("/leaderboard")
    def leaderboard():
        lb_data = _build_leaderboard(cfg)
        return render_template("leaderboard.html", lb=lb_data, config=cfg)

    @app.route("/h2h")
    def h2h():
        target_sid = request.args.get("vs", "")
        h2h_data = _build_h2h(cfg, target_sid)
        return render_template("h2h.html", h2h=h2h_data, config=cfg)

    @app.route("/clutches")
    def clutches():
        cl_data = _build_clutch_analysis(cfg)
        return render_template("clutches.html", cl=cl_data, config=cfg)

    @app.route("/mechanics")
    def mechanics():
        mech_data = _build_mechanics(cfg)
        return render_template("mechanics.html", mech=mech_data, config=cfg)

    @app.route("/rounds")
    def rounds():
        rd_data = _build_round_timeline(cfg)
        return render_template("rounds.html", rd=rd_data, config=cfg)

    @app.route("/pistol")
    def pistol():
        pdata = _build_pistol_analysis(cfg)
        return render_template("pistol.html", p=pdata, config=cfg)

    @app.route("/tilt")
    def tilt():
        tilt_data = _build_tilt_analysis(cfg)
        return render_template("tilt.html", t=tilt_data, config=cfg)

    @app.route("/role")
    def role():
        role_data = _build_role_detection(cfg)
        return render_template("role.html", r=role_data, config=cfg)

    @app.route("/predict")
    def predict():
        pred_data = _build_opponent_prediction(cfg)
        return render_template("predict.html", p=pred_data, config=cfg)

    @app.route("/deaths")
    def deaths():
        death_data = _build_death_analysis(cfg)
        return render_template("deaths.html", da=death_data, config=cfg)

    @app.route("/inventory")
    def inventory():
        steam_id = cfg.get("steam_id", "")
        inv = _fetch_steam_inventory(steam_id) if steam_id else {"all_items": [], "total": 0, "groups": {}}
        return render_template("inventory.html", inv=inv, config=cfg)

    @app.route("/inventory/export")
    def inventory_export():
        steam_id = cfg.get("steam_id", "")
        inv = _fetch_steam_inventory(steam_id) if steam_id else {"all_items": [], "total": 0, "groups": {}}
        lines = [f"CS2 Inventar - {cfg.get('player_name', steam_id)}", f"Insgesamt: {inv['total']} Items", "=" * 60, ""]
        for cat, items in sorted(inv["groups"].items()):
            lines.append(f"\n--- {cat} ({len(items)}) ---")
            for item in items:
                lines.append(f"  {item['name']}  [{item['type']}]")
        text = "\n".join(lines)
        return Response(text, mimetype="text/plain",
                        headers={"Content-Disposition": "attachment; filename=cs2_inventar.txt"})

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
            "steam_api_key": request.form.get("steam_api_key", "").strip(),
            "cs2_auth_token": request.form.get("cs2_auth_token", "").strip(),
            "last_sharecode": cfg.get("last_sharecode", ""),
            "ai_provider": request.form.get("ai_provider", cfg.get("ai_provider", "ollama")).strip(),
            "gemini_api_key": request.form.get("gemini_api_key", cfg.get("gemini_api_key", "")).strip(),
            "ollama_url": request.form.get("ollama_url", cfg.get("ollama_url", "http://192.168.188.71:11434")).strip(),
            "ai_model": request.form.get("ai_model", cfg.get("ai_model", "")).strip(),
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

    @app.route("/graph")
    def graph():
        vault_path = cfg.get("obsidian_vault_path", "")
        subfolder = cfg.get("coach_subfolder", "CS2-Coach")
        graph_data: dict = {"has_data": False}
        if vault_path:
            graph_dir = Path(vault_path) / subfolder / "_graph"
            analysis_file = graph_dir / "Graph-Analyse.md"
            json_file = graph_dir / "cs2_graph.json"
            if analysis_file.exists():
                graph_data["has_data"] = True
                graph_data["analysis_md"] = analysis_file.read_text(encoding="utf-8")
            if json_file.exists():
                try:
                    gdata = json.loads(json_file.read_text(encoding="utf-8"))
                    graph_data["nodes"] = len(gdata.get("nodes", []))
                    graph_data["edges"] = len(gdata.get("edges", gdata.get("links", [])))
                    graph_data["top_nodes"] = sorted(
                        gdata.get("nodes", []),
                        key=lambda n: n.get("degree", n.get("size", 0)),
                        reverse=True,
                    )[:15]
                except Exception:
                    pass
        return render_template("graph.html", graph=graph_data, config=cfg)

    @app.route("/graph/rebuild", methods=["POST"])
    def graph_rebuild():
        vault_path = cfg.get("obsidian_vault_path", "")
        subfolder = cfg.get("coach_subfolder", "CS2-Coach")
        if not vault_path:
            flash("Vault-Pfad nicht konfiguriert.", "error")
            return redirect(url_for("graph"))
        try:
            from ..graph import index_vault
            stats = index_vault(vault_path, subfolder)
            flash(f"Graph neu gebaut: {stats['nodes']} Knoten, {stats['edges']} Kanten, {stats['clusters']} Cluster", "success")
        except Exception as exc:
            flash(f"Graph-Fehler: {exc}", "error")
        return redirect(url_for("graph"))

    @app.route("/zones")
    def zones():
        zone_data = _build_zone_analysis(cfg)
        return render_template("zones.html", zd=zone_data, config=cfg)

    @app.route("/team")
    def team():
        team_data = _build_team_analysis(cfg)
        return render_template("team.html", td=team_data, config=cfg)

    @app.route("/motor")
    def motor():
        motor_data = _build_motor_skills(cfg)
        return render_template("motor.html", ms=motor_data, config=cfg)

    @app.route("/export/csv")
    def export_csv():
        import csv
        import io
        exports = _get_exports(cfg)
        if not exports:
            flash("Keine Daten zum Exportieren.", "error")
            return redirect(url_for("settings"))

        buf = io.StringIO()
        fields = [
            "date", "map", "result", "score", "kills", "deaths", "assists",
            "kd", "adr", "rating", "hs_pct", "kast",
            "counter_strafe", "crosshair_placement", "utility_per_round",
            "opening_kills", "opening_deaths", "trade_kills", "survival_rate",
            "clutch_wins", "clutch_attempts",
        ]
        writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for e in exports:
            writer.writerow({f: e.get(f, "") for f in fields})

        output = buf.getvalue()
        return Response(
            output,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=cs2_coach_stats.csv"},
        )

    @app.route("/bookmarks")
    def bookmarks():
        bm_data = _build_bookmarks(cfg)
        return render_template("bookmarks.html", bm=bm_data, config=cfg)

    @app.route("/viewer")
    def viewer():
        vd = _build_viewer_data(cfg)
        return render_template("viewer.html", vd=vd, config=cfg)

    @app.route("/chat")
    def chat():
        provider = cfg.get("ai_provider", "ollama")
        ollama_url = cfg.get("ollama_url", "http://192.168.188.71:11434")
        ollama_status = check_ollama_status(ollama_url) if provider == "ollama" else {"online": False, "models": []}
        return render_template("chat.html", config=cfg,
                               ollama_status=ollama_status)

    @app.route("/api/ai-analyze-export", methods=["POST"])
    def api_ai_analyze_export():
        """SSE endpoint: AI deep analysis of a single export."""
        data = request.get_json(silent=True) or {}
        report = data.get("report", "")
        player = data.get("player", {})
        match = data.get("match", {})

        if not report and not player:
            return jsonify({"error": "Keine Daten"}), 400

        provider = cfg.get("ai_provider", "ollama")
        player_name = player.get("name", cfg.get("player_name", ""))

        context = (
            f"Spieler: {player_name}\n"
            f"Map: {match.get('map', '?')} — {match.get('score_own', '?')}:{match.get('score_enemy', '?')} "
            f"({match.get('result', '?')})\n"
            f"K/D: {player.get('kd', '?')} | ADR: {player.get('adr', '?')} | "
            f"KAST: {player.get('kast_pct', '?')}% | Rating: {player.get('rating', '?')}\n"
            f"HS%: {player.get('hs_pct', '?')} | Accuracy: {player.get('accuracy', '?')}%\n"
            f"Opening Kills: {player.get('opening_kills', 0)} | Opening Deaths: {player.get('opening_deaths', 0)}\n"
            f"Trade Kills: {player.get('trade_kills', 0)} | Survival Rate: {player.get('survival_rate', '?')}%\n"
            f"Counter-Strafe: {player.get('counter_strafe_score', '?')} | "
            f"Utility/Runde: {player.get('utility_per_round', '?')}\n\n"
            f"--- Regelbasierter Coach-Report ---\n{report}"
        )

        prompt = (
            "Du bist ein erfahrener CS2-Coach. Analysiere dieses Match tiefgehend. "
            "Gib 3 konkrete Staerken und 3 konkrete Schwaechen basierend auf den Daten. "
            "Erstelle dann einen priorisierten Trainingsplan mit 3 Uebungen. "
            "Beziehe dich auf die tatsaechlichen Zahlen. Antworte auf Deutsch."
        )

        messages = [{"role": "user", "content": prompt}]

        def generate():
            try:
                if provider == "gemini":
                    api_key = cfg.get("gemini_api_key", "")
                    if not api_key:
                        yield f"data: {json.dumps({'text': '**Fehler:** Kein Gemini API Key konfiguriert.'})}\n\n"
                        yield f"data: {json.dumps({'done': True})}\n\n"
                        return
                    model = cfg.get("ai_model", "") or "gemini-2.0-flash"
                    for chunk in stream_gemini(messages, context, api_key, model):
                        yield f"data: {json.dumps({'text': chunk})}\n\n"
                else:
                    ollama_url = cfg.get("ollama_url", "http://192.168.188.71:11434")
                    model = cfg.get("ai_model", "")
                    if not model:
                        status = check_ollama_status(ollama_url)
                        model = status["models"][0] if status["online"] and status["models"] else "llama3.1:8b"
                    for chunk in stream_ollama(messages, context, ollama_url, model):
                        yield f"data: {json.dumps({'text': chunk})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'text': f'**Fehler:** {e}'})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"

        return app.response_class(generate(), mimetype="text/event-stream")

    @app.route("/api/chat", methods=["POST"])
    def api_chat():
        """SSE endpoint for AI coach chat streaming."""
        data = request.get_json(silent=True) or {}
        messages = data.get("messages", [])
        if not messages:
            return jsonify({"error": "Keine Nachricht"}), 400

        provider = cfg.get("ai_provider", "ollama")
        exports = _get_exports(cfg)
        context = build_player_context(exports, cfg.get("player_name", ""))

        def generate():
            try:
                if provider == "gemini":
                    api_key = cfg.get("gemini_api_key", "")
                    if not api_key:
                        yield f"data: {json.dumps({'text': '**Fehler:** Kein Gemini API Key konfiguriert. Gehe zu Einstellungen.'})}\n\n"
                        yield f"data: {json.dumps({'done': True})}\n\n"
                        return
                    model = cfg.get("ai_model", "") or "gemini-2.0-flash"
                    for chunk in stream_gemini(messages, context, api_key, model):
                        yield f"data: {json.dumps({'text': chunk})}\n\n"
                else:
                    ollama_url = cfg.get("ollama_url", "http://192.168.188.71:11434")
                    model = cfg.get("ai_model", "")
                    if not model:
                        # Auto-detect first available model
                        status = check_ollama_status(ollama_url)
                        if status["online"] and status["models"]:
                            model = status["models"][0]
                        else:
                            model = "llama3.1:8b"
                    for chunk in stream_ollama(messages, context, ollama_url, model):
                        yield f"data: {json.dumps({'text': chunk})}\n\n"

                yield f"data: {json.dumps({'done': True})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'text': f'**Fehler:** {e}'})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"

        return Response(stream_with_context(generate()), mimetype="text/event-stream")

    @app.route("/api/narrate-round", methods=["POST"])
    def api_narrate_round():
        """SSE endpoint — AI narrates a specific round with coaching advice."""
        data = request.get_json(silent=True) or {}
        filename = data.get("filename", "")
        round_num = data.get("round")
        if not filename or round_num is None:
            return jsonify({"error": "filename und round erforderlich"}), 400

        # Load the export
        vault_path = cfg.get("obsidian_vault_path", "")
        subfolder = cfg.get("coach_subfolder", "CS2-Coach")
        export_dir = Path(vault_path) / subfolder / "exports"
        filepath = export_dir / filename
        if not filepath.resolve().is_relative_to(export_dir.resolve()) or not filepath.exists():
            return jsonify({"error": "Export nicht gefunden"}), 404

        match_data = json.loads(filepath.read_text(encoding="utf-8"))
        timeline = match_data.get("round_timeline", [])
        player = match_data.get("player", {})
        match_info = match_data.get("match", {})

        # Find the requested round
        target_round = None
        for r in timeline:
            if r.get("round") == round_num:
                target_round = r
                break
        if not target_round:
            return jsonify({"error": f"Runde {round_num} nicht gefunden"}), 404

        # Build round context for the AI
        round_ctx = _build_round_narration_context(
            target_round, round_num, timeline, player, match_info
        )

        narration_prompt = f"""Du bist ein CS2 Coach-Kommentator. Erzaehle diese Runde wie ein professioneller Analyst — lebendig, konkret, mit taktischem Einblick.

Regeln:
- Antworte auf Deutsch
- Erzaehle die Runde chronologisch, als wuerdest du ein Pro-Match analysieren
- Nenne den Spieler beim Namen
- Bewerte die Entscheidungen des Spielers (Peek-Timing, Position, Waffenwahl, Trade-Verhalten)
- Gib am Ende 1-2 konkrete Coaching-Tipps fuer genau diese Situation
- Halte die Erzaehlung unter 250 Woerter
- Nutze CS2-Terminologie (Peek, Trade, Off-Angle, Rotate, Default, etc.)

{round_ctx}"""

        messages = [{"role": "user", "content": narration_prompt}]
        provider = cfg.get("ai_provider", "ollama")

        def generate():
            try:
                if provider == "gemini":
                    api_key = cfg.get("gemini_api_key", "")
                    if not api_key:
                        yield f"data: {json.dumps({'text': '**Fehler:** Kein Gemini API Key konfiguriert.'})}\n\n"
                        yield f"data: {json.dumps({'done': True})}\n\n"
                        return
                    model = cfg.get("ai_model", "") or "gemini-2.0-flash"
                    for chunk in stream_gemini(messages, "", api_key, model):
                        yield f"data: {json.dumps({'text': chunk})}\n\n"
                else:
                    ollama_url = cfg.get("ollama_url", "http://192.168.188.71:11434")
                    model = cfg.get("ai_model", "")
                    if not model:
                        status = check_ollama_status(ollama_url)
                        if status["online"] and status["models"]:
                            model = status["models"][0]
                        else:
                            model = "llama3.1:8b"
                    for chunk in stream_ollama(messages, "", ollama_url, model):
                        yield f"data: {json.dumps({'text': chunk})}\n\n"

                yield f"data: {json.dumps({'done': True})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'text': f'**Fehler:** {e}'})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"

        return Response(stream_with_context(generate()), mimetype="text/event-stream")

    @app.route("/api/narrate-match", methods=["POST"])
    def api_narrate_match():
        """SSE endpoint — AI narrates the entire match with coaching analysis."""
        data = request.get_json(silent=True) or {}
        filename = data.get("filename", "")
        if not filename:
            return jsonify({"error": "filename erforderlich"}), 400

        vault_path = cfg.get("obsidian_vault_path", "")
        subfolder = cfg.get("coach_subfolder", "CS2-Coach")
        export_dir = Path(vault_path) / subfolder / "exports"
        filepath = export_dir / filename
        if not filepath.resolve().is_relative_to(export_dir.resolve()) or not filepath.exists():
            return jsonify({"error": "Export nicht gefunden"}), 404

        match_data = json.loads(filepath.read_text(encoding="utf-8"))
        match_ctx = _build_match_narration_context(match_data)

        narration_prompt = f"""Du bist ein professioneller CS2 Analyst und Coach. Erzaehle dieses komplette Match wie eine Post-Match-Analyse bei einem Turnier — strukturiert, lebendig, mit taktischer Tiefe.

Regeln:
- Antworte auf Deutsch
- Nenne den Spieler beim Namen
- Strukturiere die Analyse so:
  1. **Ueberblick** — Map, Ergebnis, Rating, ein-Satz-Zusammenfassung der Performance
  2. **Erste Haelfte** — Wie lief die erste Seite? Schluesselrunden? Momentum?
  3. **Zweite Haelfte** — Seitenwechsel, wie hat sich die Performance veraendert?
  4. **Schluesselmomente** — Die 2-3 Runden die das Match entschieden haben (Hero Rounds, Throw Rounds, Clutches)
  5. **Staerken** — Was hat der Spieler gut gemacht? Konkrete Beispiele
  6. **Baustellen** — Was muss besser werden? Konkrete Beispiele
  7. **Coaching-Fazit** — 2-3 konkrete Trainings-Empfehlungen basierend auf diesem Match
- Beziehe dich auf echte Runden-Nummern und Events
- Sei ehrlich aber konstruktiv
- Nutze CS2-Terminologie
- Max 600 Woerter

{match_ctx}"""

        messages = [{"role": "user", "content": narration_prompt}]
        provider = cfg.get("ai_provider", "ollama")

        def generate():
            try:
                if provider == "gemini":
                    api_key = cfg.get("gemini_api_key", "")
                    if not api_key:
                        yield f"data: {json.dumps({'text': '**Fehler:** Kein Gemini API Key konfiguriert.'})}\n\n"
                        yield f"data: {json.dumps({'done': True})}\n\n"
                        return
                    model = cfg.get("ai_model", "") or "gemini-2.0-flash"
                    for chunk in stream_gemini(messages, "", api_key, model):
                        yield f"data: {json.dumps({'text': chunk})}\n\n"
                else:
                    ollama_url = cfg.get("ollama_url", "http://192.168.188.71:11434")
                    model = cfg.get("ai_model", "")
                    if not model:
                        status = check_ollama_status(ollama_url)
                        if status["online"] and status["models"]:
                            model = status["models"][0]
                        else:
                            model = "llama3.1:8b"
                    for chunk in stream_ollama(messages, "", ollama_url, model):
                        yield f"data: {json.dumps({'text': chunk})}\n\n"

                yield f"data: {json.dumps({'done': True})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'text': f'**Fehler:** {e}'})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"

        return Response(stream_with_context(generate()), mimetype="text/event-stream")

    @app.route("/api/save-narration", methods=["POST"])
    def api_save_narration():
        """Persist a narration (match or round) into the export JSON file."""
        data = request.get_json(silent=True) or {}
        filename = data.get("filename", "")
        narration_type = data.get("type", "")  # "match" or "round"
        text = data.get("text", "")
        round_num = data.get("round")

        if not filename or not narration_type or not text:
            return jsonify({"error": "filename, type, text erforderlich"}), 400

        vault_path = cfg.get("obsidian_vault_path", "")
        subfolder = cfg.get("coach_subfolder", "CS2-Coach")
        export_dir = Path(vault_path) / subfolder / "exports"
        filepath = export_dir / filename
        if not filepath.resolve().is_relative_to(export_dir.resolve()) or not filepath.exists():
            return jsonify({"error": "Export nicht gefunden"}), 404

        export_data = json.loads(filepath.read_text(encoding="utf-8"))

        if narration_type == "match":
            export_data["match_narration"] = text
        elif narration_type == "round" and round_num is not None:
            rn = export_data.setdefault("round_narrations", {})
            rn[str(round_num)] = text
        else:
            return jsonify({"error": "type muss 'match' oder 'round' sein"}), 400

        filepath.write_text(json.dumps(export_data, ensure_ascii=False, indent=2), encoding="utf-8")
        return jsonify({"ok": True})

    @app.route("/api/ollama-status")
    def api_ollama_status():
        ollama_url = cfg.get("ollama_url", "http://192.168.188.71:11434")
        status = check_ollama_status(ollama_url)
        return jsonify(status)

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
            # Extract time of day from datetime field or filename
            dt_str = match.get("datetime", "")
            time_hour = -1
            if dt_str and " " in dt_str:
                try:
                    time_hour = int(dt_str.split(" ")[1].split(":")[0])
                except (ValueError, IndexError):
                    pass
            # Build per-player stats lookup from scoreboard
            scoreboard = data.get("scoreboard", [])
            player_stats_map = {}
            for sp in scoreboard:
                sid = sp.get("steam_id", "")
                if sid:
                    player_stats_map[sid] = {
                        "name": sp.get("name", ""),
                        "kd": sp.get("kd", 0),
                        "adr": sp.get("adr", 0),
                        "kast": sp.get("kast_pct", 0),
                        "rating": sp.get("rating", ""),
                    }
            exports.append({
                "filename": f.name,
                "date": match.get("date", match.get("datetime", "?")),
                "datetime": dt_str,
                "time_hour": time_hour,
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
                "accuracy": player.get("accuracy", 0),
                "burst_kills": player.get("spray_control", {}).get("burst_kills", 0),
                "spray_kills": player.get("spray_control", {}).get("spray_kills", 0),
                "target_player": player.get("steam_id", ""),
                "player_stats_map": player_stats_map,
            })
        except (json.JSONDecodeError, KeyError):
            continue

    exports.sort(key=lambda e: e.get("datetime", e.get("date", "")), reverse=True)
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


def _build_kill_map_data_from_export(data: dict) -> dict | None:
    """Build kill map JSON from a stored export (compact kill_positions format).

    Mirrors _build_kill_map_data() but reads the compacted export schema so the
    export detail view can render the same 2D map as the fresh-analysis view.
    Utility positions are not part of the export schema, so "utils" stays empty.
    """
    match_info = data.get("match", {})
    map_key = (match_info.get("map") or "").lower()
    if not map_key or map_key not in MAP_RADAR_DATA:
        return None

    positions = data.get("kill_positions", [])
    if not positions:
        return None

    dots = []
    for pos in positions:
        px = game_to_radar(pos.get("x", 0), pos.get("y", 0), map_key)
        if not px:
            continue
        is_kill = pos.get("t") == "k"
        enemy = pos.get("e", "")
        dots.append({
            "x": round(px[0], 1),
            "y": round(px[1], 1),
            "type": "kill" if is_kill else "death",
            "weapon": pos.get("w", "").replace("weapon_", ""),
            "headshot": pos.get("hs", False),
            "label": (f"Kill: {enemy}" if is_kill else f"Death by {enemy}"),
            "round": pos.get("r", 0),
        })

    if not dots:
        return None

    total_rounds = match_info.get("total_rounds") or (
        match_info.get("score_own", 0) + match_info.get("score_enemy", 0)
    )

    return {
        "map": map_key,
        "dots": dots,
        "utils": [],
        "total_rounds": total_rounds,
    }


def _build_viewer_data(cfg: dict) -> dict:
    """Build aggregated kill/death positions across all exports for the 2D viewer."""
    vault_path = cfg.get("obsidian_vault_path", "")
    subfolder = cfg.get("coach_subfolder", "CS2-Coach")
    if not vault_path:
        return {"has_data": False}

    export_dir = Path(vault_path) / subfolder / "exports"
    if not export_dir.exists():
        return {"has_data": False}

    # Aggregate per-map positions from all exports
    maps_data: dict[str, dict] = {}  # map -> {dots, matches}
    match_index: list[dict] = []  # list of matches for filter UI

    for f in sorted(export_dir.glob("*_coach.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        positions = data.get("kill_positions", [])
        match_info = data.get("match", {})
        map_name = match_info.get("map", "").lower()
        if not map_name or map_name not in MAP_RADAR_DATA:
            continue

        match_date = match_info.get("date", "")
        result_str = match_info.get("result", "")
        score = f"{match_info.get('score_own', 0)}:{match_info.get('score_enemy', 0)}"
        match_id = len(match_index)

        match_index.append({
            "id": match_id,
            "date": match_date,
            "map": map_name,
            "result": result_str,
            "score": score,
        })

        if map_name not in maps_data:
            maps_data[map_name] = {"dots": [], "matches": []}

        maps_data[map_name]["matches"].append(match_id)

        for pos in positions:
            px = game_to_radar(pos["x"], pos["y"], map_name)
            epx = game_to_radar(pos.get("ex", 0), pos.get("ey", 0), map_name)
            if not px:
                continue

            dot = {
                "x": round(px[0], 1),
                "y": round(px[1], 1),
                "type": "kill" if pos.get("t") == "k" else "death",
                "weapon": pos.get("w", "").replace("weapon_", ""),
                "headshot": pos.get("hs", False),
                "round": pos.get("r", 0),
                "enemy": pos.get("e", ""),
                "match": match_id,
            }
            if epx:
                dot["ex"] = round(epx[0], 1)
                dot["ey"] = round(epx[1], 1)
            maps_data[map_name]["dots"].append(dot)

    if not maps_data:
        return {"has_data": False}

    # Pick the map with the most data as default
    default_map = max(maps_data, key=lambda m: len(maps_data[m]["dots"]))

    # Summary per map
    map_summaries = []
    for m in sorted(maps_data.keys()):
        dots = maps_data[m]["dots"]
        kills = sum(1 for d in dots if d["type"] == "kill")
        deaths = sum(1 for d in dots if d["type"] == "death")
        map_summaries.append({
            "name": m,
            "kills": kills,
            "deaths": deaths,
            "matches": len(maps_data[m]["matches"]),
            "kd": round(kills / deaths, 2) if deaths else kills,
        })

    return {
        "has_data": True,
        "maps_data": maps_data,
        "map_summaries": map_summaries,
        "default_map": default_map,
        "match_index": match_index,
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


def _build_opponent_strength(cfg: dict) -> dict:
    """Analyze performance by opponent strength tier using enemy team ratings."""
    vault_path = cfg.get("obsidian_vault_path", "")
    subfolder = cfg.get("coach_subfolder", "CS2-Coach")
    if not vault_path:
        return {"has_data": False}

    export_dir = Path(vault_path) / subfolder / "exports"
    if not export_dir.exists():
        return {"has_data": False}

    # Tiers: weak (<0.85 avg enemy rating), average (0.85-1.05), strong (>1.05)
    tiers = {
        "weak": {"label": "Schwach", "threshold": "< 0.85", "color": "#4ade80",
                 "matches": 0, "wins": 0, "rating_sum": 0, "kd_sum": 0,
                 "adr_sum": 0, "enemy_rating_sum": 0},
        "average": {"label": "Mittel", "threshold": "0.85 – 1.05", "color": "#fbbf24",
                    "matches": 0, "wins": 0, "rating_sum": 0, "kd_sum": 0,
                    "adr_sum": 0, "enemy_rating_sum": 0},
        "strong": {"label": "Stark", "threshold": "> 1.05", "color": "#f87171",
                   "matches": 0, "wins": 0, "rating_sum": 0, "kd_sum": 0,
                   "adr_sum": 0, "enemy_rating_sum": 0},
    }
    match_details = []

    for f in sorted(export_dir.glob("*_coach.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        sb = data.get("scoreboard", [])
        match = data.get("match", {})
        player = data.get("player", {})
        if not sb or not player:
            continue

        target_idx = next((i for i, s in enumerate(sb) if s.get("is_target")), None)
        if target_idx is None:
            continue

        team_start = 0 if target_idx < 5 else 5
        enemy_start = 5 if target_idx < 5 else 0

        # Compute enemy team average KD and ADR (rating not always available for enemies)
        enemy_kds = []
        enemy_adrs = []
        for i in range(enemy_start, enemy_start + 5):
            if i >= len(sb):
                continue
            s = sb[i]
            enemy_kds.append(s.get("kd", 0))
            enemy_adrs.append(s.get("adr", 0))

        if not enemy_kds:
            continue

        avg_enemy_kd = sum(enemy_kds) / len(enemy_kds)
        avg_enemy_adr = sum(enemy_adrs) / len(enemy_adrs)
        # Use enemy K/D as proxy for strength (more reliable than rating which may be 0)
        enemy_strength = avg_enemy_kd

        my_rating = player.get("rating", 0)
        my_kd = player.get("kd", 0)
        my_adr = player.get("adr", 0)
        won = match.get("result") == "Sieg"

        if enemy_strength < 0.85:
            tier_key = "weak"
        elif enemy_strength <= 1.05:
            tier_key = "average"
        else:
            tier_key = "strong"

        t = tiers[tier_key]
        t["matches"] += 1
        t["wins"] += 1 if won else 0
        t["rating_sum"] += my_rating
        t["kd_sum"] += my_kd
        t["adr_sum"] += my_adr
        t["enemy_rating_sum"] += enemy_strength

        match_details.append({
            "date": match.get("date", ""),
            "map": match.get("map", "?"),
            "result": match.get("result", "?"),
            "my_rating": my_rating,
            "my_kd": my_kd,
            "enemy_kd": round(avg_enemy_kd, 2),
            "enemy_adr": round(avg_enemy_adr, 1),
            "tier": tier_key,
        })

    total_matches = sum(t["matches"] for t in tiers.values())
    if total_matches < 3:
        return {"has_data": False}

    # Compute tier averages
    tier_list = []
    for key in ["weak", "average", "strong"]:
        t = tiers[key]
        n = t["matches"]
        if n == 0:
            continue
        tier_list.append({
            "key": key,
            "label": t["label"],
            "threshold": t["threshold"],
            "color": t["color"],
            "matches": n,
            "win_rate": round(t["wins"] / n * 100, 1),
            "avg_rating": round(t["rating_sum"] / n, 2),
            "avg_kd": round(t["kd_sum"] / n, 2),
            "avg_adr": round(t["adr_sum"] / n, 1),
            "avg_enemy_kd": round(t["enemy_rating_sum"] / n, 2),
        })

    # Tips
    tips = []
    strong_t = tiers["strong"]
    weak_t = tiers["weak"]
    if strong_t["matches"] >= 3 and weak_t["matches"] >= 3:
        strong_kd = round(strong_t["kd_sum"] / strong_t["matches"], 2)
        weak_kd = round(weak_t["kd_sum"] / weak_t["matches"], 2)
        drop = round((1 - strong_kd / weak_kd) * 100, 1) if weak_kd > 0 else 0
        if drop > 15:
            tips.append({
                "type": "warning",
                "text": f"Gegen starke Gegner faellt dein K/D um {drop}% ({weak_kd} vs. {strong_kd}) — an Positioning und Utility arbeiten.",
            })
        elif drop < 5:
            tips.append({
                "type": "success",
                "text": f"Dein K/D bleibt stabil gegen starke Gegner ({strong_kd} vs. {weak_kd}) — du adaptierst gut.",
            })

    avg_t = tiers["average"]
    if strong_t["matches"] >= 3:
        strong_wr = round(strong_t["wins"] / strong_t["matches"] * 100, 1)
        if strong_wr >= 45:
            tips.append({"type": "success",
                         "text": f"Du gewinnst {strong_wr}% gegen starke Gegner — du kannst mit guten Spielern mithalten."})
        elif strong_wr < 30:
            tips.append({"type": "warning",
                         "text": f"Nur {strong_wr}% Winrate gegen starke Gegner — fokussiere auf Fundamentals (Utility, Positioning)."})

    return {
        "has_data": True,
        "total_matches": total_matches,
        "tiers": tier_list,
        "tips": tips[:4],
        "matches": match_details[:30],
    }


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


def _build_compare_achievements(exports: list[dict], cfg: dict) -> list[dict]:
    """Check which achievements have been unlocked (simple list for compare page)."""
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


def _build_session_insights(exports: list[dict]) -> dict:
    """Analyze optimal play time and fatigue patterns across all sessions."""
    if len(exports) < 5:
        return {"has_data": False}

    # ── Optimal Play Time ──
    # Group by time bracket: 6-12=Morgens, 12-17=Nachmittags, 17-21=Abends, 21-6=Nachts
    brackets = {
        "Morgens (6–12h)": (6, 12),
        "Nachmittags (12–17h)": (12, 17),
        "Abends (17–21h)": (17, 21),
        "Nachts (21–6h)": (21, 6),
    }

    bracket_data: dict[str, list[dict]] = {b: [] for b in brackets}
    hourly_data: dict[int, list[dict]] = {h: [] for h in range(24)}

    for e in exports:
        h = e.get("time_hour", -1)
        if h < 0:
            continue
        hourly_data[h].append(e)
        for bname, (start, end) in brackets.items():
            if start < end:
                if start <= h < end:
                    bracket_data[bname].append(e)
            else:  # wraps around midnight (21-6)
                if h >= start or h < end:
                    bracket_data[bname].append(e)

    playtime = []
    for bname in brackets:
        matches = bracket_data[bname]
        if not matches:
            continue
        n = len(matches)
        avg_rating = round(sum(m.get("rating", 0) for m in matches) / n, 2)
        avg_kd = round(sum(m.get("kd", 0) for m in matches) / n, 2)
        wins = sum(1 for m in matches if m.get("result") == "Sieg")
        wr = round(wins / n * 100, 1)
        playtime.append({
            "bracket": bname,
            "matches": n,
            "avg_rating": avg_rating,
            "avg_kd": avg_kd,
            "win_rate": wr,
        })

    # Find best/worst bracket
    best_time = max(playtime, key=lambda b: b["avg_rating"]) if playtime else None
    worst_time = min(playtime, key=lambda b: b["avg_rating"]) if playtime else None

    # Hourly breakdown for chart
    hourly = []
    for h in range(24):
        matches = hourly_data[h]
        if matches:
            hourly.append({
                "hour": h,
                "matches": len(matches),
                "avg_rating": round(sum(m.get("rating", 0) for m in matches) / len(matches), 2),
                "win_rate": round(sum(1 for m in matches if m.get("result") == "Sieg") / len(matches) * 100, 1),
            })
        else:
            hourly.append({"hour": h, "matches": 0, "avg_rating": 0, "win_rate": 0})

    # ── Fatigue Detection ──
    # Group by date, then analyze performance by game number within session
    by_date: dict[str, list[dict]] = {}
    for e in exports:
        date = e.get("date", "?")[:10]
        by_date.setdefault(date, []).append(e)

    # Collect ratings by game position (game 1, 2, 3, 4, 5+)
    pos_ratings: dict[int, list[float]] = {}
    for date, matches in by_date.items():
        if len(matches) < 2:
            continue
        # Sort by datetime within a day
        day_sorted = sorted(matches, key=lambda m: m.get("datetime", m.get("date", "")))
        for i, m in enumerate(day_sorted):
            pos = min(i + 1, 6)  # cap at position 6 ("6+")
            pos_ratings.setdefault(pos, []).append(m.get("rating", 0))

    fatigue_curve = []
    for pos in sorted(pos_ratings.keys()):
        ratings = pos_ratings[pos]
        if ratings:
            label = f"Spiel {pos}" if pos < 6 else "Spiel 6+"
            fatigue_curve.append({
                "position": pos,
                "label": label,
                "avg_rating": round(sum(ratings) / len(ratings), 2),
                "matches": len(ratings),
            })

    # Detect fatigue: compare game 1-2 avg with game 4+ avg
    early_games = [r for p, rs in pos_ratings.items() if p <= 2 for r in rs]
    late_games = [r for p, rs in pos_ratings.items() if p >= 4 for r in rs]
    fatigue_drop = None
    if early_games and late_games:
        early_avg = sum(early_games) / len(early_games)
        late_avg = sum(late_games) / len(late_games)
        drop = early_avg - late_avg
        if drop > 0.05:
            fatigue_drop = {
                "early_avg": round(early_avg, 2),
                "late_avg": round(late_avg, 2),
                "drop": round(drop, 2),
                "recommendation": f"Nach 3 Spielen sinkt dein Rating um {drop:.2f} — nimm dir eine Pause nach dem 3. Match",
            }

    # Optimal session length
    session_lengths: dict[int, list[float]] = {}
    for date, matches in by_date.items():
        n = len(matches)
        if n >= 1:
            avg_r = sum(m.get("rating", 0) for m in matches) / n
            wr = sum(1 for m in matches if m.get("result") == "Sieg") / n * 100
            session_lengths.setdefault(n, []).append(avg_r)

    length_stats = []
    for length in sorted(session_lengths.keys()):
        ratings = session_lengths[length]
        label = f"{length} Match{'es' if length != 1 else ''}"
        length_stats.append({
            "length": length,
            "label": label,
            "sessions": len(ratings),
            "avg_rating": round(sum(ratings) / len(ratings), 2),
        })

    optimal_length = max(length_stats, key=lambda l: l["avg_rating"]) if length_stats else None

    return {
        "has_data": True,
        "playtime": playtime,
        "best_time": best_time,
        "worst_time": worst_time,
        "hourly": hourly,
        "fatigue_curve": fatigue_curve,
        "fatigue_drop": fatigue_drop,
        "length_stats": length_stats,
        "optimal_length": optimal_length,
    }


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


def _build_round_narration_context(
    target_round: dict,
    round_num: int,
    timeline: list[dict],
    player: dict,
    match_info: dict,
) -> str:
    """Build a detailed text context for AI round narration."""
    player_name = player.get("name", "Spieler")
    map_name = match_info.get("map", "?")
    score_own = match_info.get("score_own", "?")
    score_enemy = match_info.get("score_enemy", "?")
    result = match_info.get("result", "?")

    # Current round score (rounds won before this round)
    rounds_won_before = sum(1 for r in timeline if r["round"] < round_num and r.get("won"))
    rounds_lost_before = round_num - 1 - rounds_won_before

    side = target_round.get("side", "?")
    won = target_round.get("won", False)
    kills = target_round.get("player_kills", 0)
    died = target_round.get("player_died", False)
    died_early = target_round.get("died_early", False)
    events = target_round.get("events", [])

    # Build event timeline
    event_lines = []
    for e in events:
        pct = e.get("pct", "?")
        if e["type"] == "kill":
            hs = " (Headshot)" if e.get("headshot") else ""
            event_lines.append(
                f"  [{pct}%] {player_name} toetet {e.get('target', '?')} "
                f"mit {e.get('weapon', '?')}{hs}"
            )
        elif e["type"] == "death":
            hs = " (Headshot)" if e.get("headshot") else ""
            event_lines.append(
                f"  [{pct}%] {player_name} stirbt durch {e.get('killer', '?')} "
                f"mit {e.get('weapon', '?')}{hs}"
            )
        elif e["type"] == "bomb_plant":
            who = player_name if e.get("is_self") else "Teammate"
            event_lines.append(f"  [{pct}%] {who} pflanzt die Bombe")
        elif e["type"] == "bomb_defuse":
            who = player_name if e.get("is_self") else "Teammate"
            event_lines.append(f"  [{pct}%] {who} entschaerft die Bombe")

    events_text = "\n".join(event_lines) if event_lines else "  Keine Events erfasst."

    # Previous round context
    prev_ctx = ""
    if round_num > 1:
        prev = next((r for r in timeline if r["round"] == round_num - 1), None)
        if prev:
            pw = "gewonnen" if prev.get("won") else "verloren"
            pk = prev.get("player_kills", 0)
            prev_ctx = f"Vorherige Runde (R{round_num - 1}): {pw}, {pk} Kills"

    # Next round context
    next_ctx = ""
    nxt = next((r for r in timeline if r["round"] == round_num + 1), None)
    if nxt:
        nw = "gewonnen" if nxt.get("won") else "verloren"
        next_ctx = f"Naechste Runde (R{round_num + 1}): {nw}"

    # Determine buy type from economy data if available
    economy = {}
    match_econ = target_round.get("buy_type", "")

    ctx = f"""=== RUNDEN-KONTEXT ===

Match: {map_name} — Endstand {score_own}:{score_enemy} ({result})
Spieler: {player_name}
Runde: {round_num} von {len(timeline)}
Spielstand vor dieser Runde: {rounds_won_before}:{rounds_lost_before}
Seite: {side}
{prev_ctx}

=== RUNDE {round_num} — EVENTS (chronologisch) ===
{events_text}

=== ERGEBNIS ===
Runde {"gewonnen" if won else "verloren"}
Kills: {kills}
Gestorben: {"Ja" + (" (frueh)" if died_early else "") if died else "Nein (ueberlebt)"}
{next_ctx}"""

    return ctx


def _build_match_narration_context(data: dict) -> str:
    """Build comprehensive match context for full-match AI narration."""
    player = data.get("player", {})
    match = data.get("match", {})
    timeline = data.get("round_timeline", [])
    economy = data.get("economy", {})
    duel_matrix = data.get("duel_matrix", [])

    player_name = player.get("name", "Spieler")
    map_name = match.get("map", "?")
    score_own = match.get("score_own", "?")
    score_enemy = match.get("score_enemy", "?")
    result = match.get("result", "?")
    total_rounds = match.get("total_rounds", len(timeline))

    # ── Player stats overview ──
    stats_block = f"""Spieler: {player_name}
Map: {map_name} — {score_own}:{score_enemy} ({result})
Rating: {player.get('rating', '?')} | K/D: {player.get('kd', '?')} ({player.get('kills', '?')}/{player.get('deaths', '?')}/{player.get('assists', '?')})
ADR: {player.get('adr', '?')} | HS%: {player.get('hs_pct', '?')}% | KAST: {player.get('kast_pct', '?')}%
Counter-Strafe: {player.get('counter_strafe_pct', '?')}% | Accuracy: {player.get('accuracy', '?')}%
Opening Duels: {player.get('opening_kills', 0)}W / {player.get('opening_deaths', 0)}L
Survival Rate: {player.get('survival_rate', '?')}%"""

    # Side split
    ss = player.get("side_split", {})
    if ss:
        stats_block += f"\nCT: {ss.get('ct_kills', 0)}K/{ss.get('ct_deaths', 0)}D | T: {ss.get('t_kills', 0)}K/{ss.get('t_deaths', 0)}D"

    # ── Round-by-round compact timeline ──
    round_lines = []
    for r in timeline:
        rn = r["round"]
        side = r.get("side", "?")
        won = "W" if r.get("won") else "L"
        kills = r.get("player_kills", 0)
        died = r.get("player_died", False)

        events_short = []
        for e in r.get("events", []):
            pct = e.get("pct", "?")
            if e["type"] == "kill":
                hs = "HS" if e.get("headshot") else ""
                events_short.append(f"Kill {e.get('target', '?')} ({e.get('weapon', '?')}{' '+hs if hs else ''}) @{pct}%")
            elif e["type"] == "death":
                hs = "HS" if e.get("headshot") else ""
                events_short.append(f"Tod durch {e.get('killer', '?')} ({e.get('weapon', '?')}{' '+hs if hs else ''}) @{pct}%")
            elif e["type"] == "bomb_plant" and e.get("is_self"):
                events_short.append(f"Bombe gelegt @{pct}%")
            elif e["type"] == "bomb_defuse" and e.get("is_self"):
                events_short.append(f"Bombe entschaerft @{pct}%")

        ev_str = " | ".join(events_short) if events_short else "keine Events"
        early = " [FRUEH]" if r.get("died_early") else ""
        round_lines.append(f"R{rn} {side} {won} {kills}K{early}: {ev_str}")

    # Split into halves
    half1 = [l for l in round_lines if int(l.split()[0][1:]) <= 12]
    half2 = [l for l in round_lines if int(l.split()[0][1:]) > 12]

    # ── Key rounds identification ──
    key_rounds = []
    for r in timeline:
        kills = r.get("player_kills", 0)
        died_early = r.get("died_early", False)
        won = r.get("won", False)
        has_bomb = any(e["type"] in ("bomb_plant", "bomb_defuse") and e.get("is_self") for e in r.get("events", []))

        if kills >= 3:
            key_rounds.append(f"R{r['round']}: {kills}K HIGHLIGHT")
        elif has_bomb and won:
            key_rounds.append(f"R{r['round']}: Bombe + Sieg")
        elif kills == 0 and died_early and not won:
            key_rounds.append(f"R{r['round']}: Frueh gestorben, 0 Impact — Runde verloren")

    # ── Economy summary ──
    econ_lines = []
    for buy_type in ["pistol", "eco", "force", "fullbuy"]:
        e = economy.get(buy_type, {})
        if e and e.get("rounds", 0) > 0:
            econ_lines.append(f"{buy_type.title()}: {e.get('rounds',0)} Runden, K/D {e.get('kd','?')}, WR {e.get('win_rate','?')}%, ADR {e.get('adr','?')}")

    # ── Top duels ──
    duel_lines = []
    if duel_matrix:
        sorted_duels = sorted(duel_matrix, key=lambda d: d.get("kills", 0) + d.get("deaths", 0), reverse=True)[:5]
        for d in sorted_duels:
            duel_lines.append(f"{d.get('name','?')}: {d.get('kills',0)}K/{d.get('deaths',0)}D (K/D {d.get('kd','?')})")

    # ── Momentum (streaks) ──
    streaks = []
    current_streak = 0
    streak_type = None
    for r in timeline:
        w = r.get("won", False)
        if streak_type is None:
            streak_type = w
            current_streak = 1
        elif w == streak_type:
            current_streak += 1
        else:
            if current_streak >= 3:
                label = "Siegesserie" if streak_type else "Niederlagenserie"
                start_r = r["round"] - current_streak
                streaks.append(f"{label}: R{start_r}-R{r['round']-1} ({current_streak} Runden)")
            streak_type = w
            current_streak = 1
    if current_streak >= 3:
        label = "Siegesserie" if streak_type else "Niederlagenserie"
        start_r = timeline[-1]["round"] - current_streak + 1
        streaks.append(f"{label}: R{start_r}-R{timeline[-1]['round']} ({current_streak} Runden)")

    ctx = f"""=== MATCH-KONTEXT ===

{stats_block}

=== ERSTE HAELFTE (R1-R12) ===
{chr(10).join(half1)}

=== ZWEITE HAELFTE (R13+) ===
{chr(10).join(half2)}

=== SCHLUESSELRUNDEN ===
{chr(10).join(key_rounds) if key_rounds else "Keine besonderen Highlight/Throw-Runden"}

=== MOMENTUM ===
{chr(10).join(streaks) if streaks else "Keine laengeren Serien"}

=== ECONOMY ===
{chr(10).join(econ_lines) if econ_lines else "Keine Economy-Daten"}

=== TOP-DUELLE ===
{chr(10).join(duel_lines) if duel_lines else "Keine Duel-Daten"}

=== GESAMT: {total_rounds} Runden ==="""

    return ctx


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

    demos = sorted(folder_path.glob("*.dem"), key=lambda p: p.name, reverse=True)
    analyzed_files = _get_analyzed_filenames(cfg)
    new_demos = [d for d in demos if d.name not in analyzed_files]

    from cs2_coach.parser import _read_demo_timestamp
    recent = []
    for d in demos[:10]:
        recent.append({
            "name": d.name,
            "path": str(d),
            "size_mb": round(d.stat().st_size / (1024 * 1024), 1),
            "date": _read_demo_timestamp(d).strftime("%Y-%m-%d %H:%M"),
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
    sorted_months = sorted(monthly.keys(), reverse=True)
    for i, mk in enumerate(sorted_months):
        mm = mk[-2:]
        label = f"{month_names.get(mm, mm)} {mk[:4]}"
        summary = _summarize(monthly[mk], label, mk)
        # Delta to previous month
        if i + 1 < len(sorted_months):
            prev = _summarize(monthly[sorted_months[i + 1]], "", "")
            summary["delta"] = {
                "rating": round(summary["avg_rating"] - prev["avg_rating"], 2),
                "kd": round(summary["avg_kd"] - prev["avg_kd"], 2),
                "adr": round(summary["avg_adr"] - prev["avg_adr"], 1),
                "hs": round(summary["avg_hs"] - prev["avg_hs"], 1),
                "kast": round(summary["avg_kast"] - prev["avg_kast"], 1),
                "win_rate": round(summary["win_rate"] - prev["win_rate"], 1),
                "prev_label": prev["label"] if prev["label"] else sorted_months[i + 1],
            }
        periods.append(summary)

    # Weekly summaries (most recent first)
    sorted_weeks = sorted(weekly.keys(), reverse=True)[:8]
    weekly_periods = []
    for i, wk in enumerate(sorted_weeks):
        label = f"KW {wk.split('-W')[1]} / {wk.split('-W')[0]}"
        summary = _summarize(weekly[wk], label, wk)
        if i + 1 < len(sorted_weeks):
            prev_wk = sorted_weeks[i + 1]
            prev = _summarize(weekly[prev_wk], "", "")
            summary["delta"] = {
                "rating": round(summary["avg_rating"] - prev["avg_rating"], 2),
                "kd": round(summary["avg_kd"] - prev["avg_kd"], 2),
                "adr": round(summary["avg_adr"] - prev["avg_adr"], 1),
                "hs": round(summary["avg_hs"] - prev["avg_hs"], 1),
                "kast": round(summary["avg_kast"] - prev["avg_kast"], 1),
                "win_rate": round(summary["win_rate"] - prev["win_rate"], 1),
                "prev_label": prev["label"] if prev["label"] else prev_wk,
            }
        weekly_periods.append(summary)

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
    eco_s = summary.get("eco", {})
    force_s = summary.get("force", {})
    full_s = summary.get("fullbuy", {})
    pistol_s = summary.get("pistol", {})

    # Pistol rounds
    if pistol_s:
        if pistol_s["win_rate"] >= 48:
            tips.append({"type": "positive", "text": f"Starke Pistolrunden: {pistol_s['win_rate']}% Win-Rate — oekonomischer Vorteil fuers Team."})
        elif pistol_s["win_rate"] < 40:
            tips.append({"type": "warning", "text": f"Pistolrunden-WR nur {pistol_s['win_rate']}% — trainiere USP/Glock-Aim und Pistol-Setups."})

    # Eco rounds
    if eco_s:
        if eco_s["kd"] >= 0.7:
            tips.append({"type": "positive", "text": f"Eco K/D {eco_s['kd']} — du machst auch ohne Equipment Impact."})
        elif eco_s["kd"] < 0.5:
            tips.append({"type": "warning", "text": f"Eco K/D nur {eco_s['kd']} — in Sparrunden passiver spielen und auf Picks lauern."})

    # Force buys
    if force_s:
        if force_s["win_rate"] >= 42:
            tips.append({"type": "positive", "text": f"Force-Buys effektiv: {force_s['win_rate']}% Win-Rate — gute Kaufentscheidungen."})
        elif force_s["win_rate"] < 30:
            tips.append({"type": "warning", "text": f"Force-Buys nur {force_s['win_rate']}% WR — Full-Save koennte effektiver sein."})

    # Full buys
    if full_s:
        if full_s["win_rate"] < 52:
            tips.append({"type": "warning", "text": f"Full-Buy WR nur {full_s['win_rate']}% — Utility-Einsatz und Teamplay verbessern."})
        elif full_s["win_rate"] >= 58:
            tips.append({"type": "positive", "text": f"Starke Full-Buys: {full_s['win_rate']}% Win-Rate — Equipment wird effektiv genutzt."})

    # Cross-category insights
    if force_s and full_s and force_s.get("kd", 0) > full_s.get("kd", 0) * 0.9:
        tips.append({"type": "info", "text": f"Force-Buy K/D ({force_s['kd']}) fast wie Full-Buy ({full_s['kd']}) — du spielst mit wenig Equipment aehnlich gut."})

    if eco_s and full_s and eco_s.get("total_rounds", 0) > full_s.get("total_rounds", 0) * 0.3:
        eco_ratio = round(eco_s["total_rounds"] / max(full_s["total_rounds"], 1) * 100)
        tips.append({"type": "info", "text": f"Eco-Anteil {eco_ratio}% der Full-Buy-Runden — Pistolrunden gewinnen spart Economy-Stress."})

    # Best/worst comparison insight (always show)
    if best_cat and worst_cat and best_cat != worst_cat:
        tips.append({"type": "info", "text": f"Staerkste Kategorie: {summary[best_cat]['label']} ({summary[best_cat]['win_rate']}% WR) — Schwaeche: {summary[worst_cat]['label']} ({summary[worst_cat]['win_rate']}% WR)"})

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

        # CT-Side performance trend
        last5_ct_kills = sum(e.get("ct_kills", 0) for e in last5)
        last5_ct_deaths = sum(e.get("ct_deaths", 0) for e in last5)
        all_ct_kills = sum(e.get("ct_kills", 0) for e in exports)
        all_ct_deaths = sum(e.get("ct_deaths", 0) for e in exports)
        last5_ct_kd = last5_ct_kills / max(last5_ct_deaths, 1)
        all_ct_kd = all_ct_kills / max(all_ct_deaths, 1)
        if all_ct_kd > 0 and last5_ct_kd < all_ct_kd * 0.75:
            alerts.append({"type": "warning", "icon": "shield-alert",
                           "text": f"CT-Side schwaecher: K/D {last5_ct_kd:.2f} (letzte 5) vs. {all_ct_kd:.2f} (gesamt)"})
        elif all_ct_kd > 0 and last5_ct_kd > all_ct_kd * 1.25:
            alerts.append({"type": "success", "icon": "shield-check",
                           "text": f"CT-Side staerker: K/D {last5_ct_kd:.2f} (letzte 5) vs. {all_ct_kd:.2f} (gesamt)"})

        # T-Side performance trend
        last5_t_kills = sum(e.get("t_kills", 0) for e in last5)
        last5_t_deaths = sum(e.get("t_deaths", 0) for e in last5)
        all_t_kills = sum(e.get("t_kills", 0) for e in exports)
        all_t_deaths = sum(e.get("t_deaths", 0) for e in exports)
        last5_t_kd = last5_t_kills / max(last5_t_deaths, 1)
        all_t_kd = all_t_kills / max(all_t_deaths, 1)
        if all_t_kd > 0 and last5_t_kd < all_t_kd * 0.75:
            alerts.append({"type": "warning", "icon": "swords",
                           "text": f"T-Side schwaecher: K/D {last5_t_kd:.2f} (letzte 5) vs. {all_t_kd:.2f} (gesamt)"})
        elif all_t_kd > 0 and last5_t_kd > all_t_kd * 1.25:
            alerts.append({"type": "success", "icon": "swords",
                           "text": f"T-Side staerker: K/D {last5_t_kd:.2f} (letzte 5) vs. {all_t_kd:.2f} (gesamt)"})

        # Crosshair placement trend
        last5_cp = last5_avg("crosshair_placement")
        all_cp = all_avg("crosshair_placement")
        if all_cp > 0 and last5_cp > 0:
            cp_diff = last5_cp - all_cp
            if cp_diff > 3:  # higher degrees = worse
                alerts.append({"type": "warning", "icon": "crosshair",
                               "text": f"Crosshair Placement verschlechtert: {last5_cp:.1f}° (letzte 5) vs. {all_cp:.1f}° (gesamt)"})
            elif cp_diff < -3:
                alerts.append({"type": "success", "icon": "crosshair",
                               "text": f"Crosshair Placement verbessert: {last5_cp:.1f}° (letzte 5) vs. {all_cp:.1f}° (gesamt)"})

        # Survival rate trend
        last5_sr = last5_avg("survival_rate")
        all_sr = all_avg("survival_rate")
        sr_diff = last5_sr - all_sr
        if sr_diff < -5:
            alerts.append({"type": "warning", "icon": "heart-crack",
                           "text": f"Survival Rate sinkt: {last5_sr:.1f}% (letzte 5) vs. {all_sr:.1f}% (gesamt) — mehr Tode pro Match"})

        # Map-specific losing streak (3+ losses on same map recently)
        last10 = sorted(exports, key=lambda e: e.get("date", ""))[-10:]
        map_results: dict[str, list[str]] = {}
        for e in last10:
            m = e.get("map", "")
            if m:
                map_results.setdefault(m, []).append(e.get("result", ""))
        for m, results in map_results.items():
            # Check tail of results for consecutive losses
            consecutive_losses = 0
            for r in reversed(results):
                if r == "Niederlage":
                    consecutive_losses += 1
                else:
                    break
            if consecutive_losses >= 3:
                alerts.append({"type": "warning", "icon": "map-pin",
                               "text": f"{consecutive_losses} Niederlagen in Folge auf {m} — Map meiden oder gezielt trainieren"})

        # Consistency alert: rating variance increasing
        if n >= 10:
            last5_ratings = [e.get("rating", 0) for e in last5]
            all_ratings = [e.get("rating", 0) for e in exports]
            last5_var = sum((r - sum(last5_ratings) / 5) ** 2 for r in last5_ratings) / 5
            all_var = sum((r - sum(all_ratings) / n) ** 2 for r in all_ratings) / n
            if last5_var > all_var * 2 and last5_var > 0.02:
                alerts.append({"type": "info", "icon": "activity",
                               "text": f"Inkonsistent: Dein Rating schwankt stark in den letzten 5 Spielen — versuche konstanter zu spielen"})

        # Win streak / loss streak
        if streak >= 3 and streak_type == "Sieg":
            alerts.append({"type": "success", "icon": "flame",
                           "text": f"Hot Streak! {streak} Siege in Folge"})
        elif streak >= 3 and streak_type == "Niederlage":
            alerts.append({"type": "warning", "icon": "alert-triangle",
                           "text": f"{streak} Niederlagen in Folge — Zeit fuer eine Pause?"})

    # ── Aim Rating (Leetify-style composite 0-100) ──
    # Components: Crosshair Placement, Counter-Strafe, HS%, Accuracy, Spray Control
    aim_components = {}

    # Crosshair Placement: 0-5° = excellent, 5-15° = good, 15-30° = avg, 30+ = poor
    cp_score = round(_norm(avg_cp, 4, 30, lower_better=True), 1) if avg_cp > 0 else 50
    aim_components["crosshair"] = {"label": "Crosshair Placement", "value": f"{avg_cp}°", "score": cp_score}

    # Counter-Strafe: 90%+ = elite, 75% = good, 60% = avg, below = poor
    cs_score = round(_norm(avg_cs, 50, 95), 1)
    aim_components["counter_strafe"] = {"label": "Counter-Strafe", "value": f"{avg_cs}%", "score": cs_score}

    # Headshot %: 60%+ = elite, 45% = good, 30% = avg, below = poor
    hs_score = round(_norm(avg_hs, 20, 60), 1)
    aim_components["hs_pct"] = {"label": "Headshot %", "value": f"{avg_hs}%", "score": hs_score}

    # Accuracy: 25%+ = elite, 18% = good, 12% = avg, below = poor
    avg_acc = round(sum(e.get("accuracy", 0) for e in exports) / n, 1) if n > 0 else 0
    acc_score = round(_norm(avg_acc, 8, 28), 1)
    aim_components["accuracy"] = {"label": "Accuracy", "value": f"{avg_acc}%", "score": acc_score}

    # Spray Control (burst ratio): higher burst_kills ratio = better trigger discipline
    total_burst = sum(e.get("burst_kills", 0) for e in exports)
    total_spray = sum(e.get("spray_kills", 0) for e in exports)
    burst_ratio = total_burst / max(total_burst + total_spray, 1) * 100
    spray_score = round(_norm(burst_ratio, 30, 85), 1)
    aim_components["spray"] = {"label": "Spray Control", "value": f"{round(burst_ratio)}%", "score": spray_score}

    # Composite Aim Rating (weighted average)
    aim_rating = round(
        cp_score * 0.25 +      # Crosshair placement is king
        cs_score * 0.20 +      # Counter-strafe very important
        hs_score * 0.25 +      # HS% shows raw aim
        acc_score * 0.15 +     # Accuracy matters
        spray_score * 0.15,    # Spray discipline
        1
    )

    # Compute Aim Rating trend (last 5 vs overall)
    if len(last5) >= 3:
        l5_cp = sum(e.get("crosshair_placement", 0) for e in last5) / 5
        l5_cs = sum(e.get("counter_strafe", 0) for e in last5) / 5
        l5_hs = sum(e.get("hs_pct", 0) for e in last5) / 5
        l5_acc = sum(e.get("accuracy", 0) for e in last5) / 5
        l5_aim = round(
            _norm(l5_cp, 4, 30, lower_better=True) * 0.25 +
            _norm(l5_cs, 50, 95) * 0.20 +
            _norm(l5_hs, 20, 60) * 0.25 +
            _norm(l5_acc, 8, 28) * 0.15 +
            spray_score * 0.15,  # spray doesn't change much match-to-match
            1
        )
        aim_trend = round(l5_aim - aim_rating, 1)
    else:
        l5_aim = aim_rating
        aim_trend = 0

    # Rank label for aim rating
    if aim_rating >= 85:
        aim_rank = "Elite"
    elif aim_rating >= 70:
        aim_rank = "Advanced"
    elif aim_rating >= 55:
        aim_rank = "Intermediate"
    elif aim_rating >= 40:
        aim_rank = "Developing"
    else:
        aim_rank = "Beginner"

    aim_data = {
        "rating": aim_rating,
        "rank": aim_rank,
        "trend": aim_trend,
        "last5": l5_aim,
        "components": aim_components,
    }

    # ── Formkurve ──
    chronological = sorted(exports, key=lambda e: e.get("date", ""))
    last20 = chronological[-20:]
    form_sparkline = [{"rating": round(e.get("rating", 0), 2), "map": e.get("map", "?"),
                       "result": e.get("result", "?"), "date": e.get("date", "?")} for e in last20]
    form_curve = {
        "last1": round(chronological[-1].get("rating", 0), 2) if chronological else 0,
        "last5": round(sum(e.get("rating", 0) for e in chronological[-5:]) / min(n, 5), 2),
        "last20": round(sum(e.get("rating", 0) for e in last20) / len(last20), 2) if last20 else 0,
        "all_time": avg_rating,
    }

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
        "form_sparkline": form_sparkline,
        "form_curve": form_curve,
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
        "aim": aim_data,
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


def _build_map_mastery(exports: list[dict], map_stats: list[dict]) -> dict:
    """Build per-map mastery profiles with radar charts."""
    if not map_stats:
        return {"has_data": False}

    by_map: dict[str, list[dict]] = {}
    for e in exports:
        by_map.setdefault(e.get("map", "?"), []).append(e)

    def _safe_avg(lst):
        return sum(lst) / len(lst) if lst else 0

    global_avg = {
        "rating": _safe_avg([e.get("rating", 0) for e in exports if e.get("rating")]),
        "adr": _safe_avg([e.get("adr", 0) for e in exports if e.get("adr")]),
        "kd": _safe_avg([e.get("kd", 0) for e in exports if e.get("kd")]),
        "kast": _safe_avg([e.get("kast", 0) for e in exports if e.get("kast")]),
    }

    def _norm_score(val, metric):
        ranges = {
            "rating": (0.5, 1.4), "adr": (40, 140), "kd": (0.4, 2.0),
            "kast": (40, 90), "crosshair": (30, 3),
            "counter_strafe": (50, 95), "utility": (0.3, 3.0),
            "win_rate": (0, 100),
        }
        lo, hi = ranges.get(metric, (0, 100))
        if metric == "crosshair":
            pct = (lo - val) / (lo - hi) * 100 if lo != hi else 50
        else:
            pct = (val - lo) / (hi - lo) * 100 if hi != lo else 50
        return max(0, min(100, round(pct)))

    radar_labels = ["Win-Rate", "Rating", "ADR", "K/D", "KAST", "Crosshair", "Utility"]

    maps_result = []
    for ms in map_stats:
        map_name = ms["map"]
        map_exports = by_map.get(map_name, [])
        n = len(map_exports)
        if n == 0:
            continue

        avg_rating = _safe_avg([e.get("rating", 0) for e in map_exports if e.get("rating")])
        avg_adr = _safe_avg([e.get("adr", 0) for e in map_exports if e.get("adr")])
        avg_kd = _safe_avg([e.get("kd", 0) for e in map_exports if e.get("kd")])
        avg_kast = _safe_avg([e.get("kast", 0) for e in map_exports if e.get("kast")])
        avg_cp = _safe_avg([e.get("crosshair_placement", 0) for e in map_exports if e.get("crosshair_placement")])
        avg_cs = _safe_avg([e.get("counter_strafe", 0) for e in map_exports if e.get("counter_strafe")])
        avg_util = _safe_avg([e.get("utility_per_round", 0) for e in map_exports if e.get("utility_per_round")])

        clutch_wins = sum(e.get("clutch_wins", 0) for e in map_exports)
        clutch_attempts = sum(e.get("clutch_attempts", 0) for e in map_exports)
        clutch_rate = round(clutch_wins / clutch_attempts * 100, 1) if clutch_attempts else 0

        od_wins = sum(e.get("opening_kills", 0) for e in map_exports)
        od_losses = sum(e.get("opening_deaths", 0) for e in map_exports)
        od_total = od_wins + od_losses
        opening_wr = round(od_wins / od_total * 100, 1) if od_total else 0

        radar_scores = [
            _norm_score(ms["win_rate"], "win_rate"),
            _norm_score(avg_rating, "rating"),
            _norm_score(avg_adr, "adr"),
            _norm_score(avg_kd, "kd"),
            _norm_score(avg_kast, "kast"),
            _norm_score(avg_cp, "crosshair"),
            _norm_score(avg_util, "utility"),
        ]

        mastery = round(
            radar_scores[0] * 0.25 +
            radar_scores[1] * 0.20 +
            radar_scores[2] * 0.15 +
            radar_scores[3] * 0.15 +
            radar_scores[4] * 0.10 +
            radar_scores[5] * 0.08 +
            radar_scores[6] * 0.07
        )

        deltas = {
            "rating": round(avg_rating - global_avg["rating"], 2),
            "adr": round(avg_adr - global_avg["adr"], 1),
            "kd": round(avg_kd - global_avg["kd"], 2),
            "kast": round(avg_kast - global_avg["kast"], 1),
        }

        recent = map_exports[:5]
        recent_rating = _safe_avg([e.get("rating", 0) for e in recent if e.get("rating")])
        trend = round(recent_rating - avg_rating, 2) if recent else 0

        dim_names = ["Win-Rate", "Rating", "ADR", "K/D", "KAST", "Crosshair", "Utility"]
        weakest_idx = radar_scores.index(min(radar_scores))
        weakest_dim = dim_names[weakest_idx]

        maps_result.append({
            "map": map_name,
            "matches": n,
            "mastery": mastery,
            "radar_scores": radar_scores,
            "win_rate": ms["win_rate"],
            "avg_rating": round(avg_rating, 2),
            "avg_adr": round(avg_adr, 1),
            "avg_kd": round(avg_kd, 2),
            "avg_kast": round(avg_kast, 1),
            "avg_crosshair": round(avg_cp, 1),
            "avg_counter_strafe": round(avg_cs, 0),
            "avg_utility": round(avg_util, 2),
            "clutch_rate": clutch_rate,
            "clutch_wins": clutch_wins,
            "clutch_attempts": clutch_attempts,
            "opening_wr": opening_wr,
            "opening_wins": od_wins,
            "opening_total": od_total,
            "deltas": deltas,
            "trend": trend,
            "weakest_dim": weakest_dim,
        })

    maps_result.sort(key=lambda x: -x["mastery"])

    return {
        "has_data": True,
        "maps": maps_result,
        "radar_labels": radar_labels,
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


# ── Nemesis ────────────────────────────────────────────────
def _build_nemesis(cfg: dict) -> dict:
    """Aggregate duel data across all matches for cross-match enemy analysis."""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    # Aggregate by steam_id
    agg: dict[str, dict] = {}
    match_count = 0

    for f in sorted(export_dir.glob("*_coach.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        duels = data.get("duel_matrix", [])
        match_info = data.get("match", {})
        if not duels:
            continue
        match_count += 1

        # Build teammate set to filter them out
        player = data.get("player", {})
        target_sid = player.get("steam_id", "")
        scoreboard = data.get("scoreboard", [])
        teammate_sids = {target_sid} if target_sid else set()
        target_idx = next((i for i, s in enumerate(scoreboard) if s.get("is_target")), None)
        if target_idx is not None:
            team_start = 0 if target_idx < 5 else 5
            for i in range(team_start, team_start + 5):
                if i < len(scoreboard):
                    sid_sb = scoreboard[i].get("steam_id", "")
                    if sid_sb:
                        teammate_sids.add(sid_sb)

        for d in duels:
            sid = d.get("steam_id", d.get("name", ""))
            if not sid or sid in teammate_sids:
                continue
            if sid not in agg:
                agg[sid] = {
                    "name": d["name"], "steam_id": sid,
                    "kills": 0, "deaths": 0, "hs_kills": 0, "hs_deaths": 0,
                    "matches": 0, "weapons": {}, "maps": {},
                    "results": [],  # track win/loss when facing this enemy
                }
            entry = agg[sid]
            entry["name"] = d["name"]  # update to latest name
            entry["kills"] += d.get("kills", 0)
            entry["deaths"] += d.get("deaths", 0)
            entry["hs_kills"] += d.get("hs_kills", 0)
            entry["hs_deaths"] += d.get("hs_deaths", 0)
            entry["matches"] += 1
            for w in d.get("top_weapons", []):
                entry["weapons"][w] = entry["weapons"].get(w, 0) + 1
            map_name = match_info.get("map", "?")
            entry["maps"][map_name] = entry["maps"].get(map_name, 0) + 1
            entry["results"].append(match_info.get("result", "?"))

    if not agg:
        return {"has_data": False}

    # Build ranked lists
    enemies = []
    for sid, e in agg.items():
        total = e["kills"] + e["deaths"]
        kd = round(e["kills"] / e["deaths"], 2) if e["deaths"] > 0 else float(e["kills"])
        # Threat score: weighted by encounters and how badly they beat you
        reverse_kd = e["deaths"] / max(e["kills"], 1)
        threat = round(reverse_kd * min(e["matches"], 5) * (e["deaths"] / max(total, 1)), 2)
        dominance = round((e["kills"] / max(total, 1)) * 100, 1)
        top_weapons = sorted(e["weapons"].items(), key=lambda x: -x[1])[:3]
        top_maps = sorted(e["maps"].items(), key=lambda x: -x[1])[:3]
        wins = sum(1 for r in e["results"] if r == "Sieg")
        losses = sum(1 for r in e["results"] if r == "Niederlage")

        enemies.append({
            "name": e["name"], "steam_id": sid,
            "kills": e["kills"], "deaths": e["deaths"],
            "kd": kd, "total_duels": total,
            "hs_kills": e["hs_kills"], "hs_deaths": e["hs_deaths"],
            "matches": e["matches"], "threat": threat, "dominance": dominance,
            "top_weapons": [w[0] for w in top_weapons],
            "top_maps": [m[0] for m in top_maps],
            "wins": wins, "losses": losses,
        })

    # Sort categories — use total_duels >= 4 to filter out trivial encounters
    min_duels = 4
    nemeses = sorted([e for e in enemies if e["kd"] < 1.0 and e["total_duels"] >= min_duels],
                     key=lambda x: x["threat"], reverse=True)[:10]
    victims = sorted([e for e in enemies if e["kd"] > 1.0 and e["total_duels"] >= min_duels],
                     key=lambda x: (x["kd"] * x["total_duels"]), reverse=True)[:10]
    frequent = sorted(enemies, key=lambda x: x["total_duels"], reverse=True)[:10]
    all_sorted = sorted(enemies, key=lambda x: x["total_duels"], reverse=True)

    # Stats summary
    total_kills = sum(e["kills"] for e in enemies)
    total_deaths = sum(e["deaths"] for e in enemies)
    avg_kd = round(total_kills / max(total_deaths, 1), 2)

    return {
        "has_data": True,
        "match_count": match_count,
        "total_enemies": len(enemies),
        "total_kills": total_kills,
        "total_deaths": total_deaths,
        "avg_kd": avg_kd,
        "nemeses": nemeses,
        "victims": victims,
        "frequent": frequent,
        "all": all_sorted,
    }


# ── Opponent Scouting (Gegner-Vorhersage) ─────────────────
def _build_scout_data(cfg: dict) -> dict:
    """Build deep per-opponent scouting intel for pre-match preparation."""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    agg: dict[str, dict] = {}
    match_count = 0

    for f in sorted(export_dir.glob("*_coach.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        duels = data.get("duel_matrix", [])
        match_info = data.get("match", {})
        kill_positions = data.get("kill_positions", [])
        if not duels:
            continue
        match_count += 1

        # Build teammate set to filter them out
        player = data.get("player", {})
        target_sid = player.get("steam_id", "")
        scoreboard = data.get("scoreboard", [])
        teammate_sids = {target_sid} if target_sid else set()
        target_idx = next((i for i, s in enumerate(scoreboard) if s.get("is_target")), None)
        if target_idx is not None:
            team_start = 0 if target_idx < 5 else 5
            for i in range(team_start, team_start + 5):
                if i < len(scoreboard):
                    sid_sb = scoreboard[i].get("steam_id", "")
                    if sid_sb:
                        teammate_sids.add(sid_sb)

        map_name = match_info.get("map", "?")
        match_result = match_info.get("result", "?")
        match_date = match_info.get("date", "?")

        for d in duels:
            sid = d.get("steam_id", d.get("name", ""))
            if not sid or sid in teammate_sids:
                continue
            if sid not in agg:
                agg[sid] = {
                    "name": d["name"], "steam_id": sid,
                    "kills": 0, "deaths": 0, "hs_kills": 0, "hs_deaths": 0,
                    "matches": 0, "weapons_them": {}, "weapons_you": {},
                    "maps": {}, "results": [], "dates": [],
                    "positions": {},  # map_name -> {kills: [], deaths: []}
                }
            entry = agg[sid]
            entry["name"] = d["name"]
            entry["kills"] += d.get("kills", 0)
            entry["deaths"] += d.get("deaths", 0)
            entry["hs_kills"] += d.get("hs_kills", 0)
            entry["hs_deaths"] += d.get("hs_deaths", 0)
            entry["matches"] += 1
            entry["dates"].append(match_date)

            # Weapons they used to kill you (from top_weapons in duel_matrix)
            for w in d.get("top_weapons", []):
                entry["weapons_them"][w] = entry["weapons_them"].get(w, 0) + 1

            # Map tracking with win/loss
            if map_name not in entry["maps"]:
                entry["maps"][map_name] = {"played": 0, "wins": 0, "losses": 0}
            entry["maps"][map_name]["played"] += 1
            if match_result == "Sieg":
                entry["maps"][map_name]["wins"] += 1
            elif match_result == "Niederlage":
                entry["maps"][map_name]["losses"] += 1
            entry["results"].append(match_result)

        # Collect per-opponent kill positions
        for kp in kill_positions:
            enemy_name = kp.get("e", "")
            kp_type = kp.get("t", "")
            if not enemy_name:
                continue
            # Find matching opponent by name
            for sid, entry in agg.items():
                if entry["name"] == enemy_name:
                    if map_name not in entry["positions"]:
                        entry["positions"][map_name] = {"kills": [], "deaths": []}
                    pos = {
                        "x": kp.get("x", 0), "y": kp.get("y", 0),
                        "ex": kp.get("ex", 0), "ey": kp.get("ey", 0),
                        "w": kp.get("w", ""), "hs": kp.get("hs", False),
                    }
                    if kp_type == "k":
                        entry["positions"][map_name]["kills"].append(pos)
                    elif kp_type == "d":
                        entry["positions"][map_name]["deaths"].append(pos)
                    break

    if not agg:
        return {"has_data": False}

    # Build opponent profiles with tactical tips
    opponents = []
    for sid, e in agg.items():
        total = e["kills"] + e["deaths"]
        if total < 2:
            continue
        kd = round(e["kills"] / max(e["deaths"], 1), 2)
        hs_pct_you = round(e["hs_kills"] / max(e["kills"], 1) * 100, 1)
        hs_pct_them = round(e["hs_deaths"] / max(e["deaths"], 1) * 100, 1)
        win_rate = round(sum(1 for r in e["results"] if r == "Sieg") / max(len(e["results"]), 1) * 100, 1)

        # Threat classification
        if e["deaths"] > e["kills"] + 2 and total >= 6:
            threat = "nemesis"
        elif e["kills"] > e["deaths"] + 2 and total >= 6:
            threat = "victim"
        else:
            threat = "even"

        # Top weapons they use against you
        top_weapons_them = sorted(e["weapons_them"].items(), key=lambda x: -x[1])[:5]

        # Map stats
        map_stats = []
        for m, ms in sorted(e["maps"].items(), key=lambda x: -x[1]["played"]):
            map_stats.append({"name": m, "played": ms["played"], "wins": ms["wins"], "losses": ms["losses"]})

        # Last seen
        valid_dates = [d for d in e["dates"] if d != "?"]
        last_seen = sorted(valid_dates, reverse=True)[0] if valid_dates else "?"

        # Position counts per map (for visualization)
        pos_summary = {}
        for m, p in e["positions"].items():
            pos_summary[m] = {"kills": len(p["kills"]), "deaths": len(p["deaths"])}

        # ── Generate tactical tips ──
        tips = []

        # AWP warning
        awp_count = e["weapons_them"].get("awp", 0)
        total_weapon_uses = sum(e["weapons_them"].values()) if e["weapons_them"] else 1
        if awp_count > 0 and awp_count / max(total_weapon_uses, 1) > 0.3:
            tips.append({"type": "warning", "icon": "crosshair",
                         "text": f"AWP-Spieler — {round(awp_count/total_weapon_uses*100)}% seiner Kills mit AWP. Smokes und Off-Angles vorbereiten."})

        # Headshot machine
        if hs_pct_them >= 55 and e["deaths"] >= 4:
            tips.append({"type": "warning", "icon": "target",
                         "text": f"Headshot-Maschine ({hs_pct_them}% HS gegen dich) — nicht wide-peeken, Jiggle-Peeks nutzen."})

        # You dominate them
        if kd >= 1.5 and total >= 6:
            tips.append({"type": "success", "icon": "trending-up",
                         "text": f"Du dominierst ({kd} K/D, {e['kills']}K/{e['deaths']}D) — selbstbewusst spielen."})

        # They dominate you
        if kd <= 0.7 and total >= 6:
            tips.append({"type": "danger", "icon": "alert-triangle",
                         "text": f"Gefaehrlich! ({kd} K/D, {e['deaths']}D gegen dich) — direkte Duelle vermeiden, Utility und Trades nutzen."})

        # Win rate warning
        if win_rate < 40 and len(e["results"]) >= 3:
            tips.append({"type": "danger", "icon": "trending-down",
                         "text": f"Nur {win_rate}% Win-Rate in {len(e['results'])} Matches gegen diesen Spieler."})
        elif win_rate > 65 and len(e["results"]) >= 3:
            tips.append({"type": "success", "icon": "trophy",
                         "text": f"{win_rate}% Win-Rate in {len(e['results'])} Matches — du gewinnst meistens."})

        # Deagle/pistol specialist
        deagle_count = e["weapons_them"].get("deagle", 0)
        if deagle_count > 0 and deagle_count / max(total_weapon_uses, 1) > 0.25:
            tips.append({"type": "info", "icon": "zap",
                         "text": f"Deagle-Spieler — {deagle_count} Kills mit Deagle. In Eco-Runden vorsichtig."})

        # SMG/rush tendency
        smg_kills = sum(e["weapons_them"].get(w, 0) for w in ["mac10", "mp9", "mp5sd", "mp7", "p90"])
        if smg_kills > 0 and smg_kills / max(total_weapon_uses, 1) > 0.3:
            tips.append({"type": "info", "icon": "zap",
                         "text": f"SMG-/Rush-Tendenz ({round(smg_kills/total_weapon_uses*100)}% SMG-Kills) — Abstand halten, Angles halten."})

        # Low headshot rate = spray player
        if hs_pct_them < 25 and e["deaths"] >= 5:
            tips.append({"type": "info", "icon": "info",
                         "text": f"Spray-Spieler ({hs_pct_them}% HS) — Aim-Duelle solltest du gewinnen."})

        # Multi-map opponent
        if len(e["maps"]) >= 3:
            tips.append({"type": "info", "icon": "map",
                         "text": f"Auf {len(e['maps'])} verschiedenen Maps getroffen — vielseitiger Spieler."})

        opponents.append({
            "name": e["name"], "steam_id": sid,
            "kills": e["kills"], "deaths": e["deaths"],
            "kd": kd, "total_duels": total,
            "hs_pct_you": hs_pct_you, "hs_pct_them": hs_pct_them,
            "matches": e["matches"], "threat": threat,
            "top_weapons_them": [{"name": w[0], "count": w[1]} for w in top_weapons_them],
            "maps": map_stats, "win_rate": win_rate,
            "last_seen": last_seen, "tips": tips,
            "positions": pos_summary,
        })

    opponents.sort(key=lambda x: x["total_duels"], reverse=True)

    return {
        "has_data": True,
        "opponents": opponents,
        "match_count": match_count,
        "total_opponents": len(opponents),
    }


# ── Death Analysis ────────────────────────────────────────
def _build_death_analysis(cfg: dict) -> dict:
    """Analyze death patterns across all matches — why and when do you die?"""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    matches = []
    # Aggregates
    total_deaths = 0
    total_kills = 0
    total_rounds = 0
    total_opening_deaths = 0
    total_opening_kills = 0
    total_trade_kills = 0
    agg_timing = {"early": 0, "mid": 0, "late": 0}
    agg_side = {"ct_deaths": 0, "t_deaths": 0, "ct_rounds": 0, "t_rounds": 0}
    agg_weapons: dict[str, int] = {}  # weapon -> times killed by
    agg_killers: dict[str, dict] = {}  # killer name -> {kills, hs, weapons}
    # Per-round death context from timeline
    deaths_with_kill = 0  # got a kill before dying
    deaths_no_impact = 0  # died without any kill/assist
    hs_deaths = 0
    total_death_events = 0
    # ── Death Categories ──
    cat_counts = {
        "timing": 0,      # died very early (<25% round), bad peek timing
        "aim": 0,          # lost a headshot duel (enemy HS, player didn't get a kill)
        "positioning": 0,  # died without impact, mid-round (bad angle/exposure)
        "utility": 0,      # died without any utility usage that round
        "eco": 0,          # died in eco/force round (acceptable death)
        "impact": 0,       # died but got kill(s) first (productive death)
        "traded": 0,       # died but was in trade position (won round despite death)
    }
    cat_examples: dict[str, list] = {k: [] for k in cat_counts}

    for f in sorted(export_dir.glob("*_coach.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        match_info = data.get("match", {})
        player = data.get("player", {})
        timeline = data.get("round_timeline", [])

        if not player.get("deaths") and not timeline:
            continue

        m_deaths = player.get("deaths", 0)
        m_kills = player.get("kills", 0)
        m_rounds = match_info.get("total_rounds", 0)
        m_opening_d = player.get("opening_deaths", 0)
        m_opening_k = player.get("opening_kills", 0)
        m_trade_k = player.get("trade_kills", 0)

        total_deaths += m_deaths
        total_kills += m_kills
        total_rounds += m_rounds
        total_opening_deaths += m_opening_d
        total_opening_kills += m_opening_k
        total_trade_kills += m_trade_k


        # Death timing
        dt = player.get("death_timing", {})
        agg_timing["early"] += dt.get("early", 0)
        agg_timing["mid"] += dt.get("mid", 0)
        agg_timing["late"] += dt.get("late", 0)

        # Side split
        side = player.get("side_split", {})
        agg_side["ct_deaths"] += side.get("ct_deaths", player.get("ct_deaths", 0))
        agg_side["t_deaths"] += side.get("t_deaths", player.get("t_deaths", 0))

        # Per-round timeline analysis
        match_death_weapons: dict[str, int] = {}
        round_details = []
        for rd in timeline:
            rnum = rd.get("round", 0)
            rd_side = rd.get("side", "?")
            events = rd.get("events", [])
            died = rd.get("player_died", False)
            kills_in_round = rd.get("player_kills", 0)

            if rd_side == "CT":
                agg_side["ct_rounds"] += 1
            elif rd_side == "T":
                agg_side["t_rounds"] += 1

            kill_events = [e for e in events if e.get("type") == "kill"]
            death_events = [e for e in events if e.get("type") == "death"]

            if died and death_events:
                de = death_events[0]
                total_death_events += 1
                weapon = de.get("weapon", "unknown")
                killer = de.get("killer", "?")
                is_hs = de.get("headshot", False)
                death_pct = de.get("pct", 50)

                # Weapon that killed us
                w_clean = weapon.replace("weapon_", "")
                agg_weapons[w_clean] = agg_weapons.get(w_clean, 0) + 1
                match_death_weapons[w_clean] = match_death_weapons.get(w_clean, 0) + 1

                # Killer tracking
                if killer != "?" and killer:
                    if killer not in agg_killers:
                        agg_killers[killer] = {"kills": 0, "hs": 0, "weapons": {}}
                    agg_killers[killer]["kills"] += 1
                    if is_hs:
                        agg_killers[killer]["hs"] += 1
                    agg_killers[killer]["weapons"][w_clean] = agg_killers[killer]["weapons"].get(w_clean, 0) + 1

                if is_hs:
                    hs_deaths += 1

                # Did we get a kill before dying?
                if kills_in_round > 0:
                    deaths_with_kill += 1
                else:
                    deaths_no_impact += 1

                # ── Categorize this death ──
                has_utility = any(e.get("type") in ("flash", "smoke", "grenade", "molotov")
                                 for e in events)
                died_early = rd.get("died_early", False) or death_pct < 25
                won_round = rd.get("won", False)
                eco_weapons = {"glock", "usp_silencer", "hkp2000", "p250", "tec9",
                               "fiveseven", "cz75_auto", "deagle", "revolver",
                               "mac10", "mp9", "mp7", "mp5sd", "ump45", "p90",
                               "bizon", "nova", "sawedoff", "mag7"}
                # Determine economy from weapon used by player (if they had kills)
                player_weapons = [e.get("weapon", "").replace("weapon_", "")
                                  for e in events if e.get("type") == "kill"]
                is_eco_round = (not player_weapons or
                                all(pw in eco_weapons for pw in player_weapons))

                death_cat = "positioning"  # default
                example = {"round": rnum, "map": match_info.get("map", "?"),
                           "date": match_info.get("date", "?"), "side": rd_side,
                           "weapon": w_clean, "killer": killer}

                if kills_in_round > 0 and won_round:
                    death_cat = "traded"
                elif kills_in_round > 0:
                    death_cat = "impact"
                elif died_early and is_hs:
                    death_cat = "timing"
                elif died_early:
                    death_cat = "timing"
                elif is_eco_round and w_clean not in eco_weapons:
                    # Killed by rifle/awp while on eco — acceptable
                    death_cat = "eco"
                elif is_hs and kills_in_round == 0:
                    death_cat = "aim"
                elif not has_utility and kills_in_round == 0 and death_pct < 60:
                    death_cat = "utility"
                else:
                    death_cat = "positioning"

                cat_counts[death_cat] += 1
                if len(cat_examples[death_cat]) < 3:
                    example["cat"] = death_cat
                    cat_examples[death_cat].append(example)

                round_details.append({
                    "round": rnum,
                    "side": rd_side,
                    "weapon": w_clean,
                    "killer": killer,
                    "headshot": is_hs,
                    "pct": death_pct,
                    "had_kill": kills_in_round > 0,
                    "won": rd.get("won", False),
                    "category": death_cat,
                })

        # Top death weapons this match
        top_wpn = sorted(match_death_weapons.items(), key=lambda x: x[1], reverse=True)
        top_wpn_str = ", ".join(f"{w}" for w, _ in top_wpn[:3]) if top_wpn else "-"

        matches.append({
            "date": match_info.get("date", "?"),
            "map": match_info.get("map", "?"),
            "score": f"{match_info.get('score_own', '?')}:{match_info.get('score_enemy', '?')}",
            "result": match_info.get("result", "?"),
            "rating": player.get("rating", 0),
            "kills": m_kills,
            "deaths": m_deaths,
            "rounds": m_rounds,
            "opening_deaths": m_opening_d,
            "death_rate": round(m_deaths / m_rounds * 100, 1) if m_rounds else 0,
            "top_weapons": top_wpn_str,
            "round_deaths": round_details,
        })

    if not matches:
        return {"has_data": False}

    match_count = len(matches)
    avg_deaths = round(total_deaths / match_count, 1) if match_count else 0
    death_rate = round(total_deaths / total_rounds * 100, 1) if total_rounds else 0
    survival_rate = round((1 - total_deaths / total_rounds) * 100, 1) if total_rounds else 0
    opening_death_rate = round(total_opening_deaths / total_rounds * 100, 1) if total_rounds else 0
    opening_duel_wr = round(total_opening_kills / (total_opening_kills + total_opening_deaths) * 100, 1) if (total_opening_kills + total_opening_deaths) else 0

    # Top weapons that kill us
    top_weapons = sorted(agg_weapons.items(), key=lambda x: x[1], reverse=True)[:10]
    top_weapons_total = sum(v for _, v in top_weapons)

    # Top killers (nemeses by death count)
    top_killers = sorted(agg_killers.items(), key=lambda x: x[1]["kills"], reverse=True)[:8]
    top_killers_list = []
    for name, info in top_killers:
        top_wpn = max(info["weapons"], key=info["weapons"].get) if info["weapons"] else "?"
        top_killers_list.append({
            "name": name,
            "deaths": info["kills"],
            "hs": info["hs"],
            "hs_pct": round(info["hs"] / info["kills"] * 100) if info["kills"] else 0,
            "weapon": top_wpn,
        })

    # Timing percentages
    timing_total = agg_timing["early"] + agg_timing["mid"] + agg_timing["late"]
    timing_pcts = {
        k: round(v / timing_total * 100, 1) if timing_total else 0
        for k, v in agg_timing.items()
    }

    # Side percentages
    side_total = agg_side["ct_deaths"] + agg_side["t_deaths"]
    ct_death_pct = round(agg_side["ct_deaths"] / side_total * 100, 1) if side_total else 0
    t_death_pct = round(agg_side["t_deaths"] / side_total * 100, 1) if side_total else 0
    ct_death_rate = round(agg_side["ct_deaths"] / agg_side["ct_rounds"] * 100, 1) if agg_side["ct_rounds"] else 0
    t_death_rate = round(agg_side["t_deaths"] / agg_side["t_rounds"] * 100, 1) if agg_side["t_rounds"] else 0

    # Trade and impact stats
    trade_rate = round(total_trade_kills / total_rounds * 100, 1) if total_rounds else 0
    impact_pct = round(deaths_with_kill / total_death_events * 100, 1) if total_death_events else 0

    # Trend data (deaths per match, chronological)
    trend = [{"date": m["date"], "deaths": m["deaths"], "death_rate": m["death_rate"],
              "map": m["map"], "result": m["result"]} for m in reversed(matches)]

    # Actionable tips
    tips = []
    if timing_pcts["early"] > 40:
        tips.append({"type": "warning", "icon": "alert-triangle",
                     "text": f"{timing_pcts['early']}% deiner Tode passieren frueh in der Runde — spiel passiver oder warte auf Utility."})
    if opening_death_rate > 15:
        tips.append({"type": "warning", "icon": "skull",
                     "text": f"Opening Death Rate {opening_death_rate}% — du stirbst zu oft als Erster. Lass einen Teammate vorgehen."})
    if trade_rate < 5 and total_rounds > 50:
        tips.append({"type": "warning", "icon": "users",
                     "text": f"Nur {total_trade_kills} Trade-Kills in {total_rounds} Runden — achte darauf, Teammates schneller zu traden."})
    if impact_pct > 50:
        tips.append({"type": "success", "icon": "check-circle",
                     "text": f"In {impact_pct}% deiner Tode hattest du vorher einen Kill — du stirbst produktiv."})
    if opening_duel_wr >= 55:
        tips.append({"type": "success", "icon": "swords",
                     "text": f"Opening Duel Winrate {opening_duel_wr}% — starke Erstduelle, weiter so."})

    worse_side = "CT" if ct_death_rate > t_death_rate + 5 else ("T" if t_death_rate > ct_death_rate + 5 else None)
    if worse_side:
        worse_rate = ct_death_rate if worse_side == "CT" else t_death_rate
        tips.append({"type": "warning", "icon": "shield",
                     "text": f"Auf {worse_side}-Side stirbst du haeufiger ({worse_rate}% Death Rate) — ueberdenke dein Positioning."})

    # Category-specific tips
    if total_death_events > 20:
        timing_pct_cat = cat_counts["timing"] / total_death_events * 100
        aim_pct_cat = cat_counts["aim"] / total_death_events * 100
        pos_pct_cat = cat_counts["positioning"] / total_death_events * 100
        util_pct_cat = cat_counts["utility"] / total_death_events * 100
        impact_pct_cat = cat_counts["impact"] / total_death_events * 100

        if timing_pct_cat > 25:
            tips.append({"type": "warning", "icon": "timer",
                         "text": f"{timing_pct_cat:.0f}% deiner Tode sind Timing-Fehler — du peekst zu frueh oder wirst beim Rotieren erwischt."})
        if aim_pct_cat > 30:
            tips.append({"type": "warning", "icon": "target",
                         "text": f"{aim_pct_cat:.0f}% deiner Tode sind Aim-Duelle die du verlierst — trainiere Crosshair Placement und Recoil."})
        if pos_pct_cat > 30:
            tips.append({"type": "warning", "icon": "map-pin",
                         "text": f"{pos_pct_cat:.0f}% deiner Tode kommen durch schlechtes Positioning — spiele weniger exposed, nutze Cover."})
        if util_pct_cat > 20:
            tips.append({"type": "warning", "icon": "flame",
                         "text": f"{util_pct_cat:.0f}% deiner Tode passieren ohne eigene Utility — wirf Flash/Smoke bevor du peekst."})
        if impact_pct_cat > 35:
            tips.append({"type": "success", "icon": "trophy",
                         "text": f"{impact_pct_cat:.0f}% deiner Tode sind produktiv (Impact-Deaths) — du stirbst mit Wirkung."})

    return {
        "has_data": True,
        "match_count": match_count,
        "total_deaths": total_deaths,
        "total_rounds": total_rounds,
        "avg_deaths": avg_deaths,
        "death_rate": death_rate,
        "survival_rate": survival_rate,
        "opening_deaths": total_opening_deaths,
        "opening_death_rate": opening_death_rate,
        "opening_duel_wr": opening_duel_wr,
        "timing": agg_timing,
        "timing_pcts": timing_pcts,
        "side": {
            "ct_deaths": agg_side["ct_deaths"],
            "t_deaths": agg_side["t_deaths"],
            "ct_pct": ct_death_pct,
            "t_pct": t_death_pct,
            "ct_death_rate": ct_death_rate,
            "t_death_rate": t_death_rate,
        },
        "trade": {
            "trade_kills": total_trade_kills,
            "trade_rate": trade_rate,
        },
        "impact": {
            "with_kill": deaths_with_kill,
            "no_impact": deaths_no_impact,
            "impact_pct": impact_pct,
        },
        "hs_deaths": hs_deaths,
        "hs_death_pct": round(hs_deaths / total_death_events * 100, 1) if total_death_events else 0,
        "top_weapons": [{"weapon": w, "count": c, "pct": round(c / top_weapons_total * 100, 1) if top_weapons_total else 0} for w, c in top_weapons],
        "top_killers": top_killers_list,
        "trend": trend,
        "matches": matches[:20],
        "tips": tips,
        "categories": {
            k: {"count": v, "pct": round(v / total_death_events * 100, 1) if total_death_events else 0}
            for k, v in cat_counts.items()
        },
        "cat_examples": cat_examples,
        "cat_total": total_death_events,
    }


# ── Momentum ──────────────────────────────────────────────
def _build_momentum(cfg: dict) -> dict:
    """Analyze performance flow, streaks, tilt patterns, and session momentum."""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    matches = []
    for f in sorted(export_dir.glob("*_coach.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        m = data.get("match", {})
        p = data.get("player", {})
        timeline = data.get("round_timeline", [])
        if not m or not p:
            continue

        result = m.get("result", "?")
        matches.append({
            "date": m.get("date", ""),
            "datetime": m.get("datetime", m.get("date", "")),
            "map": m.get("map", "?"),
            "result": result,
            "score": f"{m.get('score_own', '?')}:{m.get('score_enemy', '?')}",
            "rating": p.get("rating", 0),
            "kd": p.get("kd", 0),
            "adr": p.get("adr", 0),
            "kills": p.get("kills", 0),
            "deaths": p.get("deaths", 0),
            "won": result == "Sieg",
            "rounds": len(timeline),
            "round_data": [{
                "round": r.get("round", 0),
                "won": r.get("won", False),
                "kills": r.get("player_kills", 0),
                "died": r.get("player_died", False),
                "side": r.get("side", "?"),
            } for r in timeline],
        })

    if len(matches) < 3:
        return {"has_data": False}

    # ── Streaks ──
    streaks = []
    cur_type = None
    cur_count = 0
    best_win_streak = 0
    worst_loss_streak = 0
    current_streak_type = None
    current_streak_count = 0

    for i, m in enumerate(matches):
        w = m["won"]
        st = "win" if w else "loss"
        if st == cur_type:
            cur_count += 1
        else:
            if cur_type and cur_count >= 2:
                streaks.append({"type": cur_type, "count": cur_count, "end_idx": i - 1})
            cur_type = st
            cur_count = 1
        if w:
            best_win_streak = max(best_win_streak, cur_count)
        else:
            worst_loss_streak = max(worst_loss_streak, cur_count)
    if cur_type and cur_count >= 2:
        streaks.append({"type": cur_type, "count": cur_count, "end_idx": len(matches) - 1})
    # Current streak
    current_streak_type = cur_type or "none"
    current_streak_count = cur_count

    # ── Tilt detection ──
    # Tilt = 2+ losses where rating drops below 0.85 progressively
    tilt_sessions = []
    i = 0
    while i < len(matches):
        if not matches[i]["won"] and matches[i]["rating"] < 0.85:
            start = i
            while i < len(matches) and not matches[i]["won"]:
                i += 1
            length = i - start
            if length >= 2:
                avg_rating = round(sum(matches[j]["rating"] for j in range(start, i)) / length, 2)
                tilt_sessions.append({
                    "start_idx": start,
                    "length": length,
                    "avg_rating": avg_rating,
                    "start_date": matches[start]["date"],
                    "maps": [matches[j]["map"] for j in range(start, i)],
                })
        else:
            i += 1

    # ── Comeback detection ──
    comebacks = []
    for i, m in enumerate(matches):
        rd = m["round_data"]
        if not rd or not m["won"]:
            continue
        # Check for losing at halftime but winning overall
        half = len(rd) // 2
        if half < 6:
            continue
        first_half_wins = sum(1 for r in rd[:half] if r["won"])
        first_half_losses = half - first_half_wins
        if first_half_losses >= first_half_wins + 3:
            comebacks.append({
                "idx": i, "date": m["date"], "map": m["map"],
                "score": m["score"],
                "deficit": f"{first_half_wins}:{first_half_losses}",
            })

    # ── Match-to-match momentum ──
    flow = []
    for i, m in enumerate(matches):
        label = "neutral"
        if i > 0:
            prev = matches[i - 1]
            rating_diff = m["rating"] - prev["rating"]
            if m["won"] and prev["won"]:
                label = "hot"
            elif not m["won"] and not prev["won"]:
                label = "cold"
            elif m["won"] and not prev["won"] and m["rating"] >= 1.0:
                label = "bounce"
            elif not m["won"] and prev["won"] and m["rating"] < 0.8:
                label = "tilt"

        flow.append({
            "date": m["date"], "map": m["map"], "result": m["result"],
            "score": m["score"], "rating": m["rating"], "kd": m["kd"],
            "won": m["won"], "label": label,
        })

    # ── Session grouping (games on same day) ──
    sessions: dict[str, list] = {}
    for i, m in enumerate(matches):
        day = m["date"]
        if day not in sessions:
            sessions[day] = []
        sessions[day].append(m)

    session_analysis = []
    for day, games in sorted(sessions.items()):
        if len(games) < 2:
            continue
        ratings = [g["rating"] for g in games]
        first_rating = ratings[0]
        last_rating = ratings[-1]
        trend = "improving" if last_rating > first_rating + 0.1 else \
                "declining" if last_rating < first_rating - 0.1 else "stable"
        wins = sum(1 for g in games if g["won"])
        session_analysis.append({
            "date": day, "games": len(games),
            "wins": wins, "losses": len(games) - wins,
            "first_rating": first_rating, "last_rating": last_rating,
            "avg_rating": round(sum(ratings) / len(ratings), 2),
            "trend": trend,
        })

    # ── After-loss performance ──
    after_loss_ratings = []
    for i in range(1, len(matches)):
        if not matches[i - 1]["won"]:
            after_loss_ratings.append(matches[i]["rating"])
    after_loss_avg = round(sum(after_loss_ratings) / len(after_loss_ratings), 2) if after_loss_ratings else 0

    after_win_ratings = []
    for i in range(1, len(matches)):
        if matches[i - 1]["won"]:
            after_win_ratings.append(matches[i]["rating"])
    after_win_avg = round(sum(after_win_ratings) / len(after_win_ratings), 2) if after_win_ratings else 0

    return {
        "has_data": True,
        "total_matches": len(matches),
        "flow": flow,
        "best_win_streak": best_win_streak,
        "worst_loss_streak": worst_loss_streak,
        "current_streak": {"type": current_streak_type, "count": current_streak_count},
        "streaks": sorted(streaks, key=lambda x: x["count"], reverse=True)[:10],
        "tilt_sessions": tilt_sessions,
        "comebacks": comebacks,
        "sessions": session_analysis,
        "after_loss_avg": after_loss_avg,
        "after_win_avg": after_win_avg,
    }


# ── Teammates ─────────────────────────────────────────────
def _build_teammates(cfg: dict) -> dict:
    """Analyze teammate performance and chemistry across all matches."""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    agg: dict[str, dict] = {}
    my_stats: dict[str, list] = {}  # my rating when playing with this teammate
    match_count = 0

    for f in sorted(export_dir.glob("*_coach.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        sb = data.get("scoreboard", [])
        match = data.get("match", {})
        player = data.get("player", {})
        if not sb or not match:
            continue
        match_count += 1

        target_idx = next((i for i, s in enumerate(sb) if s.get("is_target")), None)
        if target_idx is None:
            continue

        target_sid = player.get("steam_id", "") or sb[target_idx].get("steam_id", "")
        my_rating = player.get("rating", 0)
        my_kd = player.get("kd", 0)
        result = match.get("result", "?")
        map_name = match.get("map", "?")
        team_start = 0 if target_idx < 5 else 5

        # Collect teammates from same team half
        match_teammates = []
        for i in range(team_start, team_start + 5):
            if i >= len(sb):
                continue
            s = sb[i]
            if s.get("is_target"):
                continue
            sid = s.get("steam_id", "")
            if not sid or sid == target_sid:
                continue
            match_teammates.append(sid)

            if sid not in agg:
                agg[sid] = {
                    "name": s["name"], "steam_id": sid,
                    "matches": 0, "wins": 0, "losses": 0,
                    "kills": 0, "deaths": 0, "assists": 0,
                    "adr_sum": 0, "rating_sum": 0,
                    "maps": {}, "dates": [],
                }
            t = agg[sid]
            t["name"] = s["name"]
            t["matches"] += 1
            t["kills"] += s.get("kills", 0)
            t["deaths"] += s.get("deaths", 0)
            t["assists"] += s.get("assists", 0)
            t["adr_sum"] += s.get("adr", 0)
            t["maps"][map_name] = t["maps"].get(map_name, 0) + 1
            t["dates"].append(match.get("date", ""))
            if result == "Sieg":
                t["wins"] += 1
            elif result == "Niederlage":
                t["losses"] += 1

            # Track my performance when playing with this teammate
            if sid not in my_stats:
                my_stats[sid] = []
            my_stats[sid].append({"rating": my_rating, "kd": my_kd, "won": result == "Sieg"})

    if not agg:
        return {"has_data": False}

    # Collect ALL my match ratings/results for "without" comparison
    all_my_ratings = []
    all_my_kds = []
    all_my_wins = 0
    for sid_lists in my_stats.values():
        pass  # already tracked per-teammate
    # Rebuild from first pass — need per-match unique data
    all_match_data: list[dict] = []  # one entry per match
    match_teammate_map: dict[int, set[str]] = {}  # match_idx -> set of teammate sids
    _seen_files: set[str] = set()
    _midx = 0
    for f in sorted((Path(vault_path) / sub / "exports").glob("*_coach.json")):
        if f.name in _seen_files:
            continue
        _seen_files.add(f.name)
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        pl = data.get("player", {})
        mi = data.get("match", {})
        sb = data.get("scoreboard", [])
        if not sb or not mi:
            continue
        r = pl.get("rating", 0)
        k = pl.get("kd", 0)
        w = mi.get("result", "") == "Sieg"
        all_match_data.append({"rating": r, "kd": k, "won": w})
        target_idx = next((i for i, s in enumerate(sb) if s.get("is_target")), None)
        if target_idx is not None:
            team_start = 0 if target_idx < 5 else 5
            tmates = set()
            for i in range(team_start, team_start + 5):
                if i < len(sb) and not sb[i].get("is_target"):
                    sid = sb[i].get("steam_id", "")
                    if sid:
                        tmates.add(sid)
            match_teammate_map[_midx] = tmates
        _midx += 1

    total_matches = len(all_match_data)
    overall_rating = round(sum(m["rating"] for m in all_match_data) / total_matches, 2) if total_matches else 0
    overall_wr = round(sum(1 for m in all_match_data if m["won"]) / total_matches * 100, 1) if total_matches else 0

    # Build teammate list
    teammates = []
    for sid, t in agg.items():
        kd = round(t["kills"] / max(t["deaths"], 1), 2)
        avg_adr = round(t["adr_sum"] / t["matches"], 1)
        win_rate = round(t["wins"] / t["matches"] * 100, 1)
        top_maps = sorted(t["maps"].items(), key=lambda x: -x[1])[:3]

        # My stats when playing WITH this teammate
        ms = my_stats.get(sid, [])
        my_avg_rating = round(sum(m["rating"] for m in ms) / len(ms), 2) if ms else 0
        my_avg_kd = round(sum(m["kd"] for m in ms) / len(ms), 2) if ms else 0
        my_wr = round(sum(1 for m in ms if m["won"]) / len(ms) * 100, 1) if ms else 0

        # My stats WITHOUT this teammate
        without_ratings = [all_match_data[i]["rating"]
                          for i in range(total_matches)
                          if sid not in match_teammate_map.get(i, set())]
        without_wins = [all_match_data[i]["won"]
                       for i in range(total_matches)
                       if sid not in match_teammate_map.get(i, set())]
        my_rating_without = round(sum(without_ratings) / len(without_ratings), 2) if without_ratings else 0
        my_wr_without = round(sum(without_wins) / len(without_wins) * 100, 1) if without_wins else 0

        # Delta: with minus without
        rating_delta = round(my_avg_rating - my_rating_without, 2) if without_ratings else 0
        wr_delta = round(my_wr - my_wr_without, 1) if without_wins else 0

        # Chemistry score: combination of win rate and my performance with them
        chemistry = round((win_rate * 0.4 + my_avg_rating * 30 + min(kd, 2.0) * 15) / 1, 1)

        teammates.append({
            "name": t["name"], "steam_id": sid,
            "matches": t["matches"], "wins": t["wins"], "losses": t["losses"],
            "kills": t["kills"], "deaths": t["deaths"],
            "kd": kd, "avg_adr": avg_adr, "win_rate": win_rate,
            "top_maps": [m[0] for m in top_maps],
            "last_played": t["dates"][-1] if t["dates"] else "",
            "my_rating_with": my_avg_rating, "my_kd_with": my_avg_kd,
            "my_wr_with": my_wr, "chemistry": chemistry,
            "my_rating_without": my_rating_without,
            "my_wr_without": my_wr_without,
            "rating_delta": rating_delta,
            "wr_delta": wr_delta,
        })

    # Sort by matches played
    by_matches = sorted(teammates, key=lambda x: -x["matches"])
    by_chemistry = sorted([t for t in teammates if t["matches"] >= 2],
                          key=lambda x: -x["chemistry"])
    by_winrate = sorted([t for t in teammates if t["matches"] >= 2],
                        key=lambda x: -x["win_rate"])

    # Overall stats
    total_unique = len(teammates)
    regulars = [t for t in teammates if t["matches"] >= 3]

    # Premade impact ranking: sort regulars by rating delta
    by_impact = sorted(regulars, key=lambda x: -x["rating_delta"])

    return {
        "has_data": True,
        "match_count": match_count,
        "total_unique": total_unique,
        "regulars_count": len(regulars),
        "overall_rating": overall_rating,
        "overall_wr": overall_wr,
        "by_matches": by_matches[:20],
        "by_chemistry": by_chemistry[:10],
        "by_winrate": by_winrate[:10],
        "by_impact": by_impact[:10],
        "all": by_matches,
    }


# ── Team / Squad Analysis ─────────────────────────────────
def _build_team_analysis(cfg: dict) -> dict:
    """Analyze squad compositions — duos, trios, 5-stacks and their synergies."""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    from itertools import combinations

    # Per-match: collect teammates + result
    match_squads: list[dict] = []  # {teammates: set, result, map, date, my_rating, team_rating}

    for f in sorted(export_dir.glob("*_coach.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        sb = data.get("scoreboard", [])
        match = data.get("match", {})
        player = data.get("player", {})
        if not sb or not match:
            continue

        target_idx = next((i for i, s in enumerate(sb) if s.get("is_target")), None)
        if target_idx is None:
            continue

        target_sid = player.get("steam_id", "") or sb[target_idx].get("steam_id", "")
        team_start = 0 if target_idx < 5 else 5

        teammates = {}
        team_kills = 0
        team_deaths = 0
        team_adr_sum = 0
        team_count = 0
        for i in range(team_start, team_start + 5):
            if i >= len(sb):
                continue
            s = sb[i]
            sid = s.get("steam_id", "")
            if s.get("is_target") or sid == target_sid or not sid:
                continue
            teammates[sid] = s.get("name", "?")
            team_kills += s.get("kills", 0)
            team_deaths += s.get("deaths", 0)
            team_adr_sum += s.get("adr", 0)
            team_count += 1

        if not teammates:
            continue

        team_kd = round(team_kills / max(team_deaths, 1), 2)
        team_avg_adr = round(team_adr_sum / max(team_count, 1), 1)

        match_squads.append({
            "teammates": teammates,
            "result": match.get("result", "?"),
            "map": match.get("map", "?"),
            "date": match.get("date", ""),
            "my_rating": player.get("rating", 0),
            "my_kd": player.get("kd", 0),
            "team_kd": team_kd,
            "team_adr": team_avg_adr,
        })

    if len(match_squads) < 3:
        return {"has_data": False}

    # Build duo stats: for every pair of teammates, track win/loss and my performance
    duo_stats: dict[tuple, dict] = {}
    # Also track solo vs group performance
    solo_matches = []  # matches with mostly randoms (<=1 repeat teammate)
    group_matches = []  # matches with 2+ repeat teammates

    # First pass: count how often each teammate appears (to detect regulars)
    tm_freq: dict[str, int] = {}
    tm_names: dict[str, str] = {}
    for ms in match_squads:
        for sid, name in ms["teammates"].items():
            tm_freq[sid] = tm_freq.get(sid, 0) + 1
            tm_names[sid] = name

    regulars = {sid for sid, cnt in tm_freq.items() if cnt >= 2}

    for ms in match_squads:
        tm_sids = set(ms["teammates"].keys())
        regular_in_match = tm_sids & regulars
        won = ms["result"] == "Sieg"

        if len(regular_in_match) >= 2:
            group_matches.append(ms)
        else:
            solo_matches.append(ms)

        # Duo combinations
        for a, b in combinations(sorted(tm_sids), 2):
            key = (a, b)
            if key not in duo_stats:
                duo_stats[key] = {"wins": 0, "losses": 0, "matches": 0,
                                  "my_rating_sum": 0, "maps": {}}
            duo_stats[key]["matches"] += 1
            duo_stats[key]["my_rating_sum"] += ms["my_rating"]
            duo_stats[key]["maps"][ms["map"]] = duo_stats[key]["maps"].get(ms["map"], 0) + 1
            if won:
                duo_stats[key]["wins"] += 1
            elif ms["result"] == "Niederlage":
                duo_stats[key]["losses"] += 1

    # Build duo list (min 2 matches together)
    duos = []
    for (a, b), ds in duo_stats.items():
        if ds["matches"] < 2:
            continue
        wr = round(ds["wins"] / ds["matches"] * 100, 1)
        avg_rating = round(ds["my_rating_sum"] / ds["matches"], 2)
        top_map = max(ds["maps"], key=ds["maps"].get) if ds["maps"] else "?"
        duos.append({
            "player_a": tm_names.get(a, "?"),
            "player_b": tm_names.get(b, "?"),
            "matches": ds["matches"],
            "wins": ds["wins"],
            "losses": ds["losses"],
            "win_rate": wr,
            "my_avg_rating": avg_rating,
            "top_map": top_map,
        })

    duos.sort(key=lambda x: (-x["matches"], -x["win_rate"]))
    best_duos = sorted([d for d in duos if d["matches"] >= 3], key=lambda x: -x["win_rate"])[:5]
    worst_duos = sorted([d for d in duos if d["matches"] >= 3], key=lambda x: x["win_rate"])[:5]

    # Detect full stacks (same 4 teammates)
    stack_tracker: dict[tuple, dict] = {}
    for ms in match_squads:
        sids = tuple(sorted(ms["teammates"].keys()))
        if len(sids) != 4:
            continue
        if sids not in stack_tracker:
            stack_tracker[sids] = {"wins": 0, "losses": 0, "matches": 0,
                                   "names": [ms["teammates"][s] for s in sids],
                                   "my_rating_sum": 0, "maps": {}}
        stack_tracker[sids]["matches"] += 1
        stack_tracker[sids]["my_rating_sum"] += ms["my_rating"]
        stack_tracker[sids]["maps"][ms["map"]] = stack_tracker[sids]["maps"].get(ms["map"], 0) + 1
        if ms["result"] == "Sieg":
            stack_tracker[sids]["wins"] += 1
        elif ms["result"] == "Niederlage":
            stack_tracker[sids]["losses"] += 1

    stacks = []
    for sids, st in stack_tracker.items():
        if st["matches"] < 2:
            continue
        wr = round(st["wins"] / st["matches"] * 100, 1)
        stacks.append({
            "names": st["names"],
            "matches": st["matches"],
            "wins": st["wins"],
            "losses": st["losses"],
            "win_rate": wr,
            "my_avg_rating": round(st["my_rating_sum"] / st["matches"], 2),
            "top_map": max(st["maps"], key=st["maps"].get) if st["maps"] else "?",
        })
    stacks.sort(key=lambda x: -x["matches"])

    # Solo vs group performance
    solo_wr = round(sum(1 for m in solo_matches if m["result"] == "Sieg") / len(solo_matches) * 100, 1) if solo_matches else 0
    solo_rating = round(sum(m["my_rating"] for m in solo_matches) / len(solo_matches), 2) if solo_matches else 0
    group_wr = round(sum(1 for m in group_matches if m["result"] == "Sieg") / len(group_matches) * 100, 1) if group_matches else 0
    group_rating = round(sum(m["my_rating"] for m in group_matches) / len(group_matches), 2) if group_matches else 0

    # Tips
    tips = []
    if solo_matches and group_matches:
        if group_wr > solo_wr + 10:
            tips.append({"type": "success",
                         "text": f"Im Stack gewinnst du {group_wr}% vs. {solo_wr}% solo — spiel oefter mit Premades."})
        elif solo_wr > group_wr + 10:
            tips.append({"type": "info",
                         "text": f"Solo gewinnst du {solo_wr}% vs. {group_wr}% im Stack — du adaptierst gut an Randoms."})

    if best_duos:
        d = best_duos[0]
        tips.append({"type": "success",
                     "text": f"Bestes Duo: {d['player_a']} + {d['player_b']} ({d['win_rate']}% WR in {d['matches']} Matches)."})

    if worst_duos and worst_duos[0]["win_rate"] < 40:
        d = worst_duos[0]
        tips.append({"type": "warning",
                     "text": f"Schwieriges Duo: {d['player_a']} + {d['player_b']} (nur {d['win_rate']}% WR in {d['matches']} Matches)."})

    return {
        "has_data": True,
        "total_matches": len(match_squads),
        "total_teammates": len(tm_freq),
        "regulars_count": len(regulars),
        "solo": {"matches": len(solo_matches), "win_rate": solo_wr, "avg_rating": solo_rating},
        "group": {"matches": len(group_matches), "win_rate": group_wr, "avg_rating": group_rating},
        "duos": duos[:20],
        "best_duos": best_duos,
        "worst_duos": worst_duos,
        "stacks": stacks[:10],
        "tips": tips[:6],
    }


# ── Leaderboard ───────────────────────────────────────────
def _build_leaderboard(cfg: dict) -> dict:
    """Rank all players encountered across all matches by various metrics."""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    agg: dict[str, dict] = {}
    self_sids: set[str] = set()

    for f in sorted(export_dir.glob("*_coach.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        sb = data.get("scoreboard", [])
        match = data.get("match", {})
        if not sb or not match:
            continue

        # Detect self steam_id from is_target flag
        for s in sb:
            if s.get("is_target"):
                sid = s.get("steam_id", "")
                if sid:
                    self_sids.add(sid)

        for s in sb:
            sid = s.get("steam_id", "")
            if not sid:
                continue
            if sid not in agg:
                agg[sid] = {
                    "name": s["name"], "steam_id": sid, "is_self": s.get("is_target", False),
                    "matches": 0, "kills": 0, "deaths": 0, "assists": 0,
                    "adr_sum": 0, "hs_pct_sum": 0,
                    "wins": 0, "losses": 0,
                }
            p = agg[sid]
            p["name"] = s["name"]
            p["matches"] += 1
            p["kills"] += s.get("kills", 0)
            p["deaths"] += s.get("deaths", 0)
            p["assists"] += s.get("assists", 0)
            p["adr_sum"] += s.get("adr", 0)
            p["hs_pct_sum"] += s.get("hs_pct", 0)

            # Determine if this player's team won
            target_idx = next((i for i, x in enumerate(sb) if x.get("is_target")), None)
            if target_idx is not None:
                target_team = 0 if target_idx < 5 else 5
                player_team = 0 if sb.index(s) < 5 else 5
                same_team = target_team == player_team
                result = match.get("result", "?")
                if same_team:
                    won = result == "Sieg"
                else:
                    won = result == "Niederlage"
                if won:
                    p["wins"] += 1
                elif (same_team and result == "Niederlage") or (not same_team and result == "Sieg"):
                    p["losses"] += 1

    if not agg:
        return {"has_data": False}

    players = []
    for sid, p in agg.items():
        kd = round(p["kills"] / max(p["deaths"], 1), 2)
        avg_adr = round(p["adr_sum"] / max(p["matches"], 1), 1)
        avg_hs = round(p["hs_pct_sum"] / max(p["matches"], 1), 1)
        kpr = round(p["kills"] / max(p["matches"], 1), 1)
        win_rate = round(p["wins"] / max(p["matches"], 1) * 100, 1)

        players.append({
            "name": p["name"], "steam_id": sid, "is_self": p["is_self"],
            "matches": p["matches"], "kills": p["kills"], "deaths": p["deaths"],
            "assists": p["assists"], "kd": kd, "avg_adr": avg_adr,
            "avg_hs": avg_hs, "kpr": kpr, "win_rate": win_rate,
        })

    by_kd = sorted(players, key=lambda x: (-x["kd"], -x["kills"]))
    by_kills = sorted(players, key=lambda x: -x["kills"])
    by_adr = sorted(players, key=lambda x: -x["avg_adr"])

    # Find self rank
    self_rank_kd = next((i + 1 for i, p in enumerate(by_kd) if p["is_self"]), 0)
    self_rank_kills = next((i + 1 for i, p in enumerate(by_kills) if p["is_self"]), 0)

    return {
        "has_data": True,
        "total_players": len(players),
        "by_kd": by_kd,
        "by_kills": by_kills,
        "by_adr": by_adr,
        "self_rank_kd": self_rank_kd,
        "self_rank_kills": self_rank_kills,
    }


# ── Head-to-Head ──────────────────────────────────────────
def _build_h2h(cfg: dict, vs_sid: str) -> dict:
    """Compare yourself against another player across shared matches."""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False, "players": []}

    # First pass: collect all players for the dropdown
    all_players: dict[str, dict] = {}
    for f in sorted(export_dir.glob("*_coach.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for s in data.get("scoreboard", []):
            sid = s.get("steam_id", "")
            if sid and not s.get("is_target"):
                if sid not in all_players:
                    all_players[sid] = {"name": s["name"], "steam_id": sid, "matches": 0}
                all_players[sid]["name"] = s["name"]
                all_players[sid]["matches"] += 1

    players_list = sorted(all_players.values(), key=lambda x: -x["matches"])

    if not vs_sid or vs_sid not in all_players:
        return {"has_data": False, "players": players_list, "selected": None}

    # Second pass: build comparison for shared matches
    me = {"kills": 0, "deaths": 0, "assists": 0, "adr_sum": 0, "hs_sum": 0,
          "kast_sum": 0, "ok": 0, "od": 0, "trades": 0, "utility_sum": 0,
          "matches": 0, "wins": 0, "multikills": {"2k": 0, "3k": 0, "4k": 0, "5k": 0},
          "clutch_wins": 0, "clutch_attempts": 0, "ratings": []}
    them = {k: (v.copy() if isinstance(v, dict) else v) for k, v in me.items()}
    them["ratings"] = []
    them["multikills"] = {"2k": 0, "3k": 0, "4k": 0, "5k": 0}
    shared_matches = []

    for f in sorted(export_dir.glob("*_coach.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        sb = data.get("scoreboard", [])
        match = data.get("match", {})
        player = data.get("player", {})

        # Check if vs_sid is in this match
        vs_entry = None
        me_entry = None
        for s in sb:
            if s.get("steam_id") == vs_sid:
                vs_entry = s
            if s.get("is_target"):
                me_entry = s

        if not vs_entry or not me_entry:
            continue

        result = match.get("result", "?")

        for stats, entry, pdata in [(me, me_entry, player), (them, vs_entry, None)]:
            stats["kills"] += entry.get("kills", 0)
            stats["deaths"] += entry.get("deaths", 0)
            stats["assists"] += entry.get("assists", 0)
            stats["adr_sum"] += entry.get("adr", 0)
            stats["hs_sum"] += entry.get("hs_pct", 0)
            stats["kast_sum"] += entry.get("kast_pct", 0)
            stats["ok"] += entry.get("opening_kills", 0)
            stats["od"] += entry.get("opening_deaths", 0)
            stats["trades"] += entry.get("trade_kills", 0)
            stats["utility_sum"] += entry.get("utility_per_round", 0)
            stats["matches"] += 1
            mk = entry.get("multikills", {})
            if mk:
                for k in ["2k", "3k", "4k", "5k"]:
                    stats["multikills"][k] += mk.get(k, 0)
            cl = entry.get("clutches", {})
            if cl:
                stats["clutch_wins"] += cl.get("wins", 0)
                stats["clutch_attempts"] += cl.get("attempts", 0)

        # Rating only available for self
        me["ratings"].append(player.get("rating", 0))
        # Determine if they were on the same team
        me_idx = next((i for i, s in enumerate(sb) if s.get("is_target")), 0)
        vs_idx = next((i for i, s in enumerate(sb) if s.get("steam_id") == vs_sid), 0)
        same_team = (me_idx < 5) == (vs_idx < 5)

        if result == "Sieg":
            me["wins"] += 1
            if same_team:
                them["wins"] += 1
        elif result == "Niederlage" and not same_team:
            them["wins"] += 1

        shared_matches.append({
            "date": match.get("date", ""), "map": match.get("map", "?"),
            "score": f"{match.get('score_own', '?')}:{match.get('score_enemy', '?')}",
            "result": result, "same_team": same_team,
            "me_kills": me_entry.get("kills", 0), "me_deaths": me_entry.get("deaths", 0),
            "me_adr": me_entry.get("adr", 0),
            "them_kills": vs_entry.get("kills", 0), "them_deaths": vs_entry.get("deaths", 0),
            "them_adr": vs_entry.get("adr", 0),
        })

    n = me["matches"]
    if n == 0:
        return {"has_data": False, "players": players_list, "selected": None}

    def finalize(s):
        n = max(s["matches"], 1)
        return {
            "kills": s["kills"], "deaths": s["deaths"], "assists": s["assists"],
            "kd": round(s["kills"] / max(s["deaths"], 1), 2),
            "avg_adr": round(s["adr_sum"] / n, 1),
            "avg_hs": round(s["hs_sum"] / n, 1),
            "avg_kast": round(s["kast_sum"] / n, 1),
            "ok": s["ok"], "od": s["od"], "trades": s["trades"],
            "avg_utility": round(s["utility_sum"] / n, 2),
            "wins": s["wins"], "win_rate": round(s["wins"] / n * 100, 1),
            "multikills": s["multikills"],
            "clutch_wins": s["clutch_wins"], "clutch_attempts": s["clutch_attempts"],
        }

    me_final = finalize(me)
    me_final["avg_rating"] = round(sum(me["ratings"]) / len(me["ratings"]), 2) if me["ratings"] else 0
    them_final = finalize(them)

    return {
        "has_data": True,
        "players": players_list,
        "selected": all_players[vs_sid],
        "shared_matches": len(shared_matches),
        "matches": shared_matches,
        "me": me_final,
        "them": them_final,
    }


# ── Clutch Analysis ───────────────────────────────────────
def _build_clutch_analysis(cfg: dict) -> dict:
    """Deep analysis of clutch situations across all matches."""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    total_attempts = 0
    total_wins = 0
    matches_with_clutches = []
    by_map: dict[str, dict] = {}
    by_side = {"CT": {"attempts": 0, "wins": 0}, "T": {"attempts": 0, "wins": 0}}
    clutch_rounds = []

    for f in sorted(export_dir.glob("*_coach.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        m = data.get("match", {})
        p = data.get("player", {})
        cl = p.get("clutches", {})
        timeline = data.get("round_timeline", [])
        attempts = cl.get("attempts", 0)
        wins = cl.get("wins", 0)

        if attempts == 0:
            continue

        total_attempts += attempts
        total_wins += wins
        map_name = m.get("map", "?")

        if map_name not in by_map:
            by_map[map_name] = {"attempts": 0, "wins": 0}
        by_map[map_name]["attempts"] += attempts
        by_map[map_name]["wins"] += wins

        matches_with_clutches.append({
            "date": m.get("date", ""), "map": map_name,
            "score": f"{m.get('score_own', '?')}:{m.get('score_enemy', '?')}",
            "result": m.get("result", "?"),
            "attempts": attempts, "wins": wins,
            "rating": p.get("rating", 0),
        })

        # Find clutch rounds from timeline (rounds where player got 1+ kills
        # and was last alive — approximate by: survived + won + kills >= 1)
        for r in timeline:
            kills = r.get("player_kills", 0)
            died = r.get("player_died", False)
            won = r.get("won", False)
            side = r.get("side", "?")

            # Detect potential clutch rounds: player got kills and the round
            # was close (we use a heuristic — died_early=False, kills >= 1)
            events = r.get("events", [])
            kill_count = sum(1 for e in events if e.get("type") == "kill")
            death_events = [e for e in events if e.get("type") == "death"]

            # A clutch-like round: player killed 1+ enemies after teammates
            # died (approximate: player has kills, didn't die early, won)
            if kill_count >= 2 and won and not r.get("died_early", False):
                weapons_used = [e.get("weapon", "?") for e in events if e.get("type") == "kill"]
                clutch_rounds.append({
                    "round": r.get("round", 0), "side": side,
                    "kills": kill_count, "won": won,
                    "weapons": weapons_used,
                    "date": m.get("date", ""), "map": map_name,
                })
                if side in by_side:
                    by_side[side]["attempts"] += 1
                    if won:
                        by_side[side]["wins"] += 1

    if total_attempts == 0:
        return {"has_data": False}

    # Map stats
    map_stats = []
    for map_name, ms in sorted(by_map.items(), key=lambda x: -x[1]["attempts"]):
        wr = round(ms["wins"] / max(ms["attempts"], 1) * 100, 1)
        map_stats.append({
            "map": map_name, "attempts": ms["attempts"],
            "wins": ms["wins"], "win_rate": wr,
        })

    # Overall
    overall_wr = round(total_wins / max(total_attempts, 1) * 100, 1)

    # Best/worst clutch matches
    best = sorted([m for m in matches_with_clutches if m["wins"] > 0],
                  key=lambda x: -x["wins"])[:5]
    worst = sorted([m for m in matches_with_clutches if m["wins"] == 0 and m["attempts"] >= 2],
                   key=lambda x: -x["attempts"])[:5]

    return {
        "has_data": True,
        "total_attempts": total_attempts,
        "total_wins": total_wins,
        "win_rate": overall_wr,
        "matches_count": len(matches_with_clutches),
        "map_stats": map_stats,
        "by_side": by_side,
        "matches": sorted(matches_with_clutches, key=lambda x: x["date"], reverse=True),
        "highlight_rounds": sorted(clutch_rounds, key=lambda x: -x["kills"])[:15],
        "best_matches": best,
        "worst_matches": worst,
    }


def _build_mechanics(cfg: dict) -> dict:
    """Build aim/mechanics analysis data across all exports."""
    vault_path = cfg.get("obsidian_vault_path", "")
    subfolder = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / subfolder / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    files = sorted(export_dir.glob("*_coach.json"))
    if not files:
        return {"has_data": False}

    # Accumulators
    crosshair_vals = []
    spray_burst_total = 0
    spray_spray_total = 0
    recoil_vals = []
    cs_scores = []
    accuracy_vals = []
    engage_close = 0
    engage_mid = 0
    engage_long = 0
    engage_avg_vals = []
    death_early = 0
    death_mid = 0
    death_late = 0
    total_kills = 0
    total_deaths = 0
    awp_kills = 0
    rifle_kills = 0
    pistol_kills = 0
    hs_vals = []
    match_entries = []  # per-match mechanics snapshot
    trend_data = []  # chronological for sparklines

    for fp in files:
        try:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        player = data.get("player", {})
        match_info = data.get("match", {})
        date_str = match_info.get("date", "")
        map_name = match_info.get("map", "?")

        ch = player.get("crosshair_placement", {})
        sp = player.get("spray_control", {})
        ed = player.get("engagement_distance", {})
        dt = player.get("death_timing", {})
        wp = player.get("weapons", {})
        acc = player.get("accuracy")
        cs_s = player.get("counter_strafe_score")
        hs = player.get("hs_pct")

        if ch.get("avg_degrees") is not None:
            crosshair_vals.append(ch["avg_degrees"])
        spray_burst_total += sp.get("burst_kills", 0)
        spray_spray_total += sp.get("spray_kills", 0)
        if sp.get("avg_recoil_index") is not None:
            recoil_vals.append(sp["avg_recoil_index"])
        if cs_s is not None:
            cs_scores.append(cs_s)
        if acc is not None:
            accuracy_vals.append(acc)
        if hs is not None:
            hs_vals.append(hs)

        engage_close += ed.get("close", 0)
        engage_mid += ed.get("mid", 0)
        engage_long += ed.get("long", 0)
        if ed.get("avg") is not None:
            engage_avg_vals.append(ed["avg"])

        death_early += dt.get("early", 0)
        death_mid += dt.get("mid", 0)
        death_late += dt.get("late", 0)

        k = player.get("kills", 0)
        d = player.get("deaths", 0)
        total_kills += k
        total_deaths += d
        awp_kills += wp.get("awp_kills", 0)
        rifle_kills += wp.get("rifle_kills", 0)
        pistol_kills += wp.get("pistol_kills", 0)

        # Per-match entry
        entry = {
            "date": date_str,
            "map": map_name,
            "crosshair": ch.get("avg_degrees"),
            "accuracy": acc,
            "cs_score": cs_s,
            "hs_pct": hs,
            "recoil": sp.get("avg_recoil_index"),
        }
        match_entries.append(entry)
        trend_data.append(entry)

    n = len(match_entries)
    if n == 0:
        return {"has_data": False}

    # Compute averages
    avg_crosshair = round(sum(crosshair_vals) / len(crosshair_vals), 1) if crosshair_vals else None
    avg_recoil = round(sum(recoil_vals) / len(recoil_vals), 2) if recoil_vals else None
    avg_cs = round(sum(cs_scores) / len(cs_scores), 1) if cs_scores else None
    avg_accuracy = round(sum(accuracy_vals) / len(accuracy_vals), 1) if accuracy_vals else None
    avg_hs = round(sum(hs_vals) / len(hs_vals), 1) if hs_vals else None
    avg_engage = round(sum(engage_avg_vals) / len(engage_avg_vals), 0) if engage_avg_vals else None

    # Crosshair placement rating
    if avg_crosshair is not None:
        if avg_crosshair <= 5:
            ch_rating = "Exzellent"
        elif avg_crosshair <= 8:
            ch_rating = "Gut"
        elif avg_crosshair <= 12:
            ch_rating = "Durchschnittlich"
        else:
            ch_rating = "Verbesserungswuerdig"
    else:
        ch_rating = "—"

    # Counter-strafe rating
    if avg_cs is not None:
        if avg_cs >= 90:
            cs_rating = "Elite"
        elif avg_cs >= 80:
            cs_rating = "Gut"
        elif avg_cs >= 65:
            cs_rating = "Durchschnittlich"
        else:
            cs_rating = "Schwach"
    else:
        cs_rating = "—"

    # Spray discipline ratio
    total_spray_kills = spray_burst_total + spray_spray_total
    burst_pct = round(spray_burst_total / total_spray_kills * 100, 1) if total_spray_kills > 0 else 0

    # Engagement distance breakdown
    total_engagements = engage_close + engage_mid + engage_long
    engage_breakdown = {
        "close": engage_close,
        "mid": engage_mid,
        "long": engage_long,
        "total": total_engagements,
        "close_pct": round(engage_close / total_engagements * 100) if total_engagements > 0 else 0,
        "mid_pct": round(engage_mid / total_engagements * 100) if total_engagements > 0 else 0,
        "long_pct": round(engage_long / total_engagements * 100) if total_engagements > 0 else 0,
        "avg_distance": avg_engage,
    }

    # Death timing breakdown
    total_death_events = death_early + death_mid + death_late
    death_breakdown = {
        "early": death_early,
        "mid": death_mid,
        "late": death_late,
        "total": total_death_events,
        "early_pct": round(death_early / total_death_events * 100) if total_death_events > 0 else 0,
        "mid_pct": round(death_mid / total_death_events * 100) if total_death_events > 0 else 0,
        "late_pct": round(death_late / total_death_events * 100) if total_death_events > 0 else 0,
    }

    # Weapon split
    total_typed = awp_kills + rifle_kills + pistol_kills
    weapon_split = {
        "awp": awp_kills,
        "rifle": rifle_kills,
        "pistol": pistol_kills,
        "other": total_kills - total_typed if total_kills > total_typed else 0,
        "awp_pct": round(awp_kills / total_typed * 100) if total_typed > 0 else 0,
        "rifle_pct": round(rifle_kills / total_typed * 100) if total_typed > 0 else 0,
        "pistol_pct": round(pistol_kills / total_typed * 100) if total_typed > 0 else 0,
    }

    # Trend (last 10 matches)
    recent = trend_data[-10:]

    # Coaching insights
    insights = []
    if avg_crosshair is not None and avg_crosshair > 10:
        insights.append("Dein Crosshair-Placement ist zu hoch. Uebe auf Workshop-Maps wie 'Aim Botz' mit bewusstem Head-Level-Tracking.")
    if avg_cs is not None and avg_cs < 75:
        insights.append("Dein Counter-Strafe-Score ist niedrig. Uebe im Deathmatch, vor jedem Schuss kurz die Gegentaste zu druecken.")
    if avg_accuracy is not None and avg_accuracy < 20:
        insights.append("Deine Accuracy ist unterdurchschnittlich. Konzentriere dich auf weniger, aber praezisere Schuesse.")
    if burst_pct < 80 and total_spray_kills > 20:
        insights.append(f"Du sprayed zu viel ({100 - burst_pct:.0f}% Spray-Kills). Uebe kontrollierte Bursts von 3-5 Schuss.")
    if death_breakdown["early_pct"] > 35 and total_death_events > 30:
        insights.append(f"{death_breakdown['early_pct']}% deiner Tode sind frueh in der Runde. Ueberdenke deine Positionierung und spiele weniger aggressiv.")
    if avg_hs is not None and avg_hs >= 55:
        insights.append(f"Starke Headshot-Rate von {avg_hs}%! Deine Aim-Praezision ist ueberdurchschnittlich.")
    if avg_cs is not None and avg_cs >= 88:
        insights.append(f"Exzellentes Counter-Strafing ({avg_cs}%). Deine Bewegungsmechanik ist auf hohem Niveau.")

    return {
        "has_data": True,
        "matches_analyzed": n,
        "avg_crosshair": avg_crosshair,
        "ch_rating": ch_rating,
        "avg_recoil": avg_recoil,
        "avg_cs": avg_cs,
        "cs_rating": cs_rating,
        "avg_accuracy": avg_accuracy,
        "avg_hs": avg_hs,
        "burst_pct": burst_pct,
        "spray_burst": spray_burst_total,
        "spray_spray": spray_spray_total,
        "engage": engage_breakdown,
        "deaths": death_breakdown,
        "weapons": weapon_split,
        "total_kills": total_kills,
        "trend": recent,
        "matches": sorted(match_entries, key=lambda x: x["date"], reverse=True),
        "insights": insights,
    }


def _build_round_timeline(cfg: dict) -> dict:
    """Build round-by-round timeline data for all matches."""
    vault_path = cfg.get("obsidian_vault_path", "")
    subfolder = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / subfolder / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    files = sorted(export_dir.glob("*_coach.json"))
    if not files:
        return {"has_data": False}

    matches = []
    agg_side_rounds = {"ct_won": 0, "ct_total": 0, "t_won": 0, "t_total": 0}
    agg_pistol = {"won": 0, "total": 0}
    agg_post_plant = {"won": 0, "total": 0}
    total_comebacks = 0
    total_chokes = 0
    kill_timeline_all = []  # kills per round across matches for avg

    for fp in files:
        try:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        rt = data.get("round_timeline", [])
        if not rt:
            continue

        match_info = data.get("match", {})
        economy = data.get("economy", {})
        player = data.get("player", {})
        date_str = match_info.get("date", "")
        map_name = match_info.get("map", "?")
        score_own = match_info.get("score_own", 0)
        score_enemy = match_info.get("score_enemy", 0)
        result = match_info.get("result", "")

        rounds_data = []
        running_own = 0
        running_enemy = 0
        max_deficit = 0
        max_lead = 0

        for r in rt:
            rnum = r.get("round", 0)
            side = r.get("side", "?")
            won = r.get("won", False)
            events = r.get("events", [])
            pk = r.get("player_kills", 0)
            died = r.get("player_died", False)
            died_early = r.get("died_early", False)

            if won:
                running_own += 1
            else:
                running_enemy += 1

            diff = running_own - running_enemy
            if diff < max_deficit:
                max_deficit = diff
            if diff > max_lead:
                max_lead = diff

            # Classify events
            has_bomb_plant = any(e.get("type") == "bomb_plant" for e in events)
            has_bomb_defuse = any(e.get("type") == "bomb_defuse" for e in events)
            player_planted = any(e.get("type") == "bomb_plant" and e.get("is_self") for e in events)
            player_defused = any(e.get("type") == "bomb_defuse" and e.get("is_self") for e in events)

            # Determine buy type for this round based on economy data
            kill_weapons = [e.get("weapon", "") for e in events if e.get("type") == "kill"]

            rounds_data.append({
                "round": rnum,
                "side": side,
                "won": won,
                "kills": pk,
                "died": died,
                "died_early": died_early,
                "bomb_plant": has_bomb_plant,
                "bomb_defuse": has_bomb_defuse,
                "player_planted": player_planted,
                "player_defused": player_defused,
                "score_own": running_own,
                "score_enemy": running_enemy,
                "weapons": kill_weapons,
            })

            # Aggregates
            side_key = side.lower()
            if side_key in ("ct", "t"):
                agg_side_rounds[f"{side_key}_total"] += 1
                if won:
                    agg_side_rounds[f"{side_key}_won"] += 1

            # Pistol rounds
            if rnum in (1, 13):
                agg_pistol["total"] += 1
                if won:
                    agg_pistol["won"] += 1

            # Post-plant
            if has_bomb_plant:
                agg_post_plant["total"] += 1
                if (side == "T" and won) or (side == "CT" and not won):
                    # T planted and won, or CT failed to defuse
                    pass
                if side == "T" and won:
                    agg_post_plant["won"] += 1
                elif side == "CT" and has_bomb_defuse and won:
                    agg_post_plant["won"] += 1

            kill_timeline_all.append(pk)

        # Detect comebacks and chokes
        if max_deficit <= -4 and result == "Sieg":
            total_comebacks += 1
        if max_lead >= 4 and result == "Niederlage":
            total_chokes += 1

        matches.append({
            "date": date_str,
            "map": map_name,
            "score": f"{score_own}:{score_enemy}",
            "result": result,
            "total_rounds": len(rounds_data),
            "rounds": rounds_data,
            "max_deficit": max_deficit,
            "max_lead": max_lead,
        })

    if not matches:
        return {"has_data": False}

    # Aggregated stats
    ct_wr = round(agg_side_rounds["ct_won"] / agg_side_rounds["ct_total"] * 100, 1) if agg_side_rounds["ct_total"] > 0 else 0
    t_wr = round(agg_side_rounds["t_won"] / agg_side_rounds["t_total"] * 100, 1) if agg_side_rounds["t_total"] > 0 else 0
    pistol_wr = round(agg_pistol["won"] / agg_pistol["total"] * 100, 1) if agg_pistol["total"] > 0 else 0
    avg_kills_per_round = round(sum(kill_timeline_all) / len(kill_timeline_all), 2) if kill_timeline_all else 0

    # Round performance by number (which rounds are you best/worst at)
    round_perf = {}
    for m in matches:
        for r in m["rounds"]:
            rn = r["round"]
            if rn not in round_perf:
                round_perf[rn] = {"wins": 0, "total": 0, "kills": 0}
            round_perf[rn]["total"] += 1
            if r["won"]:
                round_perf[rn]["wins"] += 1
            round_perf[rn]["kills"] += r["kills"]

    round_stats = []
    for rn in sorted(round_perf.keys()):
        rp = round_perf[rn]
        if rp["total"] >= 3:
            round_stats.append({
                "round": rn,
                "win_rate": round(rp["wins"] / rp["total"] * 100, 1),
                "avg_kills": round(rp["kills"] / rp["total"], 2),
                "total": rp["total"],
            })

    return {
        "has_data": True,
        "matches_count": len(matches),
        "ct_wr": ct_wr,
        "t_wr": t_wr,
        "ct_rounds": agg_side_rounds,
        "t_rounds": agg_side_rounds,
        "pistol_wr": pistol_wr,
        "pistol_stats": agg_pistol,
        "avg_kpr": avg_kills_per_round,
        "comebacks": total_comebacks,
        "chokes": total_chokes,
        "round_stats": round_stats,
        "matches": sorted(matches, key=lambda x: x["date"], reverse=True),
    }


# ── Positions-Zonen ──────────────────────────────────────────
# Game-coordinate bounding boxes for named zones per map.
# Format: (x_min, x_max, y_min, y_max)
MAP_ZONES: dict[str, dict[str, tuple]] = {
    "mirage": {
        "A-Site":   (-1900, -750, -1000, 200),
        "A-Ramp":   (-1500, -300, 200, 700),
        "Palace":   (-2300, -1550, 300, 1100),
        "Jungle":   (-2400, -1500, -600, 200),
        "CT-Spawn": (-2600, -1600, -2200, -1200),
        "Mid":      (-400, 400, -1200, 600),
        "Top-Mid":  (-400, 400, 600, 1100),
        "B-Site":   (-700, 400, -2300, -1300),
        "B-Apps":   (200, 900, -1600, -400),
        "T-Spawn":  (800, 1700, -200, 800),
    },
    "dust2": {
        "A-Site":    (400, 1500, 2200, 3200),
        "A-Long":    (-200, 700, 500, 2200),
        "A-Short":   (-800, 200, 1800, 2800),
        "Mid":       (-900, -200, 300, 1800),
        "CT-Spawn":  (300, 1200, 3000, 3600),
        "B-Site":    (-2100, -1100, 2200, 3000),
        "B-Tunnel":  (-1900, -900, 800, 2200),
        "T-Spawn":   (-700, 200, -800, 200),
        "Lower-Tunnel": (-1600, -600, 200, 900),
    },
    "inferno": {
        "A-Site":  (1800, 2800, 200, 1100),
        "Apps":    (500, 1600, 100, 1200),
        "Mid":     (100, 900, -700, 200),
        "Banana":  (-100, 600, -2000, -700),
        "B-Site":  (-600, 500, -2800, -2000),
        "CT-Spawn": (2000, 2800, -800, 200),
        "T-Spawn": (-800, 200, 1200, 2200),
        "Arch":    (1200, 2000, -600, 200),
    },
    "ancient": {
        "A-Site":   (-500, 600, -1100, -200),
        "A-Main":   (-500, 500, -200, 700),
        "Mid":      (-1200, -400, -400, 600),
        "B-Site":   (-2000, -1000, -1400, -400),
        "B-Main":   (-1500, -500, 600, 1200),
        "CT-Spawn": (-600, 600, -2000, -1100),
        "T-Spawn":  (-300, 600, 1000, 1800),
    },
    "anubis": {
        "A-Site":   (-500, 600, -1200, -300),
        "A-Main":   (-500, 500, -300, 600),
        "Mid":      (-1000, -200, -600, 400),
        "B-Site":   (-2000, -1000, -1200, -200),
        "B-Main":   (-1500, -400, 400, 1200),
        "CT-Spawn": (-400, 600, -2200, -1200),
        "T-Spawn":  (-200, 600, 1000, 1800),
    },
    "nuke": {
        "Outside":  (-1800, 200, -1200, 600),
        "Ramp":     (-500, 600, 600, 1600),
        "A-Site":   (-400, 700, -1800, -600),
        "B-Site":   (-500, 600, -1800, -600),
        "Lobby":    (600, 1500, -400, 600),
        "CT-Spawn": (-1200, 0, -2600, -1800),
        "T-Spawn":  (500, 1500, 600, 1500),
    },
    "vertigo": {
        "A-Site":   (-400, 600, -400, 500),
        "A-Ramp":   (-1000, -300, -200, 600),
        "Mid":      (-800, 0, -1200, -400),
        "B-Site":   (-1800, -600, -1000, 0),
        "CT-Spawn": (-200, 600, -1800, -800),
        "T-Spawn":  (-400, 600, 600, 1400),
    },
    "overpass": {
        "A-Site":   (-2800, -1600, -1400, -400),
        "A-Long":   (-2200, -1200, -400, 600),
        "Toilets":  (-3400, -2600, -600, 200),
        "Mid":      (-1600, -600, -800, 200),
        "B-Site":   (-800, 400, -2000, -1000),
        "B-Short":  (-600, 200, -1000, -200),
        "CT-Spawn": (-1800, -800, -2200, -1400),
        "T-Spawn":  (-1600, -600, 600, 1400),
    },
}


def _classify_zone(x: float, y: float, map_name: str) -> str:
    """Classify game coordinates into a named zone. Returns 'Other' if no match."""
    zones = MAP_ZONES.get(map_name.lower(), {})
    for zone_name, (x_min, x_max, y_min, y_max) in zones.items():
        if x_min <= x <= x_max and y_min <= y <= y_max:
            return zone_name
    return "Other"


def _build_zone_analysis(cfg: dict) -> dict:
    """Aggregate kill/death positions across all exports, grouped by map and zone."""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    # Per-map zone aggregates: map -> zone -> {kills, deaths, hs_kills, hs_deaths}
    map_zones: dict[str, dict[str, dict]] = {}
    total_matches = 0
    total_positions = 0
    maps_seen: set[str] = set()

    for f in sorted(export_dir.glob("*_coach.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        positions = data.get("kill_positions", [])
        if not positions:
            continue

        map_name = data.get("match", {}).get("map", "").lower()
        if not map_name:
            continue

        maps_seen.add(map_name)
        total_matches += 1

        if map_name not in map_zones:
            map_zones[map_name] = {}

        for pos in positions:
            total_positions += 1
            is_kill = pos.get("t") == "k"
            x = pos.get("x", 0)
            y = pos.get("y", 0)
            zone = _classify_zone(x, y, map_name)

            if zone not in map_zones[map_name]:
                map_zones[map_name][zone] = {
                    "kills": 0, "deaths": 0,
                    "hs_kills": 0, "hs_deaths": 0,
                    "weapons_kill": {}, "weapons_death": {},
                }
            z = map_zones[map_name][zone]
            weapon = pos.get("w", "unknown").replace("weapon_", "")

            if is_kill:
                z["kills"] += 1
                if pos.get("hs"):
                    z["hs_kills"] += 1
                z["weapons_kill"][weapon] = z["weapons_kill"].get(weapon, 0) + 1
            else:
                z["deaths"] += 1
                if pos.get("hs"):
                    z["hs_deaths"] += 1
                z["weapons_death"][weapon] = z["weapons_death"].get(weapon, 0) + 1

    if not map_zones:
        return {"has_data": False}

    # Build per-map summaries
    maps_data = {}
    global_tips = []
    worst_zone = None  # (map, zone, kd)
    best_zone = None

    for map_name in sorted(map_zones.keys()):
        zones = map_zones[map_name]
        zone_list = []
        map_kills = 0
        map_deaths = 0

        for zone_name, z in sorted(zones.items(), key=lambda x: x[1]["kills"] + x[1]["deaths"], reverse=True):
            total = z["kills"] + z["deaths"]
            kd = round(z["kills"] / z["deaths"], 2) if z["deaths"] else (z["kills"] if z["kills"] else 0)
            hs_pct = round(z["hs_kills"] / z["kills"] * 100, 1) if z["kills"] else 0
            top_kill_wpn = max(z["weapons_kill"], key=z["weapons_kill"].get) if z["weapons_kill"] else "-"
            top_death_wpn = max(z["weapons_death"], key=z["weapons_death"].get) if z["weapons_death"] else "-"
            map_kills += z["kills"]
            map_deaths += z["deaths"]

            zone_entry = {
                "name": zone_name,
                "kills": z["kills"],
                "deaths": z["deaths"],
                "total": total,
                "kd": kd,
                "hs_pct": hs_pct,
                "top_kill_weapon": top_kill_wpn,
                "top_death_weapon": top_death_wpn,
            }
            zone_list.append(zone_entry)

            # Track global best/worst (minimum 5 events to be relevant)
            if total >= 5:
                if worst_zone is None or kd < worst_zone[2]:
                    worst_zone = (map_name, zone_name, kd, z["kills"], z["deaths"])
                if best_zone is None or kd > best_zone[2]:
                    best_zone = (map_name, zone_name, kd, z["kills"], z["deaths"])

        map_kd = round(map_kills / map_deaths, 2) if map_deaths else 0
        maps_data[map_name] = {
            "zones": zone_list,
            "kills": map_kills,
            "deaths": map_deaths,
            "kd": map_kd,
        }

    # Generate tips
    if worst_zone and worst_zone[2] < 0.8:
        global_tips.append({
            "type": "warning",
            "text": f"Auf {worst_zone[0].capitalize()} {worst_zone[1]} ist dein K/D nur {worst_zone[2]} ({worst_zone[3]}K/{worst_zone[4]}D) — trainiere diese Position.",
        })
    if best_zone and best_zone[2] >= 1.5:
        global_tips.append({
            "type": "success",
            "text": f"Auf {best_zone[0].capitalize()} {best_zone[1]} dominierst du mit K/D {best_zone[2]} ({best_zone[3]}K/{best_zone[4]}D) — nutze das aus.",
        })

    # Check for predictability (single zone > 50% of all events on a map)
    for map_name, md in maps_data.items():
        map_total = md["kills"] + md["deaths"]
        if map_total < 20:
            continue
        for z in md["zones"]:
            if z["total"] / map_total > 0.50 and z["name"] != "Other":
                global_tips.append({
                    "type": "info",
                    "text": f"Auf {map_name.capitalize()} finden {round(z['total']/map_total*100)}% deiner Duelle in {z['name']} statt — du bist vorhersehbar.",
                })
                break

    # Death-heavy zones (more deaths than kills, min 8 events)
    death_zones = []
    for map_name, md in maps_data.items():
        for z in md["zones"]:
            if z["deaths"] > z["kills"] and z["total"] >= 8 and z["name"] != "Other":
                death_zones.append((map_name, z["name"], z["kd"], z["deaths"]))
    death_zones.sort(key=lambda x: x[2])
    for dz in death_zones[:3]:
        if dz[2] < 0.7:
            global_tips.append({
                "type": "warning",
                "text": f"{dz[0].capitalize()} {dz[1]}: K/D {dz[2]} mit {dz[3]} Deaths — vermeide diese Zone oder aendere dein Approach.",
            })

    # Map list for template selector
    map_list = sorted(maps_data.keys())
    default_map = map_list[0] if map_list else ""

    return {
        "has_data": True,
        "total_matches": total_matches,
        "total_positions": total_positions,
        "maps": maps_data,
        "map_list": map_list,
        "default_map": default_map,
        "tips": global_tips[:6],
        "supported_maps": list(MAP_ZONES.keys()),
    }


# ── Muskelgedaechtnis / Motor Skills Tracker ─────────────
def _build_motor_skills(cfg: dict) -> dict:
    """Track motor skill metrics over time: counter-strafe, crosshair, spray, accuracy."""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    entries = []  # chronological match entries with motor skill values

    for f in sorted(export_dir.glob("*_coach.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        match = data.get("match", {})
        player = data.get("player", {})
        if not player:
            continue

        cp = player.get("crosshair_placement", {})
        sp = player.get("spray_control", {})

        entries.append({
            "date": match.get("date", ""),
            "map": match.get("map", "?"),
            "counter_strafe": player.get("counter_strafe_score", 0) or player.get("counter_strafe_pct", 0),
            "crosshair": cp.get("avg_degrees", 0),
            "crosshair_kills": cp.get("kills_analyzed", 0),
            "recoil_index": sp.get("avg_recoil_index", 0) if sp else 0,
            "burst_kills": sp.get("burst_kills", 0) if sp else 0,
            "spray_kills": sp.get("spray_kills", 0) if sp else 0,
            "accuracy": player.get("accuracy", 0),
            "avg_inaccuracy": player.get("avg_inaccuracy_move", 0),
            "hs_pct": player.get("hs_pct", 0),
        })

    if len(entries) < 3:
        return {"has_data": False}

    # Define skills with metadata
    skills = [
        {
            "key": "counter_strafe", "label": "Counter-Strafe",
            "desc": "Anteil stehender Schuesse — misst Bewegungskontrolle",
            "unit": "%", "higher_better": True, "icon": "footprints",
            "color": "#4ade80",
        },
        {
            "key": "crosshair", "label": "Crosshair Placement",
            "desc": "Grad-Abweichung zum Gegnerkopf — niedriger = besser",
            "unit": "°", "higher_better": False, "icon": "crosshair",
            "color": "#f87171",
        },
        {
            "key": "recoil_index", "label": "Spray Control",
            "desc": "Recoil-Index — niedriger = praeziserer Spray",
            "unit": "", "higher_better": False, "icon": "target",
            "color": "#fbbf24",
        },
        {
            "key": "hs_pct", "label": "Headshot-Rate",
            "desc": "Anteil Kopfschuesse an allen Kills",
            "unit": "%", "higher_better": True, "icon": "zap",
            "color": "#9184d9",
        },
    ]

    # Compute per-skill stats
    n = len(entries)
    skill_data = []
    tips = []

    for sk in skills:
        key = sk["key"]
        values = [e[key] for e in entries if e[key] > 0]
        if len(values) < 3:
            continue

        current = values[-1]
        first5 = values[:5]
        last5 = values[-5:]
        all_avg = round(sum(values) / len(values), 1)
        first5_avg = round(sum(first5) / len(first5), 1)
        last5_avg = round(sum(last5) / len(last5), 1)
        best = round(max(values), 1) if sk["higher_better"] else round(min(values), 1)
        worst = round(min(values), 1) if sk["higher_better"] else round(max(values), 1)

        # Trend: compare first 5 vs last 5
        if sk["higher_better"]:
            delta = round(last5_avg - first5_avg, 1)
            improved = delta > 0
        else:
            delta = round(first5_avg - last5_avg, 1)
            improved = delta > 0

        # Rolling 5-match averages for chart
        rolling = []
        for i in range(len(values)):
            window = values[max(0, i - 4):i + 1]
            rolling.append(round(sum(window) / len(window), 1))

        trend_label = "verbessert" if improved else ("verschlechtert" if abs(delta) > 1 else "stabil")

        skill_data.append({
            **sk,
            "current": current,
            "all_avg": all_avg,
            "first5_avg": first5_avg,
            "last5_avg": last5_avg,
            "best": best,
            "worst": worst,
            "delta": delta,
            "delta_abs": abs(delta),
            "improved": improved,
            "trend_label": trend_label,
            "values": values,
            "rolling": rolling,
            "count": len(values),
        })

        # Tips
        if improved and abs(delta) >= 2:
            tips.append({
                "type": "success",
                "text": f"{sk['label']} {trend_label}: {first5_avg}{sk['unit']} → {last5_avg}{sk['unit']} (+{abs(delta)}{sk['unit']})",
            })
        elif not improved and abs(delta) >= 3:
            tips.append({
                "type": "warning",
                "text": f"{sk['label']} {trend_label}: {first5_avg}{sk['unit']} → {last5_avg}{sk['unit']} (-{abs(delta)}{sk['unit']})",
            })

    if not skill_data:
        return {"has_data": False}

    # Chart labels (match dates)
    chart_labels = [e.get("date", "")[-5:] + " " + e.get("map", "")[:3] for e in entries]

    return {
        "has_data": True,
        "total_matches": n,
        "skills": skill_data,
        "tips": tips[:6],
        "chart_labels": chart_labels,
    }


# ── Replay Bookmarks ─────────────────────────────────────
def _build_bookmarks(cfg: dict) -> dict:
    """Auto-generate replay review bookmarks from round timeline data."""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    all_bookmarks = []

    for f in sorted(export_dir.glob("*_coach.json"), reverse=True)[:30]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        match = data.get("match", {})
        player = data.get("player", {})
        timeline = data.get("round_timeline", [])
        if not timeline:
            continue

        demo_file = match.get("demo_file", "")
        map_name = match.get("map", "?")
        match_date = match.get("date", "?")
        match_label = f"{map_name} {match.get('score_own', '?')}:{match.get('score_enemy', '?')}"

        match_bookmarks = []

        for rd in timeline:
            rnum = rd.get("round", 0)
            events = rd.get("events", [])
            side = rd.get("side", "?")
            won = rd.get("won", False)
            kills_in_round = rd.get("player_kills", 0)
            died = rd.get("player_died", False)

            kill_events = [e for e in events if e.get("type") == "kill"]
            death_events = [e for e in events if e.get("type") == "death"]

            bookmark = None

            # Clutch situations (1vN)
            clutch_events = [e for e in events if e.get("type") == "clutch"]
            for ce in clutch_events:
                vs = ce.get("vs", 0)
                clutch_won = ce.get("won", False)
                if clutch_won:
                    bookmark = {
                        "type": "clutch_win", "priority": 1,
                        "icon": "trophy", "color": "#4ade80",
                        "label": f"Clutch 1v{vs} gewonnen",
                        "tip": "Schau dir an was du richtig gemacht hast — Positioning, Timing, Utility.",
                    }
                elif vs >= 2:
                    bookmark = {
                        "type": "clutch_loss", "priority": 2,
                        "icon": "x-circle", "color": "#f87171",
                        "label": f"Clutch 1v{vs} verloren",
                        "tip": "Was haette anders laufen koennen? Hast du Utility genutzt?",
                    }

            # Ace / 4K+ rounds
            if not bookmark and kills_in_round >= 4:
                bookmark = {
                    "type": "multi_kill", "priority": 1,
                    "icon": "flame", "color": "#fbbf24",
                    "label": f"{kills_in_round}K Runde" + (" — ACE!" if kills_in_round >= 5 else ""),
                    "tip": "Highlight-Runde — was hat zum Erfolg gefuehrt?",
                }

            # Opening death without trade (died first, team lost)
            if not bookmark and died and death_events:
                de = death_events[0]
                pct = de.get("pct", 50)
                if pct < 20 and not won:
                    bookmark = {
                        "type": "early_death", "priority": 3,
                        "icon": "skull", "color": "#f87171",
                        "label": f"Frueh gestorben ({pct}% der Runde) — Runde verloren",
                        "tip": f"Zu aggressiv gepeekt? Utility vor dem Push nutzen. Getoetet von {de.get('killer', '?')} mit {de.get('weapon', '?').replace('weapon_', '')}.",
                    }

            # 0 impact rounds — no kills, no assists, died
            if not bookmark and kills_in_round == 0 and died and not won:
                assists_in_round = rd.get("player_assists", 0)
                if assists_in_round == 0:
                    bookmark = {
                        "type": "invisible", "priority": 4,
                        "icon": "eye-off", "color": "#75798c",
                        "label": "Invisible Round — kein Impact",
                        "tip": "Konntest du Utility einsetzen oder Trade-Position einnehmen?",
                    }

            # Team had advantage but lost (throw)
            if not bookmark and not won and kills_in_round >= 1:
                team_kills = sum(1 for e in events if e.get("type") == "kill")
                team_deaths = sum(1 for e in events if e.get("type") == "death")
                if team_kills >= team_deaths + 1 and died:
                    bookmark = {
                        "type": "throw", "priority": 3,
                        "icon": "alert-triangle", "color": "#fb923c",
                        "label": f"Vorteil verspielt ({team_kills}K vs {team_deaths}D) — trotzdem verloren",
                        "tip": "Post-Plant Positioning? Zu aggressiv nach Vorteil?",
                    }

            if bookmark:
                bookmark["round"] = rnum
                bookmark["side"] = side
                bookmark["won"] = won
                match_bookmarks.append(bookmark)

        if match_bookmarks:
            # Sort by priority
            match_bookmarks.sort(key=lambda b: b["priority"])
            all_bookmarks.append({
                "match_label": match_label,
                "date": match_date,
                "map": map_name,
                "demo_file": demo_file,
                "result": match.get("result", "?"),
                "bookmarks": match_bookmarks[:8],
                "total_bookmarks": len(match_bookmarks),
            })

    if not all_bookmarks:
        return {"has_data": False}

    # Summary stats
    total_bm = sum(m["total_bookmarks"] for m in all_bookmarks)
    type_counts = {}
    for m in all_bookmarks:
        for b in m["bookmarks"]:
            type_counts[b["type"]] = type_counts.get(b["type"], 0) + 1

    return {
        "has_data": True,
        "matches": all_bookmarks,
        "total_bookmarks": total_bm,
        "total_matches": len(all_bookmarks),
        "type_counts": type_counts,
    }


def _build_utility_analysis(cfg: dict) -> dict:
    """Build comprehensive utility/grenade analysis across all matches."""
    vault_path = cfg.get("obsidian_vault_path", "")
    subfolder = cfg.get("coach_subfolder", "CS2-Coach")
    if not vault_path:
        return {"has_data": False}

    export_dir = Path(vault_path) / subfolder / "exports"
    if not export_dir.exists():
        return {"has_data": False}

    from collections import defaultdict

    # Accumulators
    matches_data = []  # per-match utility data for trends
    map_util = defaultdict(lambda: {"matches": 0, "total": 0, "flashes": 0, "smokes": 0,
                                     "he": 0, "molotovs": 0, "rounds": 0,
                                     "enemies_blinded": 0, "teammates_blinded": 0,
                                     "blind_dur_sum": 0.0, "blind_dur_count": 0,
                                     "wins": 0, "losses": 0, "ratings": []})
    side_util = {"ct": {"total": 0, "rounds": 0}, "t": {"total": 0, "rounds": 0}}

    for f in sorted(export_dir.glob("*_coach.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        match = data.get("match", {})
        player = data.get("player", {})
        util = player.get("utility", {})
        flash_eff = player.get("flash_effectiveness", {})
        total_rounds = match.get("total_rounds", 0)
        if not total_rounds:
            continue

        map_name = match.get("map", "?")
        result = match.get("result", "?")
        rating = player.get("rating", 0)
        date_str = match.get("date", match.get("datetime", "?"))

        flashes = util.get("flashes", 0)
        smokes = util.get("smokes", 0)
        he = util.get("he", 0)
        molotovs = util.get("molotovs", 0)
        total_util = util.get("total", flashes + smokes + he + molotovs)
        per_round = round(total_util / max(total_rounds, 1), 2)

        enemies_blinded = flash_eff.get("enemies_blinded", 0)
        teammates_blinded = flash_eff.get("teammates_blinded", 0)
        avg_blind_dur = flash_eff.get("avg_enemy_duration", 0)

        # Per-match record
        matches_data.append({
            "date": date_str,
            "map": map_name,
            "result": result,
            "rating": rating,
            "total": total_util,
            "per_round": per_round,
            "flashes": flashes,
            "smokes": smokes,
            "he": he,
            "molotovs": molotovs,
            "rounds": total_rounds,
            "enemies_blinded": enemies_blinded,
            "teammates_blinded": teammates_blinded,
            "avg_blind_dur": avg_blind_dur,
            "flash_ratio": round(enemies_blinded / max(flashes, 1), 2),
        })

        # Map aggregation
        m = map_util[map_name]
        m["matches"] += 1
        m["total"] += total_util
        m["flashes"] += flashes
        m["smokes"] += smokes
        m["he"] += he
        m["molotovs"] += molotovs
        m["rounds"] += total_rounds
        m["enemies_blinded"] += enemies_blinded
        m["teammates_blinded"] += teammates_blinded
        if avg_blind_dur > 0:
            m["blind_dur_sum"] += avg_blind_dur
            m["blind_dur_count"] += 1
        m["ratings"].append(rating)
        if result == "Sieg":
            m["wins"] += 1
        elif result == "Niederlage":
            m["losses"] += 1

        # Side aggregation from round_timeline
        for rd in data.get("round_timeline", []):
            side = rd.get("side", "").lower()
            if side in ("ct", "t"):
                side_util[side]["rounds"] += 1
                # Count utility events in this round (kills with utility weapons)
                # We approximate per-side total from overall split

    if not matches_data:
        return {"has_data": False}

    # ── Overall stats ──
    total_matches = len(matches_data)
    total_grenades = sum(m["total"] for m in matches_data)
    total_flashes = sum(m["flashes"] for m in matches_data)
    total_smokes = sum(m["smokes"] for m in matches_data)
    total_he = sum(m["he"] for m in matches_data)
    total_molotovs = sum(m["molotovs"] for m in matches_data)
    total_rounds_all = sum(m["rounds"] for m in matches_data)
    total_enemies_blinded = sum(m["enemies_blinded"] for m in matches_data)
    total_teammates_blinded = sum(m["teammates_blinded"] for m in matches_data)

    avg_per_round = round(total_grenades / max(total_rounds_all, 1), 2)
    avg_flashes_pr = round(total_flashes / max(total_rounds_all, 1), 2)
    avg_smokes_pr = round(total_smokes / max(total_rounds_all, 1), 2)
    avg_he_pr = round(total_he / max(total_rounds_all, 1), 2)
    avg_molotovs_pr = round(total_molotovs / max(total_rounds_all, 1), 2)

    # Flash effectiveness overall
    flash_hit_rate = round(total_enemies_blinded / max(total_flashes, 1) * 100, 1)
    friendly_flash_rate = round(total_teammates_blinded / max(total_flashes, 1) * 100, 1)
    blind_durations = [m["avg_blind_dur"] for m in matches_data if m["avg_blind_dur"] > 0]
    avg_blind_duration = round(sum(blind_durations) / max(len(blind_durations), 1), 2)

    # ── Distribution (pie chart data) ──
    distribution = [
        {"label": "Flash", "value": total_flashes, "color": "#fbbf24"},
        {"label": "Smoke", "value": total_smokes, "color": "#94a3b8"},
        {"label": "HE", "value": total_he, "color": "#f87171"},
        {"label": "Molotov", "value": total_molotovs, "color": "#fb923c"},
    ]

    # ── Trend (last 20 matches) ──
    trend = []
    for m in matches_data[-20:]:
        trend.append({
            "date": m["date"],
            "map": m["map"],
            "result": m["result"],
            "per_round": m["per_round"],
            "flash_ratio": m["flash_ratio"],
        })

    # Compare first half vs second half for trend direction
    if len(matches_data) >= 6:
        half = len(matches_data) // 2
        first_half_pr = sum(m["per_round"] for m in matches_data[:half]) / half
        second_half_pr = sum(m["per_round"] for m in matches_data[half:]) / (len(matches_data) - half)
        util_trend = round(second_half_pr - first_half_pr, 2)
    else:
        util_trend = 0.0

    # ── Per-map stats ──
    map_stats = []
    for map_name, m in sorted(map_util.items()):
        if m["matches"] < 1:
            continue
        map_stats.append({
            "map": map_name,
            "matches": m["matches"],
            "per_round": round(m["total"] / max(m["rounds"], 1), 2),
            "flashes_pr": round(m["flashes"] / max(m["rounds"], 1), 2),
            "smokes_pr": round(m["smokes"] / max(m["rounds"], 1), 2),
            "he_pr": round(m["he"] / max(m["rounds"], 1), 2),
            "molotovs_pr": round(m["molotovs"] / max(m["rounds"], 1), 2),
            "flash_hit_rate": round(m["enemies_blinded"] / max(m["flashes"], 1) * 100, 1),
            "friendly_rate": round(m["teammates_blinded"] / max(m["flashes"], 1) * 100, 1),
            "avg_blind_dur": round(m["blind_dur_sum"] / max(m["blind_dur_count"], 1), 2),
            "avg_rating": round(sum(m["ratings"]) / max(len(m["ratings"]), 1), 2),
            "wins": m["wins"],
            "losses": m["losses"],
        })
    map_stats.sort(key=lambda x: x["per_round"], reverse=True)

    # ── Utility-Rating correlation ──
    # Split matches into high/low utility usage and compare ratings
    sorted_by_util = sorted(matches_data, key=lambda x: x["per_round"])
    third = max(len(sorted_by_util) // 3, 1)
    low_util = sorted_by_util[:third]
    high_util = sorted_by_util[-third:]
    low_avg_rating = round(sum(m["rating"] for m in low_util) / max(len(low_util), 1), 2)
    high_avg_rating = round(sum(m["rating"] for m in high_util) / max(len(high_util), 1), 2)

    low_avg_wr = round(sum(1 for m in low_util if m["result"] == "Sieg") / max(len(low_util), 1) * 100, 1)
    high_avg_wr = round(sum(1 for m in high_util if m["result"] == "Sieg") / max(len(high_util), 1) * 100, 1)

    correlation = {
        "low_util_pr": round(sum(m["per_round"] for m in low_util) / max(len(low_util), 1), 2),
        "low_rating": low_avg_rating,
        "low_wr": low_avg_wr,
        "high_util_pr": round(sum(m["per_round"] for m in high_util) / max(len(high_util), 1), 2),
        "high_rating": high_avg_rating,
        "high_wr": high_avg_wr,
        "rating_diff": round(high_avg_rating - low_avg_rating, 2),
        "wr_diff": round(high_avg_wr - low_avg_wr, 1),
    }

    # ── Benchmarks (typical CS2 values for comparison) ──
    benchmarks = [
        {"label": "Utility/Runde", "value": avg_per_round, "benchmark": 1.5,
         "desc": "Durchschnittliche Granaten pro Runde", "unit": "",
         "rank": "Gut" if avg_per_round >= 1.5 else "Unterdurchschnittlich" if avg_per_round < 1.0 else "Durchschnitt"},
        {"label": "Flash-Trefferquote", "value": flash_hit_rate, "benchmark": 60,
         "desc": "Prozent der Flashes die Gegner blenden", "unit": "%",
         "rank": "Stark" if flash_hit_rate >= 60 else "Schwach" if flash_hit_rate < 35 else "Durchschnitt"},
        {"label": "Team-Flash-Rate", "value": friendly_flash_rate, "benchmark": 30,
         "desc": "Prozent der Flashes die Teammates blenden (niedriger = besser)", "unit": "%",
         "rank": "Gut" if friendly_flash_rate <= 25 else "Zu hoch" if friendly_flash_rate > 45 else "Durchschnitt",
         "invert": True},
        {"label": "Blind-Dauer", "value": avg_blind_duration, "benchmark": 2.0,
         "desc": "Durchschnittliche Blendzeit bei Gegnern", "unit": "s",
         "rank": "Effektiv" if avg_blind_duration >= 2.0 else "Zu kurz" if avg_blind_duration < 1.2 else "OK"},
    ]

    # ── Coaching insights ──
    insights = []
    if avg_per_round < 1.0:
        insights.append({"type": "warning", "icon": "alert-triangle",
                         "text": f"Nur {avg_per_round} Utility/Runde — das ist sehr wenig. Versuche jede Runde mindestens 1 Granate zu nutzen."})
    elif avg_per_round >= 1.8:
        insights.append({"type": "success", "icon": "check-circle",
                         "text": f"Starke Utility-Nutzung mit {avg_per_round}/Runde — du setzt deine Granaten aktiv ein."})

    if flash_hit_rate < 35 and total_flashes >= 20:
        insights.append({"type": "warning", "icon": "alert-triangle",
                         "text": f"Flash-Trefferquote bei nur {flash_hit_rate}% — uebe Pop-Flashes und lerne die Standard-Flash-Lineups fuer deine Maps."})
    elif flash_hit_rate >= 65 and total_flashes >= 20:
        insights.append({"type": "success", "icon": "check-circle",
                         "text": f"Exzellente Flash-Trefferquote von {flash_hit_rate}% — deine Flashes sind effektiv."})

    if friendly_flash_rate > 45 and total_flashes >= 20:
        insights.append({"type": "warning", "icon": "alert-triangle",
                         "text": f"Team-Flash-Rate bei {friendly_flash_rate}% — achte auf Teammate-Positionen vor dem Flashen."})

    if total_smokes < total_flashes * 0.5 and total_matches >= 5:
        insights.append({"type": "info", "icon": "info",
                         "text": "Du wirfst deutlich mehr Flashes als Smokes. Smokes sind entscheidend fuer Map-Kontrolle — nutze sie haeufiger."})

    if correlation["rating_diff"] > 0.1:
        insights.append({"type": "success", "icon": "trending-up",
                         "text": f"Wenn du viel Utility nutzt ({correlation['high_util_pr']}/Rd), ist dein Rating {correlation['rating_diff']:.2f} hoeher als bei wenig Utility ({correlation['low_util_pr']}/Rd)."})
    elif correlation["rating_diff"] < -0.05:
        insights.append({"type": "info", "icon": "info",
                         "text": "Dein Rating ist bei weniger Utility-Nutzung hoeher — vielleicht konzentrierst du dich zu sehr auf Granaten statt auf Positioning."})

    if util_trend > 0.15:
        insights.append({"type": "success", "icon": "trending-up",
                         "text": f"Deine Utility-Nutzung steigt — +{util_trend:.2f}/Runde im Vergleich zur ersten Haelfte deiner Matches."})
    elif util_trend < -0.15:
        insights.append({"type": "warning", "icon": "trending-down",
                         "text": f"Deine Utility-Nutzung sinkt — {util_trend:.2f}/Runde weniger als frueher."})

    # Best/worst map for utility
    if len(map_stats) >= 2:
        best_util_map = max(map_stats, key=lambda x: x["per_round"])
        worst_util_map = min(map_stats, key=lambda x: x["per_round"])
        if best_util_map["per_round"] - worst_util_map["per_round"] > 0.3:
            insights.append({"type": "info", "icon": "map",
                             "text": f"Groesster Utility-Unterschied: {best_util_map['map']} ({best_util_map['per_round']}/Rd) vs {worst_util_map['map']} ({worst_util_map['per_round']}/Rd) — uebe Lineups fuer {worst_util_map['map']}."})

    return {
        "has_data": True,
        "total_matches": total_matches,
        "total_grenades": total_grenades,
        "total_rounds": total_rounds_all,
        "avg_per_round": avg_per_round,
        "avg_flashes_pr": avg_flashes_pr,
        "avg_smokes_pr": avg_smokes_pr,
        "avg_he_pr": avg_he_pr,
        "avg_molotovs_pr": avg_molotovs_pr,
        "total_flashes": total_flashes,
        "total_smokes": total_smokes,
        "total_he": total_he,
        "total_molotovs": total_molotovs,
        "flash_hit_rate": flash_hit_rate,
        "friendly_flash_rate": friendly_flash_rate,
        "avg_blind_duration": avg_blind_duration,
        "total_enemies_blinded": total_enemies_blinded,
        "total_teammates_blinded": total_teammates_blinded,
        "distribution": distribution,
        "trend": trend,
        "util_trend": util_trend,
        "map_stats": map_stats,
        "correlation": correlation,
        "benchmarks": benchmarks,
        "insights": insights,
    }


# ── Calendar Heatmap ─────────────────────────────────────────
def _build_calendar_data(cfg: dict) -> dict:
    """Build GitHub-style performance heatmap data from all exports."""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    exports = []
    for f in sorted(export_dir.glob("*_coach.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        mi = data.get("match", {})
        pl = data.get("player", {})
        dt_str = mi.get("datetime", mi.get("date", ""))
        date_str = dt_str[:10] if dt_str else ""
        if not date_str:
            continue
        hour = -1
        if len(dt_str) >= 16:
            try:
                hour = int(dt_str[11:13])
            except ValueError:
                pass
        result = mi.get("result", "?")
        exports.append({
            "date": date_str,
            "hour": hour,
            "map": mi.get("map", "?"),
            "score": f"{mi.get('score_own', '?')}:{mi.get('score_enemy', '?')}",
            "result": result,
            "rating": pl.get("rating", 0),
            "kd": round(pl.get("kills", 0) / max(pl.get("deaths", 1), 1), 2),
            "adr": pl.get("adr", 0),
            "kills": pl.get("kills", 0),
            "deaths": pl.get("deaths", 0),
            "filename": f.stem.replace("_coach", ""),
        })

    if not exports:
        return {"has_data": False}

    # ── Group by date ──
    by_date: dict[str, list[dict]] = {}
    for e in exports:
        by_date.setdefault(e["date"], []).append(e)

    # ── Build day summaries ──
    day_summaries: dict[str, dict] = {}
    for date, matches in by_date.items():
        n = len(matches)
        avg_rating = round(sum(m["rating"] for m in matches) / n, 2)
        wins = sum(1 for m in matches if m["result"] == "Sieg")
        losses = sum(1 for m in matches if m["result"] == "Niederlage")
        total_kills = sum(m["kills"] for m in matches)
        total_deaths = sum(m["deaths"] for m in matches)
        avg_adr = round(sum(m["adr"] for m in matches) / n, 1)
        day_summaries[date] = {
            "date": date,
            "matches": n,
            "avg_rating": avg_rating,
            "wins": wins,
            "losses": losses,
            "draws": n - wins - losses,
            "win_rate": round(wins / n * 100, 1),
            "total_kills": total_kills,
            "total_deaths": total_deaths,
            "kd": round(total_kills / max(total_deaths, 1), 2),
            "avg_adr": avg_adr,
            "match_list": matches,
        }

    # ── Calendar grid (last 365 days) ──
    from datetime import timedelta
    today = datetime.now().date()
    start = today - timedelta(days=364)
    # Align to Monday (weekday 0)
    start = start - timedelta(days=start.weekday())

    weeks = []
    current = start
    all_ratings = [d["avg_rating"] for d in day_summaries.values()]
    rating_min = min(all_ratings) if all_ratings else 0
    rating_max = max(all_ratings) if all_ratings else 2
    rating_range = max(rating_max - rating_min, 0.1)

    while current <= today:
        week = []
        for dow in range(7):
            d = current + timedelta(days=dow)
            ds = d.isoformat()
            info = day_summaries.get(ds)
            if info:
                norm = (info["avg_rating"] - rating_min) / rating_range
                level = min(4, max(1, int(norm * 4) + 1))
                if info["win_rate"] < 40:
                    color_type = "loss"
                elif info["win_rate"] >= 60:
                    color_type = "win"
                else:
                    color_type = "draw"
            else:
                level = 0
                color_type = "empty"
                info = None
            week.append({
                "date": ds,
                "level": level,
                "color_type": color_type,
                "info": info,
                "is_future": d > today,
            })
        weeks.append(week)
        current += timedelta(days=7)

    # ── Weekday analysis ──
    weekday_names = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    weekday_stats = []
    for dow in range(7):
        dow_matches = [e for e in exports if e["date"] and datetime.strptime(e["date"], "%Y-%m-%d").weekday() == dow]
        if dow_matches:
            n = len(dow_matches)
            wr = round(sum(1 for m in dow_matches if m["result"] == "Sieg") / n * 100, 1)
            avg_r = round(sum(m["rating"] for m in dow_matches) / n, 2)
            weekday_stats.append({"day": weekday_names[dow], "matches": n, "win_rate": wr, "avg_rating": avg_r})
        else:
            weekday_stats.append({"day": weekday_names[dow], "matches": 0, "win_rate": 0, "avg_rating": 0})

    best_weekday = max(weekday_stats, key=lambda w: w["avg_rating"]) if weekday_stats else None
    worst_weekday = min((w for w in weekday_stats if w["matches"] >= 2), key=lambda w: w["avg_rating"], default=None)

    # ── Hour-of-day analysis ──
    hour_stats = []
    for h in range(24):
        h_matches = [e for e in exports if e["hour"] == h]
        if h_matches:
            n = len(h_matches)
            wr = round(sum(1 for m in h_matches if m["result"] == "Sieg") / n * 100, 1)
            avg_r = round(sum(m["rating"] for m in h_matches) / n, 2)
            hour_stats.append({"hour": h, "label": f"{h:02d}:00", "matches": n, "win_rate": wr, "avg_rating": avg_r})
        else:
            hour_stats.append({"hour": h, "label": f"{h:02d}:00", "matches": 0, "win_rate": 0, "avg_rating": 0})

    active_hours = [h for h in hour_stats if h["matches"] >= 2]
    best_hour = max(active_hours, key=lambda h: h["avg_rating"]) if active_hours else None
    worst_hour = min(active_hours, key=lambda h: h["avg_rating"]) if active_hours else None

    # ── Monthly breakdown ──
    monthly: dict[str, list[dict]] = {}
    for e in exports:
        month_key = e["date"][:7]
        monthly.setdefault(month_key, []).append(e)

    month_stats = []
    for month_key in sorted(monthly.keys()):
        matches = monthly[month_key]
        n = len(matches)
        wins = sum(1 for m in matches if m["result"] == "Sieg")
        avg_r = round(sum(m["rating"] for m in matches) / n, 2)
        month_stats.append({
            "month": month_key,
            "matches": n,
            "wins": wins,
            "losses": sum(1 for m in matches if m["result"] == "Niederlage"),
            "win_rate": round(wins / n * 100, 1),
            "avg_rating": avg_r,
        })

    # ── Streaks ──
    sorted_dates = sorted(day_summaries.keys())
    active_days = len(sorted_dates)
    total_matches = len(exports)

    longest_streak = 0
    current_streak = 0
    prev_date = None
    for ds in sorted_dates:
        d = datetime.strptime(ds, "%Y-%m-%d").date()
        if prev_date and (d - prev_date).days == 1:
            current_streak += 1
        else:
            current_streak = 1
        longest_streak = max(longest_streak, current_streak)
        prev_date = d

    best_day = max(day_summaries.values(), key=lambda d: d["avg_rating"])
    worst_day = min(day_summaries.values(), key=lambda d: d["avg_rating"])

    # ── Insights ──
    insights = []
    if best_weekday and worst_weekday and best_weekday["day"] != worst_weekday["day"]:
        insights.append({
            "type": "success", "icon": "calendar-check",
            "text": f"Bester Tag: {best_weekday['day']} (Rating {best_weekday['avg_rating']}, {best_weekday['win_rate']}% WR) — schlechtester: {worst_weekday['day']} ({worst_weekday['avg_rating']})"
        })
    if best_hour:
        insights.append({
            "type": "info", "icon": "clock",
            "text": f"Beste Uhrzeit: {best_hour['label']} Uhr (Rating {best_hour['avg_rating']}, {best_hour['matches']} Matches)"
        })
    if worst_hour and best_hour and worst_hour["hour"] != best_hour["hour"]:
        insights.append({
            "type": "warning", "icon": "clock",
            "text": f"Schlechteste Uhrzeit: {worst_hour['label']} Uhr (Rating {worst_hour['avg_rating']}, {worst_hour['matches']} Matches)"
        })
    if longest_streak >= 3:
        insights.append({
            "type": "info", "icon": "flame",
            "text": f"Laengste Spiel-Serie: {longest_streak} Tage am Stueck"
        })

    avg_per_day = round(total_matches / max(active_days, 1), 1)
    if avg_per_day > 4:
        insights.append({
            "type": "warning", "icon": "alert-triangle",
            "text": f"Durchschnittlich {avg_per_day} Matches pro Spieltag — Ermuedung beachten!"
        })

    return {
        "has_data": True,
        "weeks": weeks,
        "day_summaries": day_summaries,
        "weekday_stats": weekday_stats,
        "hour_stats": hour_stats,
        "month_stats": month_stats,
        "best_day": best_day,
        "worst_day": worst_day,
        "best_weekday": best_weekday,
        "worst_weekday": worst_weekday,
        "best_hour": best_hour,
        "worst_hour": worst_hour,
        "active_days": active_days,
        "total_matches": total_matches,
        "avg_per_day": avg_per_day,
        "longest_streak": longest_streak,
        "insights": insights,
    }


# ── Duel Analysis ────────────────────────────────────────────
def _build_duel_analysis(cfg: dict) -> dict:
    """Analyze 1v1 duel patterns: weapons, distances, opponents, and fight outcomes."""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    import math

    # Aggregators
    total_kills = 0
    total_deaths = 0
    total_hs_kills = 0
    total_hs_deaths = 0
    weapon_kills: dict[str, int] = {}
    weapon_deaths: dict[str, int] = {}
    weapon_hs_kills: dict[str, int] = {}
    weapon_hs_deaths: dict[str, int] = {}
    opponent_duels: dict[str, dict] = {}  # name -> {kills, deaths, hs_k, hs_d, weapons_k, weapons_d}
    distances: list[float] = []
    dist_kills = {"close": 0, "mid": 0, "long": 0}
    dist_deaths = {"close": 0, "mid": 0, "long": 0}
    opening_wins = 0
    opening_losses = 0
    # Fight timing: when in the round did kills/deaths happen (pct of round)
    kill_timings: list[float] = []
    death_timings: list[float] = []
    # Per-match trend
    match_duels: list[dict] = []
    match_count = 0

    for f in sorted(export_dir.glob("*_coach.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        mi = data.get("match", {})
        pl = data.get("player", {})
        timeline = data.get("round_timeline", [])
        positions = data.get("kill_positions", [])
        duel_matrix = data.get("duel_matrix", [])

        if not timeline and not duel_matrix:
            continue

        match_count += 1
        m_kills = 0
        m_deaths = 0
        m_hs_k = 0
        m_hs_d = 0

        # Engagement distances from player stats
        eng = pl.get("engagement_distance", {})
        dist_kills["close"] += eng.get("close", 0)
        dist_kills["mid"] += eng.get("mid", 0)
        dist_kills["long"] += eng.get("long", 0)
        if eng.get("avg"):
            distances.append(eng["avg"])

        # Opening duels
        opening_wins += pl.get("opening_kills", 0)
        opening_losses += pl.get("opening_deaths", 0)

        # Duel matrix opponents
        if isinstance(duel_matrix, list):
            for opp in duel_matrix:
                name = opp.get("name", "?")
                if name not in opponent_duels:
                    opponent_duels[name] = {"kills": 0, "deaths": 0, "hs_k": 0, "hs_d": 0,
                                            "weapons_k": {}, "weapons_d": {}, "matches": 0}
                opponent_duels[name]["kills"] += opp.get("kills", 0)
                opponent_duels[name]["deaths"] += opp.get("deaths", 0)
                opponent_duels[name]["hs_k"] += opp.get("hs_kills", 0)
                opponent_duels[name]["hs_d"] += opp.get("hs_deaths", 0)
                opponent_duels[name]["matches"] += 1
                for w in opp.get("top_weapons", []):
                    opponent_duels[name]["weapons_k"][w] = opponent_duels[name]["weapons_k"].get(w, 0) + 1

        # Kill/death events from timeline
        for rd in timeline:
            is_opening_event = True
            for e in rd.get("events", []):
                etype = e.get("type")
                if etype == "kill":
                    w = e.get("weapon", "unknown").replace("weapon_", "")
                    hs = e.get("headshot", False)
                    pct = e.get("pct", 50)
                    m_kills += 1
                    total_kills += 1
                    weapon_kills[w] = weapon_kills.get(w, 0) + 1
                    if hs:
                        m_hs_k += 1
                        total_hs_kills += 1
                        weapon_hs_kills[w] = weapon_hs_kills.get(w, 0) + 1
                    kill_timings.append(pct)
                elif etype == "death":
                    w = e.get("weapon", "unknown").replace("weapon_", "")
                    hs = e.get("headshot", False)
                    pct = e.get("pct", 50)
                    killer = e.get("killer", "?")
                    m_deaths += 1
                    total_deaths += 1
                    weapon_deaths[w] = weapon_deaths.get(w, 0) + 1
                    if hs:
                        m_hs_d += 1
                        total_hs_deaths += 1
                        weapon_hs_deaths[w] = weapon_hs_deaths.get(w, 0) + 1
                    death_timings.append(pct)
                    # Track death weapons by opponent
                    if killer and killer != "?" and killer in opponent_duels:
                        opponent_duels[killer]["weapons_d"][w] = opponent_duels[killer]["weapons_d"].get(w, 0) + 1

        # Kill positions for distance calculation
        for kp in positions:
            if kp.get("t") == "k":
                dx = kp.get("x", 0) - kp.get("ex", 0)
                dy = kp.get("y", 0) - kp.get("ey", 0)
                dist = math.sqrt(dx * dx + dy * dy)
                distances.append(dist)

        match_duels.append({
            "date": mi.get("date", "?"),
            "map": mi.get("map", "?"),
            "kills": m_kills,
            "deaths": m_deaths,
            "hs_kills": m_hs_k,
            "hs_deaths": m_hs_d,
            "hs_pct": round(m_hs_k / m_kills * 100, 1) if m_kills else 0,
            "kd": round(m_kills / max(m_deaths, 1), 2),
        })

    if not match_duels:
        return {"has_data": False}

    # ── Weapon Duel Stats ──
    all_weapons = set(weapon_kills.keys()) | set(weapon_deaths.keys())
    weapon_stats = []
    for w in all_weapons:
        k = weapon_kills.get(w, 0)
        d = weapon_deaths.get(w, 0)
        total = k + d
        if total < 2:
            continue
        weapon_stats.append({
            "weapon": w,
            "kills": k,
            "deaths": d,
            "total": total,
            "kd": round(k / max(d, 1), 2),
            "win_rate": round(k / total * 100, 1),
            "hs_kills": weapon_hs_kills.get(w, 0),
            "hs_deaths": weapon_hs_deaths.get(w, 0),
            "hs_pct": round(weapon_hs_kills.get(w, 0) / k * 100, 1) if k else 0,
        })
    weapon_stats.sort(key=lambda w: w["total"], reverse=True)

    # ── Opponent Rankings ──
    opponent_list = []
    for name, info in opponent_duels.items():
        total = info["kills"] + info["deaths"]
        if total < 2:
            continue
        fav_weapon = max(info["weapons_k"], key=info["weapons_k"].get) if info["weapons_k"] else "?"
        killed_by = max(info["weapons_d"], key=info["weapons_d"].get) if info["weapons_d"] else "?"
        opponent_list.append({
            "name": name,
            "kills": info["kills"],
            "deaths": info["deaths"],
            "total": total,
            "kd": round(info["kills"] / max(info["deaths"], 1), 2),
            "win_rate": round(info["kills"] / total * 100, 1),
            "hs_pct": round(info["hs_k"] / info["kills"] * 100, 1) if info["kills"] else 0,
            "matches": info["matches"],
            "fav_weapon": fav_weapon,
            "killed_by": killed_by,
        })
    opponent_list.sort(key=lambda o: o["total"], reverse=True)

    # Nemeses (worst KD, min 3 duels)
    nemeses = sorted([o for o in opponent_list if o["total"] >= 3 and o["kd"] < 1.0],
                     key=lambda o: o["kd"])[:5]
    # Victims (best KD, min 3 duels)
    victims = sorted([o for o in opponent_list if o["total"] >= 3 and o["kd"] > 1.0],
                     key=lambda o: -o["kd"])[:5]

    # ── Distance breakdown ──
    avg_dist = round(sum(distances) / len(distances), 1) if distances else 0
    dist_total_k = dist_kills["close"] + dist_kills["mid"] + dist_kills["long"]
    dist_pcts = {k: round(v / dist_total_k * 100, 1) if dist_total_k else 0 for k, v in dist_kills.items()}

    # ── Opening duels ──
    opening_total = opening_wins + opening_losses
    opening_wr = round(opening_wins / opening_total * 100, 1) if opening_total else 0

    # ── Headshot analysis ──
    hs_kill_pct = round(total_hs_kills / total_kills * 100, 1) if total_kills else 0
    hs_death_pct = round(total_hs_deaths / total_deaths * 100, 1) if total_deaths else 0

    # ── Kill/Death timing distribution ──
    def _timing_buckets(timings: list[float]) -> dict:
        buckets = {"early": 0, "mid": 0, "late": 0}
        for t in timings:
            if t <= 33:
                buckets["early"] += 1
            elif t <= 66:
                buckets["mid"] += 1
            else:
                buckets["late"] += 1
        total = len(timings) or 1
        return {k: {"count": v, "pct": round(v / total * 100, 1)} for k, v in buckets.items()}

    kill_timing_dist = _timing_buckets(kill_timings)
    death_timing_dist = _timing_buckets(death_timings)

    # ── Trend (last 20 matches) ──
    trend = match_duels[-20:] if match_duels else []

    # ── Insights ──
    insights = []
    if opening_wr >= 55 and opening_total >= 10:
        insights.append({"type": "success", "icon": "swords",
                         "text": f"Opening Duel Winrate {opening_wr}% ({opening_wins}/{opening_total}) — du gewinnst Erstduelle ueberdurchschnittlich."})
    elif opening_wr < 45 and opening_total >= 10:
        insights.append({"type": "warning", "icon": "swords",
                         "text": f"Opening Duel Winrate nur {opening_wr}% ({opening_wins}/{opening_total}) — arbeite an Crosshair Placement und Peeking."})

    if hs_kill_pct >= 50:
        insights.append({"type": "success", "icon": "target",
                         "text": f"Headshot-Quote {hs_kill_pct}% — exzellentes Aim!"})
    elif hs_kill_pct < 30 and total_kills > 50:
        insights.append({"type": "warning", "icon": "target",
                         "text": f"Headshot-Quote nur {hs_kill_pct}% — mehr auf Kopfhoehe zielen."})

    if hs_death_pct > 60 and total_deaths > 30:
        insights.append({"type": "info", "icon": "crosshair",
                         "text": f"{hs_death_pct}% deiner Tode sind Headshots — Gegner zielen praezise auf dich. Nutze mehr Off-Angles."})

    if dist_pcts.get("close", 0) > 45:
        insights.append({"type": "info", "icon": "move",
                         "text": f"{dist_pcts['close']}% deiner Kills auf Nahkampf-Distanz — du spielst sehr aggressiv."})
    elif dist_pcts.get("long", 0) > 40:
        insights.append({"type": "info", "icon": "eye",
                         "text": f"{dist_pcts['long']}% deiner Kills auf langer Distanz — passiver Spielstil oder AWP-lastig."})

    # Best/worst weapon
    rifle_weapons = [w for w in weapon_stats if w["weapon"] in ("ak47", "m4a1_silencer", "m4a1", "sg556", "aug", "galil", "famas")]
    if rifle_weapons:
        best_rifle = max(rifle_weapons, key=lambda w: w["kd"])
        worst_rifle = min(rifle_weapons, key=lambda w: w["kd"])
        if best_rifle["weapon"] != worst_rifle["weapon"] and worst_rifle["total"] >= 5:
            insights.append({"type": "info", "icon": "crosshair",
                             "text": f"Beste Rifle: {best_rifle['weapon']} ({best_rifle['kd']} K/D) — schwaeachste: {worst_rifle['weapon']} ({worst_rifle['kd']} K/D)"})

    return {
        "has_data": True,
        "match_count": match_count,
        "total_kills": total_kills,
        "total_deaths": total_deaths,
        "overall_kd": round(total_kills / max(total_deaths, 1), 2),
        "hs_kill_pct": hs_kill_pct,
        "hs_death_pct": hs_death_pct,
        "opening": {"wins": opening_wins, "losses": opening_losses, "total": opening_total, "wr": opening_wr},
        "distance": {"avg": avg_dist, "close": dist_kills["close"], "mid": dist_kills["mid"], "long": dist_kills["long"],
                     "pcts": dist_pcts},
        "weapon_stats": weapon_stats[:15],
        "opponent_list": opponent_list[:20],
        "nemeses": nemeses,
        "victims": victims,
        "kill_timing": kill_timing_dist,
        "death_timing": death_timing_dist,
        "trend": trend,
        "insights": insights,
    }


# ── Side Split Analysis ──────────────────────────────────────
def _build_side_analysis(cfg: dict) -> dict:
    """Deep CT vs T side performance comparison with per-map breakdown."""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    # Aggregators
    ct = {"kills": 0, "deaths": 0, "rounds": 0, "rounds_won": 0, "plants_stopped": 0, "defuses": 0,
          "opening_kills": 0, "opening_deaths": 0, "hs_kills": 0}
    t = {"kills": 0, "deaths": 0, "rounds": 0, "rounds_won": 0, "plants": 0, "self_plants": 0,
         "opening_kills": 0, "opening_deaths": 0, "hs_kills": 0}
    ct_weapons: dict[str, int] = {}
    t_weapons: dict[str, int] = {}
    map_sides: dict[str, dict] = {}  # map -> {ct_rounds, ct_won, t_rounds, t_won, ct_kills, ...}
    match_trend: list[dict] = []
    match_count = 0

    for f in sorted(export_dir.glob("*_coach.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        mi = data.get("match", {})
        pl = data.get("player", {})
        timeline = data.get("round_timeline", [])
        side_split = pl.get("side_split", {})

        if not timeline:
            continue

        match_count += 1
        map_name = mi.get("map", "?")

        if map_name not in map_sides:
            map_sides[map_name] = {"ct_rounds": 0, "ct_won": 0, "t_rounds": 0, "t_won": 0,
                                   "ct_kills": 0, "ct_deaths": 0, "t_kills": 0, "t_deaths": 0, "matches": 0}
        map_sides[map_name]["matches"] += 1

        m_ct_k = side_split.get("ct_kills", 0)
        m_ct_d = side_split.get("ct_deaths", 0)
        m_t_k = side_split.get("t_kills", 0)
        m_t_d = side_split.get("t_deaths", 0)

        ct["kills"] += m_ct_k
        ct["deaths"] += m_ct_d
        t["kills"] += m_t_k
        t["deaths"] += m_t_d

        map_sides[map_name]["ct_kills"] += m_ct_k
        map_sides[map_name]["ct_deaths"] += m_ct_d
        map_sides[map_name]["t_kills"] += m_t_k
        map_sides[map_name]["t_deaths"] += m_t_d

        m_ct_rounds = 0
        m_ct_won = 0
        m_t_rounds = 0
        m_t_won = 0

        for rd in timeline:
            side = rd.get("side", "?")
            won = rd.get("won", False)
            events = rd.get("events", [])
            kills_in_round = rd.get("player_kills", 0)
            died = rd.get("player_died", False)
            died_early = rd.get("died_early", False)

            if side == "CT":
                ct["rounds"] += 1
                m_ct_rounds += 1
                map_sides[map_name]["ct_rounds"] += 1
                if won:
                    ct["rounds_won"] += 1
                    m_ct_won += 1
                    map_sides[map_name]["ct_won"] += 1

                # Track CT-specific events
                for e in events:
                    if e.get("type") == "bomb_defuse" and e.get("is_self"):
                        ct["defuses"] += 1
                    if e.get("type") == "kill":
                        w = e.get("weapon", "").replace("weapon_", "")
                        ct_weapons[w] = ct_weapons.get(w, 0) + 1
                        if e.get("headshot"):
                            ct["hs_kills"] += 1
                    if e.get("type") == "death" and died_early:
                        ct["opening_deaths"] += 1

                if kills_in_round > 0 and not any(
                    e.get("type") == "kill" for e2 in events
                    for e in [e2]
                    if events.index(e2) > 0
                ):
                    pass  # first kill tracking handled below

            elif side == "T":
                t["rounds"] += 1
                m_t_rounds += 1
                map_sides[map_name]["t_rounds"] += 1
                if won:
                    t["rounds_won"] += 1
                    m_t_won += 1
                    map_sides[map_name]["t_won"] += 1

                for e in events:
                    if e.get("type") == "bomb_plant":
                        t["plants"] += 1
                        if e.get("is_self"):
                            t["self_plants"] += 1
                    if e.get("type") == "kill":
                        w = e.get("weapon", "").replace("weapon_", "")
                        t_weapons[w] = t_weapons.get(w, 0) + 1
                        if e.get("headshot"):
                            t["hs_kills"] += 1
                    if e.get("type") == "death" and died_early:
                        t["opening_deaths"] += 1

        match_trend.append({
            "date": mi.get("date", "?"),
            "map": map_name,
            "ct_kd": round(m_ct_k / max(m_ct_d, 1), 2),
            "t_kd": round(m_t_k / max(m_t_d, 1), 2),
            "ct_wr": round(m_ct_won / max(m_ct_rounds, 1) * 100, 1),
            "t_wr": round(m_t_won / max(m_t_rounds, 1) * 100, 1),
            "ct_rounds": m_ct_rounds,
            "t_rounds": m_t_rounds,
        })

    if not match_trend:
        return {"has_data": False}

    # ── Computed stats ──
    ct_kd = round(ct["kills"] / max(ct["deaths"], 1), 2)
    t_kd = round(t["kills"] / max(t["deaths"], 1), 2)
    ct_wr = round(ct["rounds_won"] / max(ct["rounds"], 1) * 100, 1)
    t_wr = round(t["rounds_won"] / max(t["rounds"], 1) * 100, 1)
    ct_hs = round(ct["hs_kills"] / max(ct["kills"], 1) * 100, 1) if ct["kills"] else 0
    t_hs = round(t["hs_kills"] / max(t["kills"], 1) * 100, 1) if t["kills"] else 0
    ct_kpr = round(ct["kills"] / max(ct["rounds"], 1), 2)
    t_kpr = round(t["kills"] / max(t["rounds"], 1), 2)
    ct_dpr = round(ct["deaths"] / max(ct["rounds"], 1), 2)
    t_dpr = round(t["deaths"] / max(t["rounds"], 1), 2)

    # ── Dominant side ──
    dominant = "CT" if ct_wr > t_wr + 3 else ("T" if t_wr > ct_wr + 3 else "Balanced")

    # ── Top weapons per side ──
    ct_top_weapons = sorted(ct_weapons.items(), key=lambda x: x[1], reverse=True)[:5]
    t_top_weapons = sorted(t_weapons.items(), key=lambda x: x[1], reverse=True)[:5]

    # ── Map-specific side performance ──
    map_stats = []
    for m, s in sorted(map_sides.items(), key=lambda x: x[1]["matches"], reverse=True):
        if s["matches"] < 1:
            continue
        m_ct_wr = round(s["ct_won"] / max(s["ct_rounds"], 1) * 100, 1)
        m_t_wr = round(s["t_won"] / max(s["t_rounds"], 1) * 100, 1)
        m_ct_kd = round(s["ct_kills"] / max(s["ct_deaths"], 1), 2)
        m_t_kd = round(s["t_kills"] / max(s["t_deaths"], 1), 2)
        map_stats.append({
            "map": m,
            "matches": s["matches"],
            "ct_rounds": s["ct_rounds"], "ct_won": s["ct_won"], "ct_wr": m_ct_wr,
            "t_rounds": s["t_rounds"], "t_won": s["t_won"], "t_wr": m_t_wr,
            "ct_kd": m_ct_kd, "t_kd": m_t_kd,
            "dominant": "CT" if m_ct_wr > m_t_wr + 5 else ("T" if m_t_wr > m_ct_wr + 5 else "="),
        })

    # ── Insights ──
    insights = []
    diff = abs(ct_wr - t_wr)
    if diff > 10:
        weak = "CT" if ct_wr < t_wr else "T"
        strong = "CT" if ct_wr > t_wr else "T"
        insights.append({"type": "warning", "icon": "shield-alert",
                         "text": f"Starkes Ungleichgewicht: {strong}-Side {max(ct_wr, t_wr)}% WR vs. {weak}-Side {min(ct_wr, t_wr)}% — trainiere {weak}-spezifische Strategien."})
    elif diff < 3:
        insights.append({"type": "success", "icon": "scale",
                         "text": f"Ausgewogene Sides: CT {ct_wr}% vs. T {t_wr}% — gute Vielseitigkeit."})

    if ct_kd > 1.3 and t_kd < 0.9:
        insights.append({"type": "info", "icon": "shield",
                         "text": f"Starke CT-Side (K/D {ct_kd}) aber schwache T-Side ({t_kd}) — mehr Entry-Fragging und Trade-Kills auf T-Side ueben."})
    elif t_kd > 1.3 and ct_kd < 0.9:
        insights.append({"type": "info", "icon": "swords",
                         "text": f"Starke T-Side (K/D {t_kd}) aber schwache CT-Side ({ct_kd}) — Positioning und Crossfire-Setups verbessern."})

    if t["self_plants"] > 0:
        plant_rate = round(t["self_plants"] / max(t["rounds"], 1) * 100, 1)
        if plant_rate > 10:
            insights.append({"type": "success", "icon": "target",
                             "text": f"Aktiver Planter: Du planst die Bombe in {plant_rate}% der T-Runden selbst."})

    # Map insights
    for ms in map_stats:
        if ms["matches"] >= 3:
            if ms["ct_wr"] < 35 and ms["t_wr"] > 55:
                insights.append({"type": "warning", "icon": "map-pin",
                                 "text": f"{ms['map']}: CT-Side schwach ({ms['ct_wr']}% WR) — ueberdenke Positionen und Rotations."})
            elif ms["t_wr"] < 35 and ms["ct_wr"] > 55:
                insights.append({"type": "warning", "icon": "map-pin",
                                 "text": f"{ms['map']}: T-Side schwach ({ms['t_wr']}% WR) — Executes und Default-Strategien lernen."})

    return {
        "has_data": True,
        "match_count": match_count,
        "ct": {"kills": ct["kills"], "deaths": ct["deaths"], "kd": ct_kd, "rounds": ct["rounds"],
               "rounds_won": ct["rounds_won"], "wr": ct_wr, "hs_pct": ct_hs,
               "kpr": ct_kpr, "dpr": ct_dpr, "defuses": ct["defuses"]},
        "t": {"kills": t["kills"], "deaths": t["deaths"], "kd": t_kd, "rounds": t["rounds"],
              "rounds_won": t["rounds_won"], "wr": t_wr, "hs_pct": t_hs,
              "kpr": t_kpr, "dpr": t_dpr, "plants": t["plants"], "self_plants": t["self_plants"]},
        "dominant": dominant,
        "ct_top_weapons": [{"weapon": w, "kills": c} for w, c in ct_top_weapons],
        "t_top_weapons": [{"weapon": w, "kills": c} for w, c in t_top_weapons],
        "map_stats": map_stats,
        "trend": match_trend[-20:],
        "insights": insights,
    }


# ── Pistol-Runden-Labor ─────────────────────────────────────
def _build_pistol_analysis(cfg: dict) -> dict:
    """Analyze pistol round performance across all matches."""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    # Aggregate accumulators
    totals = {
        "rounds": 0, "won": 0,
        "ct_rounds": 0, "ct_won": 0,
        "t_rounds": 0, "t_won": 0,
        "kills": 0, "deaths": 0, "headshots": 0,
        "opening_attempts": 0, "opening_wins": 0,
        "ct_kills": 0, "ct_deaths": 0,
        "t_kills": 0, "t_deaths": 0,
    }
    weapon_kills: dict[str, int] = {}
    per_map: dict[str, dict] = {}
    match_trend: list[dict] = []
    conversion_data: list[dict] = []  # pistol win → half win tracking

    for f in sorted(export_dir.glob("*_coach.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        match = data.get("match", {})
        timeline = data.get("round_timeline", [])
        if not timeline:
            continue

        map_name = match.get("map", "?")
        total_rounds = match.get("total_rounds", 24)
        second_half_start = total_rounds // 2 + 1 if total_rounds else 13
        pistol_nums = {1, second_half_start}

        match_pistol_kills = 0
        match_pistol_deaths = 0
        match_pistol_won = 0
        match_pistol_total = 0

        # Track half performance for conversion
        first_half_won = 0
        first_half_total = 0
        second_half_won = 0
        second_half_total = 0
        first_pistol_won = None
        second_pistol_won = None

        for rd in timeline:
            rnum = rd.get("round", 0)
            won = rd.get("won", False)
            side = rd.get("side", "?")

            # Track half stats for conversion
            if rnum < second_half_start:
                first_half_total += 1
                if won:
                    first_half_won += 1
            else:
                second_half_total += 1
                if won:
                    second_half_won += 1

            if rnum not in pistol_nums:
                continue

            # This is a pistol round
            events = rd.get("events", [])
            kill_events = [e for e in events if e.get("type") == "kill"]
            death_events = [e for e in events if e.get("type") == "death"]
            kills = len(kill_events)
            deaths = len(death_events)
            hs = sum(1 for k in kill_events if k.get("headshot", False))

            totals["rounds"] += 1
            totals["kills"] += kills
            totals["deaths"] += deaths
            totals["headshots"] += hs
            match_pistol_kills += kills
            match_pistol_deaths += deaths
            match_pistol_total += 1

            if won:
                totals["won"] += 1
                match_pistol_won += 1

            # Side split
            if side == "CT":
                totals["ct_rounds"] += 1
                totals["ct_kills"] += kills
                totals["ct_deaths"] += deaths
                if won:
                    totals["ct_won"] += 1
            elif side == "T":
                totals["t_rounds"] += 1
                totals["t_kills"] += kills
                totals["t_deaths"] += deaths
                if won:
                    totals["t_won"] += 1

            # Opening duel (first event in round)
            if events:
                first_evt = events[0]
                if first_evt.get("type") in ("kill", "death"):
                    totals["opening_attempts"] += 1
                    if first_evt["type"] == "kill":
                        totals["opening_wins"] += 1

            # Weapon tracking
            for k in kill_events:
                w = k.get("weapon", "unknown")
                weapon_kills[w] = weapon_kills.get(w, 0) + 1

            # Per-map
            if map_name not in per_map:
                per_map[map_name] = {"rounds": 0, "won": 0, "kills": 0, "deaths": 0,
                                     "ct_rounds": 0, "ct_won": 0, "t_rounds": 0, "t_won": 0}
            pm = per_map[map_name]
            pm["rounds"] += 1
            pm["kills"] += kills
            pm["deaths"] += deaths
            if won:
                pm["won"] += 1
            if side == "CT":
                pm["ct_rounds"] += 1
                if won:
                    pm["ct_won"] += 1
            elif side == "T":
                pm["t_rounds"] += 1
                if won:
                    pm["t_won"] += 1

            # Track which pistol this is for conversion
            if rnum == 1:
                first_pistol_won = won
            elif rnum == second_half_start:
                second_pistol_won = won

        # Conversion data (pistol win → half win rate)
        if first_pistol_won is not None and first_half_total > 0:
            conversion_data.append({
                "pistol_won": first_pistol_won,
                "half": "first",
                "half_wr": round(first_half_won / first_half_total * 100, 1),
            })
        if second_pistol_won is not None and second_half_total > 0:
            conversion_data.append({
                "pistol_won": second_pistol_won,
                "half": "second",
                "half_wr": round(second_half_won / second_half_total * 100, 1),
            })

        # Match trend entry
        if match_pistol_total > 0:
            match_trend.append({
                "date": match.get("date", "?"),
                "map": map_name,
                "pistol_rounds": match_pistol_total,
                "pistol_won": match_pistol_won,
                "kills": match_pistol_kills,
                "deaths": match_pistol_deaths,
            })

    if totals["rounds"] == 0:
        return {"has_data": False}

    # Compute aggregates
    wr = round(totals["won"] / totals["rounds"] * 100, 1)
    kd = round(totals["kills"] / max(totals["deaths"], 1), 2)
    kpr = round(totals["kills"] / totals["rounds"], 2)
    hs_pct = round(totals["headshots"] / max(totals["kills"], 1) * 100, 1)

    ct_wr = round(totals["ct_won"] / max(totals["ct_rounds"], 1) * 100, 1)
    t_wr = round(totals["t_won"] / max(totals["t_rounds"], 1) * 100, 1)
    ct_kd = round(totals["ct_kills"] / max(totals["ct_deaths"], 1), 2)
    t_kd = round(totals["t_kills"] / max(totals["t_deaths"], 1), 2)

    opening_wr = round(totals["opening_wins"] / max(totals["opening_attempts"], 1) * 100, 1)

    # Conversion rates
    pw_halves = [c for c in conversion_data if c["pistol_won"]]
    pl_halves = [c for c in conversion_data if not c["pistol_won"]]
    conv_win = round(sum(c["half_wr"] for c in pw_halves) / len(pw_halves), 1) if pw_halves else 0
    conv_loss = round(sum(c["half_wr"] for c in pl_halves) / len(pl_halves), 1) if pl_halves else 0

    # Top weapons
    top_weapons = sorted(weapon_kills.items(), key=lambda x: -x[1])[:8]

    # Per-map summaries
    map_list = []
    for mname, pm in sorted(per_map.items(), key=lambda x: -x[1]["rounds"]):
        m_wr = round(pm["won"] / max(pm["rounds"], 1) * 100, 1)
        m_kd = round(pm["kills"] / max(pm["deaths"], 1), 2)
        m_ct_wr = round(pm["ct_won"] / max(pm["ct_rounds"], 1) * 100, 1) if pm["ct_rounds"] else 0
        m_t_wr = round(pm["t_won"] / max(pm["t_rounds"], 1) * 100, 1) if pm["t_rounds"] else 0
        map_list.append({
            "map": mname, "rounds": pm["rounds"], "won": pm["won"],
            "wr": m_wr, "kd": m_kd,
            "ct_rounds": pm["ct_rounds"], "ct_wr": m_ct_wr,
            "t_rounds": pm["t_rounds"], "t_wr": m_t_wr,
        })

    # Trend: rolling 5 match average
    trend_chart = []
    for i, mt in enumerate(match_trend):
        window = match_trend[max(0, i - 4):i + 1]
        avg_wr = round(sum(m["pistol_won"] for m in window) / max(sum(m["pistol_rounds"] for m in window), 1) * 100, 1)
        trend_chart.append({
            "date": mt["date"], "map": mt["map"],
            "wr": avg_wr,
            "kills": mt["kills"], "deaths": mt["deaths"],
        })

    # Coaching tips
    tips = []
    if wr >= 55:
        tips.append({"type": "positive", "text": f"Starke Pistolrunden: {wr}% Win-Rate — du sicherst deinem Team den oekonomischen Vorteil."})
    elif wr < 40:
        tips.append({"type": "warning", "text": f"Pistol-WR nur {wr}% — trainiere USP/Glock-Aim und Pistol-Setups."})

    if ct_wr - t_wr > 15:
        tips.append({"type": "info", "text": f"CT-Pistol deutlich staerker ({ct_wr}%) als T ({t_wr}%) — T-Execs und Glock-Rushes ueberarbeiten."})
    elif t_wr - ct_wr > 15:
        tips.append({"type": "info", "text": f"T-Pistol deutlich staerker ({t_wr}%) als CT ({ct_wr}%) — CT-Setup und USP-Aim ueberarbeiten."})

    if opening_wr >= 60:
        tips.append({"type": "positive", "text": f"Opening-Duell-WR {opening_wr}% in Pistolrunden — aggressive Picks zahlen sich aus."})
    elif opening_wr < 35 and totals["opening_attempts"] >= 6:
        tips.append({"type": "warning", "text": f"Opening-WR nur {opening_wr}% in Pistolrunden — weniger Peeks, mehr Utility nutzen."})

    if hs_pct >= 55:
        tips.append({"type": "positive", "text": f"Headshot-Rate {hs_pct}% — praezises Aim mit Pistolen."})
    elif hs_pct < 30:
        tips.append({"type": "warning", "text": f"Headshot-Rate nur {hs_pct}% — langsamer zielen, Kopfhoehe halten."})

    if conv_win > 0 and conv_loss > 0:
        diff = round(conv_win - conv_loss, 1)
        tips.append({"type": "info", "text": f"Halftime-WR nach Pistol-Sieg: {conv_win}% vs. nach Pistol-Niederlage: {conv_loss}% (Δ {diff}pp)."})

    return {
        "has_data": True,
        "total_rounds": totals["rounds"],
        "total_won": totals["won"],
        "wr": wr, "kd": kd, "kpr": kpr, "hs_pct": hs_pct,
        "ct": {"rounds": totals["ct_rounds"], "won": totals["ct_won"], "wr": ct_wr, "kd": ct_kd},
        "t": {"rounds": totals["t_rounds"], "won": totals["t_won"], "wr": t_wr, "kd": t_kd},
        "opening_wr": opening_wr,
        "opening_attempts": totals["opening_attempts"],
        "opening_wins": totals["opening_wins"],
        "conv_win": conv_win, "conv_loss": conv_loss,
        "pistol_wins_count": len(pw_halves), "pistol_losses_count": len(pl_halves),
        "top_weapons": [{"weapon": w, "kills": c} for w, c in top_weapons],
        "maps": map_list,
        "trend": trend_chart[-20:],
        "tips": tips,
        "match_count": len(match_trend),
    }


# ── Tilt-Detektor & Mental-Coach ────────────────────────────
def _build_tilt_analysis(cfg: dict) -> dict:
    """Detect tilt patterns, streaks, and post-loss performance degradation."""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    matches: list[dict] = []
    for f in sorted(export_dir.glob("*_coach.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        mi = data.get("match", {})
        pl = data.get("player", {})
        if not mi:
            continue
        matches.append({
            "date": mi.get("date", "?"),
            "datetime": mi.get("datetime", mi.get("date", "?")),
            "map": mi.get("map", "?"),
            "result": mi.get("result", "?"),
            "won": mi.get("result") == "Sieg",
            "score_own": mi.get("score_own", 0),
            "score_enemy": mi.get("score_enemy", 0),
            "rating": pl.get("rating", 0),
            "kd": pl.get("kd", 0),
            "adr": pl.get("adr", 0),
            "hs_pct": pl.get("hs_pct", 0),
            "utility_per_round": pl.get("utility_per_round", 0),
            "opening_kills": pl.get("opening_kills", 0),
            "opening_deaths": pl.get("opening_deaths", 0),
            "survival_rate": pl.get("survival_rate", 0),
        })

    if len(matches) < 3:
        return {"has_data": False}

    # ── Streaks ──
    streaks: list[dict] = []
    current_streak = {"type": None, "length": 0, "start": 0}
    for i, m in enumerate(matches):
        stype = "win" if m["won"] else "loss"
        if stype == current_streak["type"]:
            current_streak["length"] += 1
        else:
            if current_streak["length"] >= 2:
                streaks.append({**current_streak, "end": i - 1})
            current_streak = {"type": stype, "length": 1, "start": i}
    if current_streak["length"] >= 2:
        streaks.append({**current_streak, "end": len(matches) - 1})

    longest_win = max((s for s in streaks if s["type"] == "win"), key=lambda s: s["length"], default=None)
    longest_loss = max((s for s in streaks if s["type"] == "loss"), key=lambda s: s["length"], default=None)

    # ── Post-Win vs Post-Loss performance ──
    post_win_ratings = []
    post_loss_ratings = []
    post_win_adr = []
    post_loss_adr = []
    post_win_util = []
    post_loss_util = []
    for i in range(1, len(matches)):
        prev = matches[i - 1]
        curr = matches[i]
        if prev["won"]:
            post_win_ratings.append(curr["rating"])
            post_win_adr.append(curr["adr"])
            post_win_util.append(curr["utility_per_round"])
        else:
            post_loss_ratings.append(curr["rating"])
            post_loss_adr.append(curr["adr"])
            post_loss_util.append(curr["utility_per_round"])

    def _avg(lst):
        return round(sum(lst) / len(lst), 2) if lst else 0

    avg_rating_post_win = _avg(post_win_ratings)
    avg_rating_post_loss = _avg(post_loss_ratings)
    avg_adr_post_win = _avg(post_win_adr)
    avg_adr_post_loss = _avg(post_loss_adr)
    avg_util_post_win = _avg(post_win_util)
    avg_util_post_loss = _avg(post_loss_util)

    rating_tilt_delta = round(avg_rating_post_win - avg_rating_post_loss, 2)
    adr_tilt_delta = round(avg_adr_post_win - avg_adr_post_loss, 1)

    # ── Tilt score per match (rolling degradation) ──
    overall_rating = _avg([m["rating"] for m in matches])
    overall_adr = _avg([m["adr"] for m in matches])
    match_tilt: list[dict] = []
    consecutive_losses = 0
    for i, m in enumerate(matches):
        if m["won"]:
            consecutive_losses = 0
        else:
            consecutive_losses += 1

        # Rolling 3-match average
        window = matches[max(0, i - 2):i + 1]
        roll_rating = _avg([w["rating"] for w in window])
        roll_adr = _avg([w["adr"] for w in window])

        # Tilt score: 0 = calm, 100 = full tilt
        rating_drop = max(0, overall_rating - roll_rating) / max(overall_rating, 0.01) * 100
        loss_pressure = min(consecutive_losses * 15, 45)
        tilt_score = min(round(rating_drop * 0.6 + loss_pressure * 0.4, 1), 100)

        match_tilt.append({
            "date": m["date"],
            "map": m["map"],
            "result": "W" if m["won"] else "L",
            "rating": m["rating"],
            "adr": m["adr"],
            "roll_rating": roll_rating,
            "tilt_score": tilt_score,
            "consecutive_losses": consecutive_losses,
        })

    # ── Tilt episodes: 3+ consecutive losses with declining rating ──
    tilt_episodes: list[dict] = []
    i = 0
    while i < len(matches):
        if not matches[i]["won"]:
            streak_start = i
            while i < len(matches) and not matches[i]["won"]:
                i += 1
            streak_len = i - streak_start
            if streak_len >= 3:
                ep_matches = matches[streak_start:i]
                ep_ratings = [m["rating"] for m in ep_matches]
                tilt_episodes.append({
                    "start_date": ep_matches[0]["date"],
                    "end_date": ep_matches[-1]["date"],
                    "length": streak_len,
                    "maps": [m["map"] for m in ep_matches],
                    "avg_rating": _avg(ep_ratings),
                    "rating_trend": round(ep_ratings[-1] - ep_ratings[0], 2),
                })
        else:
            i += 1

    # ── Comeback ability: performance in match right after a tilt episode ──
    comeback_ratings = []
    for ep in tilt_episodes:
        # Find the match right after this episode ends
        for j, m in enumerate(matches):
            if m["date"] == ep["end_date"]:
                if j + 1 < len(matches):
                    comeback_ratings.append(matches[j + 1]["rating"])
                break
    comeback_avg = _avg(comeback_ratings) if comeback_ratings else 0

    # ── Mental resilience score (0-100) ──
    # Higher = more resilient (less performance drop after losses)
    tilt_magnitude = abs(rating_tilt_delta) * 50  # 0.10 delta = 5 points
    episode_penalty = len(tilt_episodes) * 5
    resilience = max(0, min(100, round(80 - tilt_magnitude - episode_penalty)))

    # ── Current form: is the player currently tilted? ──
    last_5 = matches[-5:] if len(matches) >= 5 else matches
    recent_losses = sum(1 for m in last_5 if not m["won"])
    recent_rating = _avg([m["rating"] for m in last_5])
    currently_tilted = recent_losses >= 3 and recent_rating < overall_rating - 0.05

    # ── Coaching tips ──
    tips = []
    if rating_tilt_delta > 0.08:
        tips.append({"type": "warning", "icon": "brain",
                     "text": f"Tilt-Effekt erkannt: Nach Niederlagen faellt dein Rating um {rating_tilt_delta} "
                             f"(von Ø {avg_rating_post_win} auf Ø {avg_rating_post_loss}). Mach nach einer Niederlage eine kurze Pause."})
    elif rating_tilt_delta > 0.03:
        tips.append({"type": "info", "icon": "brain",
                     "text": f"Leichter Tilt-Effekt: Rating nach Sieg Ø {avg_rating_post_win} vs. nach Niederlage Ø {avg_rating_post_loss} (Δ {rating_tilt_delta})."})
    else:
        tips.append({"type": "positive", "icon": "shield",
                     "text": f"Mental stark: Kaum Performance-Unterschied nach Sieg vs. Niederlage (Δ {rating_tilt_delta}). Gute mentale Stabilitaet!"})

    if tilt_episodes:
        tips.append({"type": "warning", "icon": "alert-triangle",
                     "text": f"{len(tilt_episodes)} Tilt-Episode(n) erkannt (3+ Niederlagen in Folge). "
                             f"Durchschnittliches Rating waehrend Tilt: {_avg([e['avg_rating'] for e in tilt_episodes])}."})

    if adr_tilt_delta > 10:
        tips.append({"type": "warning", "icon": "target",
                     "text": f"ADR sinkt nach Niederlagen um {adr_tilt_delta} — du wirst passiver wenn es schlecht laeuft. Bleib aktiv!"})

    if avg_util_post_loss < avg_util_post_win * 0.8 and avg_util_post_win > 1.0:
        tips.append({"type": "info", "icon": "flame",
                     "text": f"Utility-Nutzung sinkt nach Niederlagen ({avg_util_post_loss}/R vs. {avg_util_post_win}/R). Vergiss nicht Utility zu werfen auch wenn es schlecht laeuft."})

    if currently_tilted:
        tips.append({"type": "warning", "icon": "pause-circle",
                     "text": "Aktuell auf Tilt: Letzte 5 Matches zeigen Abwaertstrend. Mach eine Pause oder spiele Deathmatch zum Reset."})

    if longest_win:
        tips.append({"type": "positive", "icon": "trending-up",
                     "text": f"Laengste Siegesserie: {longest_win['length']} Siege in Folge — du kannst Momentum aufbauen!"})

    if comeback_avg > 0:
        if comeback_avg >= overall_rating:
            tips.append({"type": "positive", "icon": "rotate-ccw",
                         "text": f"Gute Comeback-Faehigkeit: Nach Tilt-Phasen spielst du im Schnitt Rating {comeback_avg} (Ø {overall_rating})."})
        else:
            tips.append({"type": "info", "icon": "rotate-ccw",
                         "text": f"Comeback nach Tilt: Rating {comeback_avg} (unter Gesamtschnitt {overall_rating}). Laenger pausieren nach Abwaertsspiralen."})

    # Mental reset tips
    mental_tips = [
        "5 Minuten Pause zwischen Matches — aufstehen, Wasser trinken, durchatmen.",
        "Nach 2 Niederlagen in Folge: Wechsle zu Deathmatch oder Aim-Training.",
        "Fokus auf Prozess statt Ergebnis — kontrolliere was du kontrollieren kannst.",
        "Setze dir ein Match-Limit pro Session (z.B. max. 4 Matches).",
        "Wenn du merkst dass du frustriert bist: Session beenden. Morgen wieder.",
    ]

    return {
        "has_data": True,
        "match_count": len(matches),
        "overall_rating": overall_rating,
        "overall_adr": overall_adr,
        "resilience": resilience,
        "currently_tilted": currently_tilted,
        "post_win": {"rating": avg_rating_post_win, "adr": avg_adr_post_win, "utility": avg_util_post_win},
        "post_loss": {"rating": avg_rating_post_loss, "adr": avg_adr_post_loss, "utility": avg_util_post_loss},
        "rating_tilt_delta": rating_tilt_delta,
        "adr_tilt_delta": adr_tilt_delta,
        "longest_win": {"length": longest_win["length"],
                        "start": matches[longest_win["start"]]["date"],
                        "end": matches[longest_win["end"]]["date"]} if longest_win else None,
        "longest_loss": {"length": longest_loss["length"],
                         "start": matches[longest_loss["start"]]["date"],
                         "end": matches[longest_loss["end"]]["date"]} if longest_loss else None,
        "tilt_episodes": tilt_episodes,
        "comeback_avg": comeback_avg,
        "match_tilt": match_tilt[-30:],
        "tips": tips,
        "mental_tips": mental_tips,
    }


# ── Rollen-Erkennung ────────────────────────────────────────
def _build_role_detection(cfg: dict) -> dict:
    """Automatically detect the player's CS2 role from aggregated stats."""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    # Accumulators across all matches
    totals = {
        "matches": 0, "kills": 0, "deaths": 0, "assists": 0,
        "opening_kills": 0, "opening_deaths": 0, "trade_kills": 0,
        "awp_kills": 0, "rifle_kills": 0, "total_kills_weapon": 0,
        "utility_total": 0, "flashes": 0, "smokes": 0, "he": 0, "molotovs": 0,
        "util_per_round_sum": 0,
        "flash_enemies_blinded": 0,
        "survival_rate_sum": 0, "adr_sum": 0,
        "hs_pct_sum": 0, "kast_sum": 0,
        "dist_close": 0, "dist_mid": 0, "dist_long": 0,
        "death_early": 0, "death_mid": 0, "death_late": 0,
        "ct_kills": 0, "ct_deaths": 0, "t_kills": 0, "t_deaths": 0,
        "multikill_3k": 0, "multikill_4k": 0, "multikill_5k": 0,
        "clutch_wins": 0, "clutch_attempts": 0,
    }
    per_match_roles: list[dict] = []  # per-match role scores for trend

    for f in sorted(export_dir.glob("*_coach.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        pl = data.get("player", {})
        mi = data.get("match", {})
        if not pl or not mi:
            continue

        totals["matches"] += 1
        totals["kills"] += pl.get("kills", 0)
        totals["deaths"] += pl.get("deaths", 0)
        totals["assists"] += pl.get("assists", 0)
        totals["opening_kills"] += pl.get("opening_kills", 0)
        totals["opening_deaths"] += pl.get("opening_deaths", 0)
        totals["trade_kills"] += pl.get("trade_kills", 0)
        totals["survival_rate_sum"] += pl.get("survival_rate", 0)
        totals["adr_sum"] += pl.get("adr", 0)
        totals["hs_pct_sum"] += pl.get("hs_pct", 0)
        totals["kast_sum"] += pl.get("kast_pct", 0)
        totals["util_per_round_sum"] += pl.get("utility_per_round", 0)

        # Weapons
        wpn = pl.get("weapons", {})
        totals["awp_kills"] += wpn.get("awp_kills", 0)
        totals["rifle_kills"] += wpn.get("rifle_kills", 0)
        totals["total_kills_weapon"] += wpn.get("awp_kills", 0) + wpn.get("rifle_kills", 0) + wpn.get("pistol_kills", 0)

        # Utility detail
        util = pl.get("utility", {})
        totals["utility_total"] += util.get("total", 0)
        totals["flashes"] += util.get("flashes", 0)
        totals["smokes"] += util.get("smokes", 0)
        totals["he"] += util.get("he", 0)
        totals["molotovs"] += util.get("molotovs", 0)

        # Flash effectiveness
        fe = pl.get("flash_effectiveness", {})
        totals["flash_enemies_blinded"] += fe.get("enemies_blinded", 0)

        # Engagement distance
        eng = pl.get("engagement_distance", {})
        totals["dist_close"] += eng.get("close", 0)
        totals["dist_mid"] += eng.get("mid", 0)
        totals["dist_long"] += eng.get("long", 0)

        # Death timing
        dt = pl.get("death_timing", {})
        totals["death_early"] += dt.get("early", 0)
        totals["death_mid"] += dt.get("mid", 0)
        totals["death_late"] += dt.get("late", 0)

        # Side split
        ss = pl.get("side_split", {})
        totals["ct_kills"] += ss.get("ct_kills", 0)
        totals["ct_deaths"] += ss.get("ct_deaths", 0)
        totals["t_kills"] += ss.get("t_kills", 0)
        totals["t_deaths"] += ss.get("t_deaths", 0)

        # Multikills
        mk = pl.get("multikills", {})
        totals["multikill_3k"] += mk.get("3k", 0)
        totals["multikill_4k"] += mk.get("4k", 0)
        totals["multikill_5k"] += mk.get("5k", 0)

        # Clutches
        cl = pl.get("clutches", {})
        totals["clutch_wins"] += cl.get("wins", 0)
        totals["clutch_attempts"] += cl.get("attempts", 0)

    n = totals["matches"]
    if n < 3:
        return {"has_data": False}

    # ── Compute normalized role indicators (0-100 each) ──
    avg_opening_kills = totals["opening_kills"] / n
    avg_opening_deaths = totals["opening_deaths"] / n
    opening_rate = (totals["opening_kills"] + totals["opening_deaths"]) / max(totals["kills"] + totals["deaths"], 1)
    opening_wr = totals["opening_kills"] / max(totals["opening_kills"] + totals["opening_deaths"], 1)
    avg_survival = totals["survival_rate_sum"] / n
    avg_util = totals["util_per_round_sum"] / n
    avg_adr = totals["adr_sum"] / n
    avg_hs = totals["hs_pct_sum"] / n
    avg_kast = totals["kast_sum"] / n
    trade_rate = totals["trade_kills"] / max(totals["kills"], 1)
    awp_ratio = totals["awp_kills"] / max(totals["total_kills_weapon"], 1)
    total_deaths_timed = totals["death_early"] + totals["death_mid"] + totals["death_late"]
    early_death_pct = totals["death_early"] / max(total_deaths_timed, 1)
    late_death_pct = totals["death_late"] / max(total_deaths_timed, 1)
    dist_total = totals["dist_close"] + totals["dist_mid"] + totals["dist_long"]
    long_pct = totals["dist_long"] / max(dist_total, 1)
    close_pct = totals["dist_close"] / max(dist_total, 1)
    clutch_rate = totals["clutch_wins"] / max(totals["clutch_attempts"], 1)
    ct_kd = totals["ct_kills"] / max(totals["ct_deaths"], 1)
    assist_rate = totals["assists"] / max(totals["kills"], 1)

    # ── Role scoring (each 0-100) ──
    def _clamp(v, lo=0, hi=100):
        return max(lo, min(hi, round(v)))

    # Entry Fragger: high opening kills, early deaths, aggressive, close distance, low survival
    entry_score = _clamp(
        avg_opening_kills * 25 +          # ~0.8-1.5 OK kills → 20-37
        opening_rate * 80 +                # high duel involvement
        (1 - avg_survival / 100) * 30 +    # low survival = aggressive
        close_pct * 25 +                   # close engagements
        early_death_pct * 20 -             # dies early (aggressive)
        avg_util * 5                       # less utility usage
    )

    # Support: high utility, flashes, trades, assists, late deaths
    support_score = _clamp(
        avg_util * 18 +                    # ~1.5-2.5 util/r → 27-45
        trade_rate * 60 +                  # trade kills
        assist_rate * 40 +                 # assists relative to kills
        totals["flash_enemies_blinded"] / max(n, 1) * 8 +  # flash impact
        late_death_pct * 20 -              # dies late = support role
        avg_opening_kills * 10             # fewer opening kills
    )

    # AWPer: high AWP ratio, long distance, high impact per kill
    awper_score = _clamp(
        awp_ratio * 120 +                  # AWP kill share (>40% = strong signal)
        long_pct * 40 +                    # long distance engagements
        avg_adr / 100 * 15 -              # good damage output
        close_pct * 20 -                   # NOT close range
        avg_util * 5                       # less utility typically
    )

    # Lurker: late kills, high survival, low opening involvement, solo plays
    lurker_score = _clamp(
        late_death_pct * 35 +              # dies late
        avg_survival / 100 * 30 +          # survives often
        clutch_rate * 30 +                 # clutch situations
        (1 - opening_rate) * 25 -          # avoids opening duels
        avg_util * 5 -                     # less utility
        trade_rate * 30                    # not trading (solo player)
    )

    # Anchor: CT-focused, high survival, holds site, trades, mid-late deaths
    anchor_score = _clamp(
        ct_kd * 15 +                       # strong CT performance
        avg_survival / 100 * 25 +          # holds position, survives
        (1 - early_death_pct) * 25 +       # doesn't die early
        avg_kast / 100 * 20 +              # consistent contribution
        avg_util * 8 -                     # uses utility to hold
        avg_opening_kills * 15             # not aggressive opener
    )

    roles = {
        "Entry Fragger": entry_score,
        "Support": support_score,
        "AWPer": awper_score,
        "Lurker": lurker_score,
        "Anchor": anchor_score,
    }

    # Primary + secondary role
    sorted_roles = sorted(roles.items(), key=lambda x: -x[1])
    primary_role = sorted_roles[0][0]
    primary_score = sorted_roles[0][1]
    secondary_role = sorted_roles[1][0]
    secondary_score = sorted_roles[1][1]

    # Role descriptions & icons
    role_meta = {
        "Entry Fragger": {
            "icon": "swords", "color": "#f87171",
            "desc": "Du gehst als Erster rein und suchst den Erstkontakt. Aggressiv, risikoreich, matchentscheidend.",
            "strengths": "Opening Duels, Aggression, Space Creation",
            "train": "Crosshair Placement, Prefire-Angles, Counter-Strafe",
        },
        "Support": {
            "icon": "shield", "color": "#60a5fa",
            "desc": "Du unterstuetzt dein Team mit Utility, Trades und guter Positionierung. Das Rueckgrat des Teams.",
            "strengths": "Utility-Einsatz, Trade Kills, Team-Support",
            "train": "Smoke/Flash Lineups, Flash Timing, Positioning",
        },
        "AWPer": {
            "icon": "crosshair", "color": "#a78bfa",
            "desc": "Du kontrollierst lange Sightlines mit der AWP. Hoher Impact pro Kill, teuer aber effektiv.",
            "strengths": "AWP-Aim, Angle Holding, Map Control",
            "train": "Flick-Aim, Quick-Scope, Repositioning nach Schuss",
        },
        "Lurker": {
            "icon": "eye", "color": "#fbbf24",
            "desc": "Du spielst abseits des Teams, sammelst Info und schlaegst spaet zu. Der stille Killer.",
            "strengths": "Game Sense, Timing, Clutch-Faehigkeit",
            "train": "Rotation Timing, Sound Cues, 1vX Situationen",
        },
        "Anchor": {
            "icon": "home", "color": "#4ade80",
            "desc": "Du haeltst die Site auf CT-Seite. Solide, zuverlaessig, der letzte Mann der faellt.",
            "strengths": "Site Holding, Utility-Defense, Konsistenz",
            "train": "Retake Setups, Molotov/HE Lineups, Off-Angles",
        },
    }

    # ── Radar chart data: 6 dimensions ──
    radar_labels = ["Aggression", "Utility", "Survival", "Aim", "Impact", "Clutch"]
    aggression = _clamp(opening_rate * 120 + close_pct * 40 + early_death_pct * 40)
    utility_dim = _clamp(avg_util * 25 + totals["flash_enemies_blinded"] / max(n, 1) * 10)
    survival_dim = _clamp(avg_survival)
    aim_dim = _clamp(avg_hs * 1.2 + (avg_adr - 50) * 0.6)
    impact_dim = _clamp(avg_adr / 100 * 40 + (totals["multikill_3k"] + totals["multikill_4k"] * 2 + totals["multikill_5k"] * 3) / n * 15 + avg_opening_kills * 20)
    clutch_dim = _clamp(clutch_rate * 80 + totals["clutch_wins"] / max(n, 1) * 20)

    radar_scores = [aggression, utility_dim, survival_dim, aim_dim, impact_dim, clutch_dim]

    # ── Role fit analysis: does skill match the role? ──
    fit_issues = []
    if primary_role == "Entry Fragger":
        if avg_hs < 40:
            fit_issues.append({"type": "warning", "text": f"Als Entry Fragger brauchst du praezises Aim — deine HS-Rate ist nur {avg_hs:.0f}%. Trainiere Prefire und Crosshair Placement."})
        if opening_wr < 0.45:
            fit_issues.append({"type": "warning", "text": f"Opening-Duel-WR nur {opening_wr*100:.0f}% — als Entry musst du diese Duelle gewinnen. Arbeite an Peeking-Technik."})
        if opening_wr >= 0.55:
            fit_issues.append({"type": "positive", "text": f"Opening-Duel-WR {opening_wr*100:.0f}% — du gewinnst deine Erstduelle zuverlaessig. Starker Entry!"})
    elif primary_role == "Support":
        if avg_util < 1.5:
            fit_issues.append({"type": "warning", "text": f"Als Support nur {avg_util:.1f} Utility/Runde — das ist zu wenig. Lerne mehr Lineups und wirf jede Runde Utility."})
        if avg_util >= 2.0:
            fit_issues.append({"type": "positive", "text": f"{avg_util:.1f} Utility/Runde — du nutzt dein Equipment gut. Exzellenter Support-Spieler!"})
    elif primary_role == "AWPer":
        if awp_ratio < 0.3:
            fit_issues.append({"type": "info", "text": f"AWP-Anteil {awp_ratio*100:.0f}% — du spielst auch viel Rifle. Hybrid-Spieler mit AWP-Tendenz."})
        if awp_ratio >= 0.5:
            fit_issues.append({"type": "positive", "text": f"AWP-Anteil {awp_ratio*100:.0f}% — klarer AWP-Fokus. Stelle sicher dass du Economy im Griff hast."})
    elif primary_role == "Lurker":
        if avg_survival < 30:
            fit_issues.append({"type": "warning", "text": f"Survival-Rate nur {avg_survival:.0f}% — fuer einen Lurker zu niedrig. Positioniere dich sicherer."})
        if clutch_rate >= 0.3:
            fit_issues.append({"type": "positive", "text": f"Clutch-Rate {clutch_rate*100:.0f}% — du bist in 1vX Situationen stark. Perfekt fuer die Lurker-Rolle."})
    elif primary_role == "Anchor":
        if ct_kd < 1.0:
            fit_issues.append({"type": "warning", "text": f"CT K/D nur {ct_kd:.2f} — als Anchor solltest du auf CT staerker sein. Arbeite an Site-Holds."})
        if ct_kd >= 1.3:
            fit_issues.append({"type": "positive", "text": f"CT K/D {ct_kd:.2f} — starke CT-Performance. Du bist ein zuverlaessiger Anchor."})

    # General fit insight
    if abs(primary_score - secondary_score) < 8:
        fit_issues.append({"type": "info", "text": f"Dein Profil liegt zwischen {primary_role} und {secondary_role} — du bist ein vielseitiger Spieler (Hybrid)."})

    # ── Key stats summary ──
    key_stats = {
        "kd": round(totals["kills"] / max(totals["deaths"], 1), 2),
        "adr": round(avg_adr, 1),
        "hs_pct": round(avg_hs, 1),
        "opening_kills_per_match": round(avg_opening_kills, 1),
        "opening_wr": round(opening_wr * 100, 1),
        "utility_per_round": round(avg_util, 2),
        "survival_rate": round(avg_survival, 1),
        "awp_ratio": round(awp_ratio * 100, 1),
        "trade_rate": round(trade_rate * 100, 1),
        "clutch_rate": round(clutch_rate * 100, 1),
        "kast": round(avg_kast, 1),
    }

    return {
        "has_data": True,
        "match_count": n,
        "primary_role": primary_role,
        "primary_score": primary_score,
        "secondary_role": secondary_role,
        "secondary_score": secondary_score,
        "roles": {name: {"score": score, **role_meta.get(name, {})} for name, score in sorted_roles},
        "radar_labels": radar_labels,
        "radar_scores": radar_scores,
        "fit_issues": fit_issues,
        "key_stats": key_stats,
    }


# ── Gegner-Vorhersage (Opponent Prediction) ─────────────────
def _build_opponent_prediction(cfg: dict) -> dict:
    """Build behavioral predictions for recurring opponents."""
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {"has_data": False}

    opp_agg: dict[str, dict] = {}
    my_match_count = 0

    for f in sorted(export_dir.glob("*_coach.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        mi = data.get("match", {})
        pl = data.get("player", {})
        duels = data.get("duel_matrix", [])
        kp = data.get("kill_positions", [])
        timeline = data.get("round_timeline", [])
        sb = data.get("scoreboard", [])
        if not duels:
            continue
        my_match_count += 1

        map_name = mi.get("map", "?")
        result = mi.get("result", "?")
        match_date = mi.get("date", "?")

        # Build teammate set
        target_sid = pl.get("steam_id", "")
        teammate_sids = {target_sid} if target_sid else set()
        target_idx = next((i for i, s in enumerate(sb) if s.get("is_target")), None)
        if target_idx is not None:
            team_start = 0 if target_idx < 5 else 5
            for i in range(team_start, team_start + 5):
                if i < len(sb):
                    sid = sb[i].get("steam_id", "")
                    if sid:
                        teammate_sids.add(sid)

        # Opponent scoreboard stats (from enemy half)
        enemy_start = 5 if (target_idx is not None and target_idx < 5) else 0
        enemy_sb = {}
        for i in range(enemy_start, enemy_start + 5):
            if i < len(sb):
                s = sb[i]
                sid = s.get("steam_id", "")
                if sid and sid not in teammate_sids:
                    enemy_sb[s.get("name", "")] = {
                        "kills": s.get("kills", 0), "deaths": s.get("deaths", 0),
                        "adr": s.get("adr", 0), "rating": s.get("rating", 0),
                    }

        # Round timeline: detect early kills/deaths per opponent for aggression
        round_first_events: dict[str, dict] = {}  # opp_name -> {first_kills, first_deaths}
        for rd in timeline:
            events = rd.get("events", [])
            if events:
                first = events[0]
                name = first.get("target", first.get("killer", ""))
                if name and name not in teammate_sids:
                    if name not in round_first_events:
                        round_first_events[name] = {"first_kills": 0, "first_deaths": 0, "rounds": 0}
                    round_first_events[name]["rounds"] += 1
                    if first.get("type") == "kill":
                        round_first_events[name]["first_deaths"] += 1  # they died first to you
                    elif first.get("type") == "death":
                        round_first_events[name]["first_kills"] += 1  # they got opening on you

        for d in duels:
            sid = d.get("steam_id", d.get("name", ""))
            name = d.get("name", "?")
            if not sid or sid in teammate_sids:
                continue

            if sid not in opp_agg:
                opp_agg[sid] = {
                    "name": name, "steam_id": sid,
                    "kills_on_you": 0, "deaths_to_you": 0,
                    "hs_on_you": 0, "hs_by_you": 0,
                    "matches": 0, "weapons": {},
                    "maps": {}, "results": [],
                    "dates": [], "ratings": [], "adrs": [],
                    "opening_kills_on_you": 0, "opening_deaths_to_you": 0,
                    "positions_kill": [], "positions_death": [],
                }
            e = opp_agg[sid]
            e["name"] = name
            e["matches"] += 1
            e["kills_on_you"] += d.get("kills", 0)
            e["deaths_to_you"] += d.get("deaths", 0)
            e["hs_on_you"] += d.get("hs_kills", 0)
            e["hs_by_you"] += d.get("hs_deaths", 0)
            e["dates"].append(match_date)
            e["results"].append(result)

            # Weapons they use
            for w in d.get("top_weapons", []):
                e["weapons"][w] = e["weapons"].get(w, 0) + 1

            # Map tracking
            if map_name not in e["maps"]:
                e["maps"][map_name] = {"played": 0, "wins": 0, "losses": 0}
            e["maps"][map_name]["played"] += 1
            if result == "Sieg":
                e["maps"][map_name]["wins"] += 1
            elif result == "Niederlage":
                e["maps"][map_name]["losses"] += 1

            # Scoreboard stats for this opponent
            opp_sb = enemy_sb.get(name, {})
            if opp_sb.get("rating"):
                e["ratings"].append(opp_sb["rating"])
            if opp_sb.get("adr"):
                e["adrs"].append(opp_sb["adr"])

            # Opening events
            rfe = round_first_events.get(name, {})
            e["opening_kills_on_you"] += rfe.get("first_kills", 0)
            e["opening_deaths_to_you"] += rfe.get("first_deaths", 0)

        # Kill positions per opponent
        for pos in kp:
            enemy_name = pos.get("e", "")
            for sid, e in opp_agg.items():
                if e["name"] == enemy_name:
                    entry = {"x": pos.get("x", 0), "y": pos.get("y", 0),
                             "ex": pos.get("ex", 0), "ey": pos.get("ey", 0),
                             "map": map_name, "w": pos.get("w", ""), "hs": pos.get("hs", False)}
                    if pos.get("t") == "k":
                        e["positions_kill"].append(entry)
                    elif pos.get("t") == "d":
                        e["positions_death"].append(entry)
                    break

    if not opp_agg:
        return {"has_data": False}

    # ── Build prediction cards for recurring opponents (2+ matches) ──
    predictions = []
    for sid, e in opp_agg.items():
        if e["matches"] < 2:
            continue

        total_duels = e["kills_on_you"] + e["deaths_to_you"]
        if total_duels < 3:
            continue

        kd_vs = round(e["deaths_to_you"] / max(e["kills_on_you"], 1), 2)
        their_hs_pct = round(e["hs_on_you"] / max(e["kills_on_you"], 1) * 100, 1)
        your_hs_pct = round(e["hs_by_you"] / max(e["deaths_to_you"], 1) * 100, 1)
        wr = round(sum(1 for r in e["results"] if r == "Sieg") / len(e["results"]) * 100, 1)
        avg_rating = round(sum(e["ratings"]) / len(e["ratings"]), 2) if e["ratings"] else 0
        avg_adr = round(sum(e["adrs"]) / len(e["adrs"]), 1) if e["adrs"] else 0

        # Weapon prediction: most likely weapon
        total_weapon = sum(e["weapons"].values()) if e["weapons"] else 0
        weapon_probs = []
        for w, c in sorted(e["weapons"].items(), key=lambda x: -x[1])[:4]:
            weapon_probs.append({"weapon": w, "count": c,
                                 "pct": round(c / max(total_weapon, 1) * 100, 1)})
        primary_weapon = weapon_probs[0]["weapon"] if weapon_probs else "?"

        # Playstyle classification
        opening_total = e["opening_kills_on_you"] + e["opening_deaths_to_you"]
        aggression = e["opening_kills_on_you"] / max(opening_total, 1) if opening_total >= 2 else 0.5
        if aggression > 0.6:
            playstyle = "Aggressiv"
            playstyle_color = "#f87171"
            playstyle_icon = "swords"
        elif aggression < 0.35:
            playstyle = "Passiv"
            playstyle_color = "#60a5fa"
            playstyle_icon = "shield"
        else:
            playstyle = "Ausgewogen"
            playstyle_color = "#fbbf24"
            playstyle_icon = "scale"

        # AWP detection
        awp_pct = e["weapons"].get("awp", 0) / max(total_weapon, 1)
        if awp_pct > 0.35:
            playstyle = "AWPer"
            playstyle_color = "#a78bfa"
            playstyle_icon = "crosshair"

        # Map preference
        map_stats = []
        for m, ms in sorted(e["maps"].items(), key=lambda x: -x[1]["played"]):
            m_wr = round(ms["wins"] / max(ms["played"], 1) * 100, 1)
            map_stats.append({"map": m, "played": ms["played"],
                              "wins": ms["wins"], "losses": ms["losses"], "wr": m_wr})

        # Threat level
        if kd_vs >= 1.5 and total_duels >= 8:
            threat = "low"
            threat_label = "Leichte Beute"
        elif kd_vs <= 0.7 and total_duels >= 8:
            threat = "high"
            threat_label = "Gefaehrlich"
        elif kd_vs <= 0.9:
            threat = "medium"
            threat_label = "Herausforderung"
        else:
            threat = "low"
            threat_label = "Kontrollierbar"

        # Confidence: more data = more confident
        confidence = min(100, e["matches"] * 15 + total_duels * 3)

        # Tactical predictions
        tactics = []
        if awp_pct > 0.3:
            tactics.append(f"Spielt {round(awp_pct*100)}% AWP — Smokes werfen, nicht wide peeken.")
        if their_hs_pct >= 50:
            tactics.append(f"Hohe HS-Rate ({their_hs_pct}%) — Jiggle-Peeks und Off-Angles nutzen.")
        elif their_hs_pct < 25 and e["kills_on_you"] >= 5:
            tactics.append(f"Spray-Spieler ({their_hs_pct}% HS) — Aim-Duelle suchen.")
        if aggression > 0.6 and opening_total >= 4:
            tactics.append("Aggressiver Spieler — Angles vorhalten, nicht reinrennen.")
        elif aggression < 0.35 and opening_total >= 4:
            tactics.append("Passiver Spieler — Push-Setups mit Utility vorbereiten.")
        if wr < 40 and len(e["results"]) >= 3:
            tactics.append(f"Du verlierst oft gegen ihn ({wr}% WR) — anders spielen als bisher.")
        elif wr > 65 and len(e["results"]) >= 3:
            tactics.append(f"Gute Bilanz ({wr}% WR) — weiter so spielen.")

        # Best/worst map vs this opponent
        if len(map_stats) >= 2:
            best_map = max(map_stats, key=lambda m: m["wr"])
            worst_map = min(map_stats, key=lambda m: m["wr"])
            if best_map["wr"] != worst_map["wr"]:
                tactics.append(f"Beste Map: {best_map['map']} ({best_map['wr']}%) — Schlechteste: {worst_map['map']} ({worst_map['wr']}%)")

        last_seen = sorted([d for d in e["dates"] if d != "?"], reverse=True)
        predictions.append({
            "name": e["name"],
            "matches": e["matches"],
            "kills_on_you": e["kills_on_you"],
            "deaths_to_you": e["deaths_to_you"],
            "kd_vs": kd_vs,
            "wr": wr,
            "their_hs_pct": their_hs_pct,
            "avg_rating": avg_rating,
            "avg_adr": avg_adr,
            "primary_weapon": primary_weapon,
            "weapon_probs": weapon_probs,
            "playstyle": playstyle,
            "playstyle_color": playstyle_color,
            "playstyle_icon": playstyle_icon,
            "threat": threat,
            "threat_label": threat_label,
            "confidence": confidence,
            "map_stats": map_stats,
            "tactics": tactics,
            "last_seen": last_seen[0] if last_seen else "?",
        })

    # Sort by most encountered
    predictions.sort(key=lambda x: -x["matches"])

    # Stats summary
    nemeses = [p for p in predictions if p["threat"] == "high"]
    victims = [p for p in predictions if p["kd_vs"] >= 1.5 and p["matches"] >= 2]

    return {
        "has_data": bool(predictions),
        "predictions": predictions[:20],
        "total_recurring": len(predictions),
        "match_count": my_match_count,
        "nemesis_count": len(nemeses),
        "victim_count": len(victims),
    }
