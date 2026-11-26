"""Granaten fuer die 3D-Ansicht der Schluesselmomente: Flugbahnen und Wirkung.

Flugbahn: ``parse_grenades`` liefert je Tick die Position jedes Projektils
(Klassen ``C...Projectile``; die Zeilen ohne "Projectile" sind die Granate in
der Hand und haben keine Position). Wirkung: Detonations-Events mit x/y/z,
Ende ueber das naechste End-Event derselben entityid NACH dem Start (Steam
vergibt die IDs im Match neu - wie app._round_utility).

Alles in Spieleinheiten, Z oben. Bahnen werden auf jeden 2. Tick ausgeduennt.
"""

from __future__ import annotations

import math
from collections import defaultdict

TICKRATE = 64
PATH_STEP = 2

PROJECTILES = {"CSmokeGrenadeProjectile": "smoke", "CFlashbangProjectile": "flash", "CHEGrenadeProjectile": "he",
               "CMolotovProjectile": "molotov", "CDecoyProjectile": "decoy"}

# Typ -> (Start-Event, End-Event oder None, Dauer ohne End-Event in s)
EFFECTS = {
    "smoke": ("smokegrenade_detonate", "smokegrenade_expired", 20.0),
    "molotov": ("inferno_startburn", "inferno_expire", 7.0),
    "flash": ("flashbang_detonate", None, 0.6),
    "he": ("hegrenade_detonate", None, 0.6),
    "decoy": ("decoy_started", "decoy_detonate", 15.0),
}


def _f(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def paths(rows, start: int, end: int) -> list[dict]:
    """Flugbahnen aus parse_grenades-Zeilen (tick, grenade_type, grenade_entity_id, x, y, z, steamid).

    Eine Bahn = eine entity-ID mit zusammenhaengenden Ticks; Luecken > 1 s trennen
    (die ID wird im Match wiederverwendet).
    """
    by: dict[tuple, list] = defaultdict(list)
    for r in rows:
        typ = PROJECTILES.get(str(r["grenade_type"]))
        t = int(r["tick"])
        if typ is None or t < start or t > end:
            continue
        x, y, z = _f(r["x"]), _f(r["y"]), _f(r["z"])
        if x is None or y is None or z is None:
            continue
        by[(int(r["grenade_entity_id"]), typ, str(r.get("steamid", "")))].append((t, x, y, z))
    out = []
    for (eid, typ, sid), pts in by.items():
        pts.sort()
        seg = [pts[0]]
        for p in pts[1:]:
            if p[0] - seg[-1][0] > TICKRATE:
                out.append(_path(typ, sid, seg))
                seg = []
            seg.append(p)
        out.append(_path(typ, sid, seg))
    return sorted(out, key=lambda p: p["pts"][0][0])


def _path(typ: str, sid: str, pts: list) -> dict:
    keep = [p for i, p in enumerate(pts) if i % PATH_STEP == 0 or i == len(pts) - 1]
    return {"type": typ, "sid": sid, "pts": [[t, round(x, 1), round(y, 1), round(z, 1)] for t, x, y, z in keep]}


def effects(events: dict, start: int, end: int) -> list[dict]:
    """Wirkungen aus Event-Zeilen: ``events[name]`` = Liste von dicts (tick, entityid, x, y, z, user_steamid).

    Auch Wirkungen, die vor dem Clip begannen und noch andauern (Rauch steht 20 s).
    """
    out = []
    for typ, (ev_start, ev_end, default_s) in EFFECTS.items():
        ends = defaultdict(list)
        for r in events.get(ev_end, []) if ev_end else []:
            ends[int(r.get("entityid", -1))].append(int(r["tick"]))
        for k in ends:
            ends[k].sort()
        for r in events.get(ev_start, []):
            t0 = int(r["tick"])
            later = [e for e in ends.get(int(r.get("entityid", -1)), []) if e > t0]
            t1 = later[0] if later else t0 + int(default_s * TICKRATE)
            if t1 < start or t0 > end:
                continue
            x, y, z = _f(r.get("x")), _f(r.get("y")), _f(r.get("z"))
            if x is None or y is None or z is None:
                continue
            out.append({"type": typ, "t0": t0, "t1": t1, "x": round(x, 1), "y": round(y, 1), "z": round(z, 1),
                        "sid": str(r.get("user_steamid", ""))})
    return sorted(out, key=lambda e: e["t0"])


def scan(demo_path: str, start: int, end: int, parser=None) -> dict:
    """Flugbahnen und Wirkungen im Fenster [start, end] aus der Demo."""
    from demoparser2 import DemoParser
    p = parser or DemoParser(demo_path)
    rows = []
    try:
        g = p.parse_grenades()
        g = g[(g["tick"] >= start) & (g["tick"] <= end)]
        rows = g.to_dict("records")
    except Exception:  # noqa: BLE001 - ohne Bahnen bleiben die Wirkungen
        pass
    events = {}
    for ev_start, ev_end, _ in EFFECTS.values():
        for name in (ev_start, ev_end):
            if not name or name in events:
                continue
            try:
                events[name] = p.parse_event(name).to_dict("records")
            except Exception:  # noqa: BLE001
                events[name] = []
    return {"paths": paths(rows, start, end), "effects": effects(events, start, end)}
