"""Match-Plan fuer den Live-Coach: welche Granaten DU auf dieser Map wirfst.

Wunsch 15.11.: die Warnung "GRANATE!" sagte nicht, welche und wohin - mitten
in der Runde zu abstrakt. Grundlage sind deine eigenen Wuerfe aus den Demos
(grenade_lineups: Wurfpunkt, Landung, Seite, wie oft von dir geworfen); der
Ort der Landung kommt aus den Callouts der Kill-Positionen (Schema 5), also
ohne eigene Kartenpolygone.

Der Plan wird beim ersten GSI-Signal einer Map im Hintergrund gebaut
(liest den Lineup-Cache, scannt hoechstens MAX_NEW neue Demos) und liegt in
.live_plan.json neben der Config - nie im GSI-Request selbst, der hat 5 s.

Es sind deine *haeufigsten* Granaten dieser Map und Seite, nicht nachweislich
die besten; ob eine bestimmte Granate Runden gewinnt, ist nicht gemessen.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from pathlib import Path

PLAN_FILE = ".live_plan.json"
LOCK_FILE = ".live_plan.lock"
MAX_NEW = 3            # neue Demos pro Plan scannen (je ~10-20 s, im Hintergrund)
PER_SIDE = 6           # so viele eigene Lineups je Seite merken (fuer die Warnung)
SHOW = 3               # so viele zeigt die Match-Start-Karte
CALLOUT_RANGE = 600    # weiter weg = kein Name
REFRESH_H = 6          # aelter = neu bauen, auch auf derselben Map

TYPE_LABEL = {"smoke": "Smoke", "flash": "Flash", "he": "HE", "molotov": "Molly"}
GSI_TYPE = {"weapon_smokegrenade": "smoke", "weapon_flashbang": "flash", "weapon_hegrenade": "he",
            "weapon_molotov": "molotov", "weapon_incgrenade": "molotov"}

_cache: dict = {"mtime": None, "plan": {}}


def pretty_place(place: str) -> str:
    """BombsiteA -> Bombsite A, TopofMid -> Top of Mid, CTSpawn -> CT Spawn."""
    s = re.sub(r"([a-z])of([A-Z])", r"\1 of \2", place or "")
    s = re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", s)
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    return s.strip()


def callouts(exports: list[dict]) -> list[tuple[float, float, float, str]]:
    """Bekannte Positionen mit Callout aus den Kill-Positionen (eigene und gegnerische)."""
    pts = []
    for e in exports:
        for kp in e.get("kill_positions") or []:
            if kp.get("p"):
                pts.append((kp.get("x", 0.0), kp.get("y", 0.0), kp.get("z", 0.0), kp["p"]))
            if kp.get("ep"):
                pts.append((kp.get("ex", 0.0), kp.get("ey", 0.0), kp.get("ez", 0.0), kp["ep"]))
    return pts


def nearest_place(pts: list, xyz: list[float]) -> str:
    """Callout des naechsten bekannten Punkts; Hoehe zaehlt doppelt (Stockwerke)."""
    best, best_d = "", CALLOUT_RANGE
    for x, y, z, place in pts:
        d = math.sqrt((x - xyz[0]) ** 2 + (y - xyz[1]) ** 2 + (2 * (z - xyz[2])) ** 2)
        if d < best_d:
            best, best_d = place, d
    return pretty_place(best)


def _own_lineups(lib: list[dict], pts: list, side: str) -> list[dict]:
    own = sorted((x for x in lib if x.get("side") == side and x.get("mine", 0) > 0),
                 key=lambda x: -x["mine"])[:PER_SIDE]
    return [{"type": x["type"], "label": TYPE_LABEL.get(x["type"], x["type"]),
             "place": nearest_place(pts, x.get("land") or [0, 0, 0]), "mine": x["mine"]} for x in own]


def build(cfg: dict, config_path: Path, bsp: str, collect=None, library=None) -> dict:
    """Plan fuer eine Map (bsp = "de_mirage")."""
    from . import grenade_lineups as gl
    from .live_coach import target_early
    from .report_ai import _exports
    from .utility_analysis import early_utility
    collect = collect or gl.collect
    library = library or gl.library
    short = bsp.removeprefix("de_")
    exports = []
    for f in _exports(cfg):
        if f"_{short}_" not in f.name:
            continue
        try:
            exports.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    pts = callouts(exports)
    try:
        lib = library(collect(cfg, config_path, bsp, max_new=MAX_NEW, log=lambda *_: None), limit=400)
    except Exception:  # noqa: BLE001 - ohne Lineups bleibt der Rest des Plans
        lib = []
    k = n = 0
    for e in exports[-10:]:
        c = early_utility(e)
        if c:
            k, n = k + c[0], n + c[1]
    last = None
    if exports:
        m = exports[-1].get("match") or {}
        last = {"date": (m.get("datetime") or m.get("date") or "")[:10],
                "score": f"{m.get('score_own', '?')}:{m.get('score_enemy', '?')}", "result": m.get("result", "")}
    target = target_early(cfg, bsp)
    name = short.capitalize()
    return {
        "map": bsp, "name": name, "id": int(time.time()), "matches": len(exports),
        "target": target, "quote": {"k": k, "n": n, "pct": round(100 * k / n) if n else None},
        "last": last, "sides": {"T": _own_lineups(lib, pts, "T"), "CT": _own_lineups(lib, pts, "CT")},
        "announce": f"{name}. Ziel {target:.0f} Prozent fruehe Granate." if target else f"{name}.",
    }


def load(config_path: Path) -> dict:
    """Aktueller Plan (gecacht auf mtime - wird bei jedem GSI-Signal gelesen)."""
    path = Path(config_path).parent / PLAN_FILE
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _cache["mtime"] != mtime:
        try:
            _cache.update(mtime=mtime, plan=json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return {}
    return _cache["plan"]


def ensure(cfg: dict, config_path: Path, bsp: str) -> None:
    """Plan fuer diese Map im Hintergrund bauen, falls er fehlt oder alt ist."""
    plan = load(config_path)
    if plan.get("map") == bsp and time.time() - plan.get("id", 0) < REFRESH_H * 3600:
        return
    lock = Path(config_path).parent / LOCK_FILE
    try:
        if time.time() - lock.stat().st_mtime > 300:   # haengengebliebener Lauf
            lock.unlink()
    except OSError:
        pass
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except OSError:
        return   # laeuft schon (anderer Worker)

    def run():
        try:
            data = build(cfg, config_path, bsp)
            (Path(config_path).parent / PLAN_FILE).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        finally:
            try:
                lock.unlink()
            except OSError:
                pass

    threading.Thread(target=run, daemon=True).start()


def suggest(plan: dict, side: str, nades: dict[str, int]) -> dict | None:
    """Deine haeufigste Granate fuer diese Seite, die du gerade dabei hast."""
    have = {GSI_TYPE.get(k) for k in nades}
    return next((x for x in (plan.get("sides") or {}).get(side, []) if x["type"] in have), None)
