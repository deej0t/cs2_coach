"""Aim-Werte aller Spieler auf dem eigenen Server: Fadenkreuz, Counter-Strafing, Accuracy, HS.

Wunsch 12.11.: fuer jeden Spieler auf dem privaten Server dieselben Werte wie in
der Match-Analyse, oeffentlich auf /server. Quelle sind GOTV-Demos statt des
Plugins: die Metriken des Parsers sind gegen echte Demos geprueft, und ein
CS2-Update, das CounterStrikeSharp lahmlegt (12.11.), haelt das hier nicht auf.

Ablauf:
1. maybe_record(): sobald Menschen spielen und kein Training laeuft, startet der
   Coach per RCON ``tv_record coach_<Zeit>`` (hoechstens jede Minute; laeuft schon
   eine Aufnahme, antwortet CS2 nur "Already recording"). Der Mapwechsel beendet
   die Aufnahme, ebenso maybe_stop(), wenn der letzte Mensch geht.
2. docker/unraid/server-demos.sh (Host-Cron) schiebt fertige Demos aus dem
   CS2-Spielordner nach server_demos/ neben der Config.
3. analyse_pending() wertet sie mit parse_demo() aus (alle Spieler auf einmal),
   speichert nur die Werte nach server_aim/<demo>.json und loescht die Demo.
4. board() fasst alles je Spieler zusammen - Fadenkreuz als Median ueber alle
   einzelnen Kills, nicht als Mittel der Demo-Mediane.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from statistics import median

DEMO_DIR = "server_demos"
RESULT_DIR = "server_aim"
STATE_FILE = ".tv_record.json"
LOCK_FILE = ".server_aim.lock"
RECORD_EVERY = 60     # s zwischen zwei tv_record-Versuchen
LOCK_STALE = 1800     # s: haengengebliebene Auswertung
MIN_KILLS = 10        # darunter kein Fadenkreuz-Wert (Median aus 3 Kills sagt nichts)
_HUMAN = re.compile(r"<\[U:1:\d+\]>")


def _human_sid(sid: str) -> bool:
    return len(sid) == 17 and sid.isdigit() and sid != "0"


# -- Aufnahme ----------------------------------------------------------------

def _state(config_path: Path) -> dict:
    try:
        return json.loads((Path(config_path).parent / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(config_path: Path, state: dict) -> None:
    (Path(config_path).parent / STATE_FILE).write_text(json.dumps(state), encoding="utf-8")


def due(config_path: Path, lines: list[str], now: float | None = None) -> bool:
    """Billiger Vorab-Test im Empfang: lohnt sich ein RCON-Thread fuer diesen Stapel?"""
    if not any(_HUMAN.search(ln) for ln in lines):
        return False
    if any(" disconnected" in ln for ln in lines):
        return True
    now = time.time() if now is None else now
    return now - _state(config_path).get("tried", 0) >= RECORD_EVERY


def on_lines(config_path: Path, lines: list[str], send) -> None:
    """Im Hintergrund-Thread des Empfangs: Aufnahme starten oder beenden."""
    try:
        if not maybe_stop(lines, send):
            maybe_record(config_path, lines, send)
    except Exception as e:  # noqa: BLE001 - RCON weg: naechster Stapel
        print(f"[server_aim] {e}", flush=True)


def maybe_record(config_path: Path, lines: list[str], send, now: float | None = None) -> str | None:
    """tv_record, wenn Menschen im Log aktiv sind - None, wenn nichts gesendet wurde."""
    from .training_server import is_active
    if send is None or is_active(config_path) or not any(_HUMAN.search(ln) for ln in lines):
        return None
    now = time.time() if now is None else now
    state = _state(config_path)
    if now - state.get("tried", 0) < RECORD_EVERY:
        return None
    state["tried"] = now
    _save_state(config_path, state)
    name = "coach_" + datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S")
    reply = (send(f"tv_record {name}") or [""])[0]
    if "not active" in reply:
        # Jeder Mapwechsel setzt tv_enable zurueck, die server.cfg setzt es zu spaet wieder:
        # "SourceTV not active". Mit tv_enable_dynamic startet GOTV beim Umschalten sofort
        # (gemessen 12.11.); aufgenommen wird beim naechsten Versuch eine Minute spaeter.
        send("tv_enable_dynamic 1", "tv_enable 0", "tv_delay 0", "tv_enable 1")
        state["tried"] = now - RECORD_EVERY + 10
        _save_state(config_path, state)
    if "Already" not in reply:
        print(f"[server_aim] tv_record {name}: {reply.strip()[:120]}", flush=True)
    return reply


def maybe_stop(lines: list[str], send) -> bool:
    """Nach einem Disconnect: kein Mensch mehr da -> Aufnahme beenden, damit die Demo fertig wird."""
    if send is None or not any(" disconnected" in ln and _HUMAN.search(ln) for ln in lines):
        return False
    from .training_server import _humans
    if _humans((send("status") or [""])[0]):
        return False
    send("tv_stoprecord")
    return True


# -- Auswertung --------------------------------------------------------------

def _row(p) -> dict:
    return {"sid": p.steam_id, "name": p.name, "kills": p.kills, "deaths": p.deaths,
            "headshots": p.headshots, "damage": p.damage,
            "cp": [round(e, 2) for e in p.crosshair_placement_errors],
            "standing": p.shots_standing, "running": p.shots_running,
            "hits": p.total_shots_hit, "shots": p.shots_fired}


def analyse_pending(config_path: Path, parse=None, log=print) -> int:
    """Alle Demos in server_demos/ auswerten; gibt die Zahl der ausgewerteten zurueck."""
    if parse is None:
        from .parser import parse_demo as parse
    base = Path(config_path).parent
    out = base / RESULT_DIR
    done = 0
    for dem in sorted((base / DEMO_DIR).glob("*.dem")):
        target = out / f"{dem.stem}.json"
        if not target.exists():
            try:
                r = parse(str(dem))
            except Exception as e:  # noqa: BLE001 - kaputte/abgebrochene Demo: beiseite legen
                log(f"[server_aim] {dem.name}: {e}")
                dem.rename(dem.with_suffix(".kaputt"))
                continue
            players = [_row(p) for p in r.all_players if _human_sid(str(p.steam_id)) and (p.kills or p.deaths)]
            out.mkdir(exist_ok=True)
            target.write_text(json.dumps({
                "demo": dem.stem, "map": r.map_name,
                "time": datetime.fromtimestamp(dem.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                "players": players}, ensure_ascii=False), encoding="utf-8")
            done += 1
        dem.unlink()  # nur die Werte bleiben (eine Stunde Demo = 50-150 MB)
    return done


def start_background(config_path: Path) -> bool:
    """Auswertung im Hintergrund, wenn Demos warten - eine zur Zeit (O_EXCL-Sperre)."""
    base = Path(config_path).parent
    if not any((base / DEMO_DIR).glob("*.dem")):
        return False
    lock = base / LOCK_FILE
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > LOCK_STALE:
            lock.unlink()
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except OSError:
        return False

    def work():
        try:
            analyse_pending(config_path)
        finally:
            lock.unlink(missing_ok=True)

    threading.Thread(target=work, daemon=True).start()
    return True


# -- Anzeige -----------------------------------------------------------------

def board(config_path: Path) -> dict:
    """Je Spieler ueber alle ausgewerteten Demos; sortiert nach Kills."""
    acc: dict[str, dict] = {}
    files = sorted((Path(config_path).parent / RESULT_DIR).glob("*.json"))
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for p in data.get("players", []):
            a = acc.setdefault(p["sid"], {"sid": p["sid"], "kills": 0, "deaths": 0, "headshots": 0, "cp": [],
                                          "standing": 0, "running": 0, "hits": 0, "shots": 0, "games": 0})
            a["name"], a["last"] = p["name"], data.get("time", "")
            a["games"] += 1
            for k in ("kills", "deaths", "headshots", "standing", "running", "hits", "shots"):
                a[k] += p.get(k, 0)
            a["cp"] += p.get("cp", [])
    rows = []
    for a in acc.values():
        moves = a["standing"] + a["running"]
        rows.append({
            "name": a["name"], "sid": a["sid"], "games": a["games"], "last": a["last"],
            "kills": a["kills"], "deaths": a["deaths"],
            "kd": round(a["kills"] / max(a["deaths"], 1), 2),
            "hs": round(100 * a["headshots"] / a["kills"]) if a["kills"] else None,
            "cp": round(median(a["cp"]), 1) if len(a["cp"]) >= MIN_KILLS else None,
            "cp_n": len(a["cp"]),
            "cs": round(100 * a["standing"] / moves) if moves else None,
            "acc": round(100 * a["hits"] / a["shots"]) if a["shots"] else None,
        })
    rows.sort(key=lambda r: -r["kills"])
    return {"rows": rows, "demos": len(files)}
