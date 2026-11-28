"""WebUI-Seite /momente: Schluesselmomente eines Matches als kommentierter Clip.

Liste = teuerste eigene Tode (costly_moments), Clip = 2D-Replay der Runde
(build_round_replay) mit Markierungen aus key_moments.annotate. Kein
Spielvideo - dafuer muesste CS2 mit GPU laufen.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import threading
from pathlib import Path

from flask import Blueprint, Response, jsonify, render_template, request, send_file

from .. import aim_trace, costly_moments, demo_cache, key_moments, map3d, map3d_render, models3d, nades3d
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


# ── Szenen bauen, cachen, vorberechnen ─────────────────────────────────────
CLIP_VERSION = 8            # erhoehen, wenn sich der Inhalt einer Szene aendert (alter Cache wird ignoriert)
CACHE_DIR = ".moments_cache"
_warming: set[str] = set()
_warm_lock = threading.Lock()
_clip_locks: dict[str, threading.Lock] = {}


def build_clip(cfg: dict, cfg_path: Path, filename: str, round_num: int, tick: int, kind: str,
               with3d: bool = True) -> tuple[dict, int]:
    """Eine Szene: Replay der Runde, Moment-Markierungen, Duell, und mit ``with3d`` 3D-Posen,
    Aim-Spur und Granaten. Rueckgabe (Daten, HTTP-Status)."""
    from .app import build_round_replay
    edir = _export_dir(cfg)
    data = _load(edir, filename) if edir else None
    if data is None:
        return {"error": "Export nicht gefunden"}, 404
    match = data.get("match") or {}
    demo = Path(cfg.get("demo_folder", "")) / (match.get("demo_file") or "-")
    if not demo.exists():
        return {"error": f"Demo fehlt: {match.get('demo_file')}"}, 404
    map_key = (match.get("map") or "").lower()
    if map_key not in MAP_RADAR_DATA:
        return {"error": f"Map ohne Radardaten: {map_key}"}, 400
    me = _me(cfg, data)
    replay = build_round_replay(str(demo), round_num, me, map_key)
    if "error" in replay:
        return replay, 400
    replay["map"] = map_key
    replay["scale"] = MAP_RADAR_DATA[map_key]["scale"]
    is_kill = kind == "kill"
    mo = (key_moments.annotate_kill if is_kill else key_moments.annotate)(replay, tick, replay["scale"])
    mo["events"] = []
    facts = {}
    p = demo_cache.cached_parser(demo)
    fr = replay["frames"]
    t0, t1 = fr[mo["clip"][0]]["t"], fr[mo["clip"][1]]["t"]
    clip_ticks = list(range(t0, t1 + 1, key_moments.VIEW3D_STEP))
    # Alle Tick-Felder der Szene in EINEM Durchlauf (3D, Duell, Aim-Spur lesen daraus)
    window = key_moments.duel_ticks(mo["death_tick"]) if mo.get("killer") else []
    fields = key_moments.DUEL_FIELDS + aim_trace.FIELDS + (key_moments.VIEW3D_FIELDS if with3d else [])
    if window or with3d:
        p.preload_ticks(fields, window + (clip_ticks if with3d else []))
    if mo.get("killer") and mo.get("killer_idx") is not None:
        name = replay["players"][mo["killer_idx"]]["name"]
        try:
            facts = key_moments.scan_duel(str(demo), me, mo["killer"], mo["death_tick"], parser=p)
            notes, mo["events"] = key_moments.duel_notes(facts, name, kill=is_kill)
            mo["notes"] = mo["notes"][:1] + notes + mo["notes"][1:]
        except Exception as e:  # noqa: BLE001 - Clip bleibt nutzbar ohne Duell-Detail
            mo["notes"].append({"tone": "info", "text": f"Duell-Detail nicht lesbar: {e}"})
    replay["moment"] = mo
    if not with3d:
        return replay, 200
    # 32 Hz statt der 8 Hz der 2D-Frames - der Browser interpoliert dazwischen
    try:
        hud: dict = {}
        replay["view3d"] = key_moments.scan_3d(str(demo), replay["players"], clip_ticks, parser=p, hud=hud)
        # Bombe dieser Runde: Zeitpunkt der Legung (Bombentimer 40 s) aus den Events
        try:
            bp = p.parse_event("bomb_planted")
            rs = replay.get("start_tick", t0)
            hud["bomb_tick"] = next((int(x) for x in sorted(bp["tick"]) if rs <= int(x) <= t1), None) if len(bp) else None
        except Exception:  # noqa: BLE001 - ohne Bombentimer
            hud["bomb_tick"] = None
        # Geblendet: aus player_blind (Dauer je Spieler). Das Tick-Feld flash_duration bleibt nach
        # dem Abklingen stehen - das Bild war damit noch Sekunden spaeter weiss.
        try:
            bl = p.parse_event("player_blind")
            idx = {pl["steamid"]: i for i, pl in enumerate(replay["players"])}
            hud["blinds"] = [[idx[str(r["user_steamid"])], int(r["tick"]),
                              int(r["tick"]) + int(float(r["blind_duration"] or 0) * key_moments.TICKRATE)]
                             for r in bl.to_dict("records")
                             if str(r["user_steamid"]) in idx and int(r["tick"]) <= t1
                             and int(r["tick"]) + float(r["blind_duration"] or 0) * key_moments.TICKRATE >= t0]                 if len(bl) else []
        except Exception:  # noqa: BLE001 - ohne Blendung
            hud["blinds"] = []
        replay["hud"] = hud
        # Killfeed: Kopfschuss/Durchschuss/geblendet je Kill (das Replay kennt nur Taeter, Opfer, Waffe)
        try:
            pdf = p.parse_event("player_death")
            flags = {(int(r["tick"]), str(r["user_steamid"])): r for r in pdf.to_dict("records")}
            for k in replay.get("kills") or []:
                r = flags.get((int(k["tick"]), str(k["victim"])))
                if r:
                    k["headshot"] = bool(r.get("headshot"))
                    k["penetrated"] = bool(r.get("penetrated"))
                    k["blind"] = bool(r.get("attackerblind"))
                    k["smoke"] = bool(r.get("thrusmoke"))
                    k["noscope"] = bool(r.get("noscope"))
        except Exception:  # noqa: BLE001 - Killfeed ohne Zusatzsymbole
            pass
    except Exception as e:  # noqa: BLE001 - 2D bleibt
        replay["view3d_error"] = str(e)
    if mo.get("killer"):
        start = max(t0, mo["death_tick"] - 4 * key_moments.TICKRATE)
        try:
            replay["aim"] = aim_trace.scan(str(demo), me, mo["killer"], start, mo["death_tick"],
                                           facts.get("my_sight"), parser=p)
        except Exception as e:  # noqa: BLE001 - 3D bleibt ohne Aim-Spur
            replay["aim_error"] = str(e)
        try:    # dieselbe Spur aus Sicht des Gegners (Gegner-Sicht)
            replay["aim_enemy"] = aim_trace.scan(str(demo), mo["killer"], me, start, mo["death_tick"],
                                                 facts.get("his_sight"), parser=p)
        except Exception:  # noqa: BLE001
            pass
    replay["has_geo"] = map3d.geo_path(cfg, cfg_path, map_key) is not None
    replay["has_render"] = map3d_render.render_parts(cfg, cfg_path, map_key)
    try:
        replay["nades"] = nades3d.scan(str(demo), t0, t1, parser=p)
    except Exception as e:  # noqa: BLE001 - ohne Granaten weiter
        replay["nades_error"] = str(e)
    return replay, 200


def _cache_file(cfg_path: Path, filename: str, round_num: int, tick: int, kind: str) -> Path:
    key = hashlib.sha1(f"{filename}|{round_num}|{tick}|{kind}|{CLIP_VERSION}".encode()).hexdigest()[:24]
    return Path(cfg_path).parent / CACHE_DIR / f"{key}.json.gz"


def cached_clip(cfg: dict, cfg_path: Path, filename: str, round_num: int, tick: int, kind: str) -> tuple[dict, int]:
    """Szene mit 3D aus dem Cache, sonst bauen und ablegen (nur Erfolge)."""
    path = _cache_file(cfg_path, filename, round_num, tick, kind)
    with _warm_lock:
        lock = _clip_locks.setdefault(path.name, threading.Lock())
    # Klick und Vorberechnung wollen oft dieselbe Szene: der zweite wartet auf den ersten
    with lock:
        if path.exists():
            try:
                return json.loads(gzip.decompress(path.read_bytes())), 200
            except Exception:  # noqa: BLE001 - kaputte Datei: neu bauen
                path.unlink(missing_ok=True)
        replay, status = build_clip(cfg, cfg_path, filename, round_num, tick, kind)
        if status == 200:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_bytes(gzip.compress(json.dumps(replay, separators=(",", ":")).encode(), 5))
            tmp.replace(path)
        return replay, status


def prewarm(cfg: dict, cfg_path: Path, filename: str, items: list[dict]) -> None:
    """Alle Szenen eines Matches nacheinander im Hintergrund in den Cache rechnen."""
    todo = [it for it in items if not _cache_file(cfg_path, filename, it["round"], it["tick"], it["kind"]).exists()]
    with _warm_lock:
        if not todo or filename in _warming:
            return
        _warming.add(filename)

    def run():
        try:
            for it in todo:
                try:
                    cached_clip(cfg, cfg_path, filename, it["round"], it["tick"], it["kind"])
                except Exception:  # noqa: BLE001 - eine kaputte Szene haelt die anderen nicht auf
                    pass
        finally:
            with _warm_lock:
                _warming.discard(filename)
    threading.Thread(target=run, name="moments-prewarm", daemon=True).start()


def create_blueprint(load_config, config_path) -> Blueprint:
    bp = Blueprint("moments_page", __name__)

    def _moments_for(cfg: dict, data: dict) -> tuple[list, list, str]:
        """(teuerste Tode, wertvollste Kills, Fehlertext) eines Exports, mit Ueberschriften."""
        moments, good, error = [], [], ""
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
        return moments, good, error

    @bp.route("/momente")
    def page():
        cfg = load_config()
        edir = _export_dir(cfg)
        files = sorted(edir.glob("*_coach.json"), key=export_sort_key, reverse=True)[:MAX_EXPORTS] \
            if edir and edir.exists() else []
        chosen = request.args.get("file") or (files[0].name if files else "")
        # Die Momente laedt der Player selbst nach (/api/momente/liste) - die Siegchance ist teuer
        return render_template("moments.html", files=[f.name for f in files], chosen=chosen, config=cfg)

    @bp.route("/api/momente/liste/<path:filename>")
    def moment_list(filename):
        """Momente eines Exports fuer den Player (Match-Seite laedt sie nach, die Siegchance ist teuer)."""
        cfg = load_config()
        edir = _export_dir(cfg)
        data = _load(edir, filename) if edir else None
        if data is None:
            return jsonify({"error": "Export nicht gefunden"}), 404
        moments, good, error = _moments_for(cfg, data)
        demo_ok = bool(cfg.get("demo_folder") and (Path(cfg["demo_folder"]) /
                       ((data.get("match") or {}).get("demo_file") or "-")).exists())
        items = [{"kind": "kill", "round": m["round"], "tick": m["tick"], "value": round(m.get("gain", 0)),
                  "headline": m["headline"]} for m in good]
        items += [{"kind": "death", "round": m["round"], "tick": m["tick"], "value": -round(m.get("cost", 0)),
                   "headline": m["headline"]} for m in moments]
        map_key = ((data.get("match") or {}).get("map") or "").lower()
        if demo_ok and items:
            # Szenen im Hintergrund vorberechnen: der erste Klick soll nicht 5-15 s warten
            prewarm(cfg, Path(config_path()), filename, items)
        return jsonify({"items": items, "error": error, "demo_ok": demo_ok,
                        "map": map_key, "render_parts": map3d_render.render_parts(cfg, Path(config_path()), map_key)})

    @bp.route("/api/momente/<path:filename>/<int:round_num>/<int:tick>")
    def clip(filename, round_num, tick):
        cfg = load_config()
        kind = request.args.get("kind") or "death"
        if request.args.get("3d"):
            path = _cache_file(Path(config_path()), filename, round_num, tick, kind)
            if not path.exists():
                cached_clip(cfg, Path(config_path()), filename, round_num, tick, kind)
            if path.exists():
                # fertige Szene direkt als gzip ausliefern (~60 statt ~400 KB, kein JSON-Umbau).
                # Ob es Kartendateien gibt, sagt die Momente-Liste (frisch) - nicht der Cache.
                resp = Response(path.read_bytes(), mimetype="application/json")
                resp.headers["Content-Encoding"] = "gzip"
                return resp
            replay, status = build_clip(cfg, Path(config_path()), filename, round_num, tick, kind)
        else:
            replay, status = build_clip(cfg, Path(config_path()), filename, round_num, tick, kind, with3d=False)
        if status != 200:
            return jsonify(replay), status
        map_key = replay.get("map", "")
        # Kartendateien koennen nach dem Cachen dazugekommen sein - immer frisch
        replay["has_geo"] = map3d.geo_path(cfg, Path(config_path()), map_key) is not None
        replay["has_render"] = map3d_render.render_parts(cfg, Path(config_path()), map_key)
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
        part = request.args.get("part", 0, type=int)
        path = map3d_render.render_path(load_config(), Path(config_path()), map_key, part)
        if path is None:
            return jsonify({"error": "Für diese Map gibt es noch keine texturierte 3D-Map"}), 404
        resp = send_file(path, mimetype="model/gltf-binary", max_age=7 * 86400)
        resp.headers["Content-Encoding"] = "gzip"
        return resp

    @bp.route("/api/momente/models/<name>")
    def model(name):
        """Spielermodelle, Waffen, Animationen (models3d.py) und deren manifest.json."""
        if not re.fullmatch(r"[a-z0-9_]+\.glb\.gz|manifest\.json|icons\.json", name):
            return jsonify({"error": "unbekannte Datei"}), 404
        path = models3d.models_dir(load_config(), Path(config_path())) / name
        if not path.exists():
            return jsonify({"error": "Modelle noch nicht exportiert"}), 404
        if name.endswith(".json"):
            return send_file(path, mimetype="application/json", max_age=3600)
        resp = send_file(path, mimetype="model/gltf-binary", max_age=7 * 86400)
        resp.headers["Content-Encoding"] = "gzip"
        return resp

    return bp
