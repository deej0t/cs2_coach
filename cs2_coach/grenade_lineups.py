"""Granaten-Lineups aus den eigenen Demos: wo stand der Werfer, wohin schaute er, wo landete sie.

Warum: geworfene Granaten pro Runde sind der staerkste gemessene Sieg-Faktor
(d = 0,94), die Qualitaet einer HE dagegen nicht. Wer wenig wirft, hat meist
keinen fertigen Wurf im Kopf. Hier entsteht pro Map eine Bibliothek echter
Wuerfe - von dir und allen anderen in deinen Matches -, die sich auf dem
Server ueben laesst (Plugin: versetzen mit Blickwinkel, Granate in die Hand,
Landepunkt vergleichen).

Nur Wuerfe aus dem Stand (< STAND_SPEED) lassen sich per Versetzen genau
nachstellen; Lauf- und Sprungwuerfe werden mitgezaehlt, aber nicht geuebt.
Gleiche Wuerfe (Abwurf <= SAME_SPOT, Landung <= SAME_LAND Units) werden zu
einem Lineup zusammengefasst - je oefter geworfen, desto eher ein Standard.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

TICKRATE = 64
CACHE_DIR = "grenade_cache"
CACHE_KEY = "nades-v3"  # v2: Abwurf beim Loslassen, Wurfart, Flugbahn  # Demos mit Fehler stehen nicht im Cache, kommen beim naechsten Lauf neu
STAND_SPEED = 10    # u/s
SAME_SPOT = 48      # Units zwischen zwei Abwurfpunkten
SAME_LAND = 150     # Units zwischen zwei Landepunkten
MAX_FLIGHT = 15     # s zwischen Wurf und Detonation
WEAPONS = {"weapon_smokegrenade": "smoke", "weapon_hegrenade": "he", "weapon_flashbang": "flash",
           "weapon_molotov": "molotov", "weapon_incgrenade": "molotov"}
DETONATE = {"smokegrenade_detonate": "smoke", "hegrenade_detonate": "he", "flashbang_detonate": "flash",
            "inferno_startburn": "molotov"}
LABELS = {"smoke": "Smoke", "he": "HE", "flash": "Flash", "molotov": "Molotov"}


def _event(p, name: str, columns: list[str], **kw):
    import pandas as pd
    df = p.parse_event(name, **kw)
    return df if hasattr(df, "iterrows") else pd.DataFrame(columns=columns)


GRENADE_CLASSES = (("smoke", "smoke"), ("hegrenade", "he"), ("flash", "flash"), ("molotov", "molotov"),
                   ("incendiary", "molotov"))
# Wurfstaerke aus der Anfangsgeschwindigkeit der Granate (CS2: 750 * (0,7 * Staerke + 0,3) u/s
# plus 1,25 x Spielergeschwindigkeit): Linksklick 750, beide Tasten ~490, Rechtsklick 225.
CLICK_LIMITS = ((620, "left"), (350, "both"), (0, "right"))
RUN_SPEED = 30      # u/s waagerecht beim Loslassen: darueber ein Laufwurf - per Versetzen nicht nachstellbar
PATH_STEP = 4       # Ticks zwischen zwei Punkten der Flugbahn (16/s)
MAX_PATH = 60


def _kind(cls: str) -> str | None:
    c = cls.lower()
    return next((k for key, k in GRENADE_CLASSES if key in c), None)


def scan_demo(demo_path, me: str, parser_factory=None) -> list[dict]:
    """Jeder Wurf: Abwurf beim LOSLASSEN (erster Punkt der Flugbahn), Wurfart, Flugbahn, Landung.

    v1 nahm Position und Blick aus weapon_fire - das feuert beim Ziehen des Stifts,
    nicht beim Loslassen ("die Positionen sind etwas schlecht", 3.11.).
    """
    if parser_factory is None:
        from demoparser2 import DemoParser as parser_factory
    p = parser_factory(str(demo_path))
    g = p.parse_grenades()
    if not hasattr(g, "iterrows") or g.empty:
        return []
    g = g[g["x"] == g["x"]].sort_values(["grenade_entity_id", "tick"])
    lands = []
    for name, typ in DETONATE.items():
        for _, r in _event(p, name, ["tick", "user_steamid", "x", "y", "z"]).iterrows():
            lands.append((int(r["tick"]), str(r["user_steamid"]), typ, (float(r["x"]), float(r["y"]), float(r["z"]))))
    lands.sort()
    ends = sorted((int(r["tick"]), str(r["winner"])) for _, r in
                  _event(p, "round_end", ["tick", "winner"]).iterrows())

    # Flugbahnen: Entity-IDs werden in einem Match wiederverwendet -> bei Luecken trennen
    tracks, cur, last = [], None, None
    for row in g.itertuples(index=False):
        key = (row.grenade_entity_id, str(row.steamid))
        if cur is None or key != cur["key"] or row.tick - last > 8:
            cur = {"key": key, "kind": _kind(str(row.grenade_type)), "pts": []}
            tracks.append(cur)
        cur["pts"].append((int(row.tick), float(row.x), float(row.y), float(row.z)))
        last = row.tick
    tracks = [t for t in tracks if t["kind"] and len(t["pts"]) >= 3]
    if not tracks:
        return []
    ticks = set()
    for t in tracks:
        t0 = t["pts"][0][0]
        ticks |= {t0 - 1, t0 - 2} | set(range(max(t0 - 64, 1), t0, 4))
    # Nicht velocity_X/Y/Z: die lieferten am 3.11. Werte bis 300.000 - Geschwindigkeit wie im
    # Parser aus dem Positionsdelta zum Vortick.
    props = ["X", "Y", "Z", "pitch", "yaw", "team_num", "is_airborne", "ducking"]
    rows = p.parse_ticks(props, ticks=sorted(ticks))
    state = {(int(r["tick"]), str(r["steamid"])): r for _, r in rows.iterrows()}

    used, out = set(), []
    for t in tracks:
        sid = t["key"][1]
        t0, x0, y0, z0 = t["pts"][0]
        me_row = state.get((t0 - 1, sid))
        if me_row is None or me_row["X"] != me_row["X"]:
            continue
        land = next((l for l in lands if t0 <= l[0] <= t0 + MAX_FLIGHT * TICKRATE and l[1] == sid
                     and l[2] == t["kind"] and l not in used), None)
        if land is None:
            continue
        used.add(land)
        prev = state.get((t0 - 2, sid))
        if prev is None or prev["X"] != prev["X"]:
            continue
        vx, vy, vz = ((float(me_row[k]) - float(prev[k])) * TICKRATE for k in ("X", "Y", "Z"))
        t1, x1, y1, z1 = t["pts"][1]
        dt = max(t1 - t0, 1) / TICKRATE
        throw = math.dist((x0, y0, z0), (x1, y1, z1)) / dt
        own = math.dist(((x1 - x0) / dt - 1.25 * vx, (y1 - y0) / dt - 1.25 * vy), (0, 0)) if throw else 0
        own = math.hypot(own, (z1 - z0) / dt - 1.25 * vz)
        click = next(c for limit, c in CLICK_LIMITS if own >= limit)
        airborne = bool(me_row["is_airborne"])
        speed = math.hypot(vx, vy)
        # Stand vor dem Sprung = wo man hingestellt wird
        ground = None
        for k in range(t0 - 1, max(t0 - 64, 1) - 1, -1):
            r = state.get((k, sid))
            if r is not None and not bool(r["is_airborne"]):
                ground = r
                break
        if ground is None:
            continue  # kein Bodenkontakt in der Sekunde davor: sonst stuende man in der Luft (3.11.: Z -112 statt -168)
        team = int(me_row["team_num"]) if me_row["team_num"] == me_row["team_num"] else 0
        winner = next((w for e, w in ends if e >= t0), "")
        path = [[round(px), round(py), round(pz)] for k, (pt, px, py, pz) in enumerate(t["pts"])
                if k % PATH_STEP == 0 and pt <= land[0]][:MAX_PATH] + [[round(c) for c in land[3]]]
        out.append({"type": t["kind"], "me": sid == me, "side": "CT" if team == 3 else "T",
                    "pos": [round(float(ground["X"]), 1), round(float(ground["Y"]), 1), round(float(ground["Z"]), 1)],
                    "ang": [round(float(me_row["pitch"]), 2), round(float(me_row["yaw"]), 2)],
                    "land": [round(c, 1) for c in land[3]], "speed": round(speed),
                    "style": {"click": click, "jump": airborne and vz > 0, "air": airborne,
                              "duck": bool(me_row["ducking"]), "run": speed > RUN_SPEED and not airborne},
                    "won": {"CT": team == 3, "T": team == 2}.get(winner.upper()),
                    "flight": round((land[0] - t0) / TICKRATE, 2), "path": path})
    return out


def nade_keys(style: dict) -> str:
    """Tasten-Anzeige: "[LEERTASTE] + [LINKSKLICK] loslassen"."""
    click = {"left": "[LINKSKLICK]", "right": "[RECHTSKLICK]", "both": "[LINKS+RECHTS]"}[style.get("click", "left")]
    if style.get("jump"):
        # CS2: der Sprungwurf landet nur gleich, wenn Springen und Loslassen im selben Moment passieren
        return f"{click} halten, dann [LEERTASTE] und loslassen GLEICHZEITIG (Jumpthrow-Bind)"
    if style.get("duck"):
        return f"[STRG] halten, {click} loslassen"
    return f"{click} loslassen"


def style_text(style: dict) -> str:
    """"Sprungwurf + Linksklick" - so, wie man es im Spiel macht."""
    click = {"left": "Linksklick", "right": "Rechtsklick", "both": "beide Maustasten"}[style.get("click", "left")]
    if style.get("jump"):
        return f"Sprungwurf + {click}"
    if style.get("duck"):
        return f"geduckt + {click}"
    return f"im Stand + {click}"


def collect(cfg: dict, config_path: Path, bsp: str | None = None, parser_factory=None, log=print,
            max_new: int = 200) -> list[dict]:
    from .practice import _map_to_bspname
    vault = (cfg.get("obsidian_vault_path") or "").strip()
    folder = (cfg.get("demo_folder") or "").strip()
    me = str(cfg.get("steam_id") or "")
    if not vault or not folder:
        return []
    cache_dir = Path(config_path).parent / CACHE_DIR
    out, scanned = [], 0
    for f in sorted((Path(vault) / cfg.get("coach_subfolder", "CS2-Coach") / "exports").glob("*_coach.json")):
        try:
            match = json.loads(f.read_text(encoding="utf-8")).get("match", {})
        except (OSError, ValueError):
            continue
        name = _map_to_bspname(match.get("map", ""))
        if (bsp and name != bsp) or not match.get("demo_file"):
            continue
        demo = Path(folder) / match["demo_file"]
        cache = cache_dir / f"{demo.stem}.json"
        try:
            known = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            known = {}
        if CACHE_KEY not in known:
            if not demo.exists() or scanned >= max_new:
                continue
            scanned += 1
            try:
                known[CACHE_KEY] = scan_demo(demo, me, parser_factory=parser_factory)
            except Exception as e:  # noqa: BLE001
                log(f"[nades] {demo.name}: {e}")
                continue
            cache_dir.mkdir(exist_ok=True)
            cache.write_text(json.dumps(known), encoding="utf-8")
        out += [{**n, "map": name, "export": f.name} for n in known[CACHE_KEY]]
    return out


SAME_HEIGHT = 24    # Units: auf der Papiertonne (Mirage T-Spawn, +56) ist ein anderes Lineup als davor


def library(throws: list[dict], limit: int = 40) -> list[dict]:
    """Wuerfe aus dem Stand oder Sprungwuerfe zu Lineups buendeln (gleiche Wurfart); die haeufigsten zuerst.

    Abwurf auch nach Hoehe getrennt: am 3.11. landeten 110 Wuerfe vom Boden und 8 von der
    Papiertonne in einer Gruppe, und ausgerechnet ein Tonnen-Wurf war der Vertreter
    ("da steht man nicht auf der Papiertonne"). Vertreter ist jetzt der Wurf, der dem
    Median der Gruppe am naechsten liegt - also die typische Ausfuehrung.
    """
    groups: list[dict] = []
    for n in throws:
        st = n.get("style") or {}
        if st.get("run") or (st.get("air") and not st.get("jump")):
            continue  # Lauf- und Fallwuerfe lassen sich per Versetzen nicht nachstellen
        for g in groups:
            r = g["members"][0]
            if (r["type"] == n["type"] and r["side"] == n["side"] and r["style"].get("click") == st.get("click")
                    and r["style"].get("jump") == st.get("jump") and math.dist(r["pos"][:2], n["pos"][:2]) <= SAME_SPOT
                    and abs(r["pos"][2] - n["pos"][2]) <= SAME_HEIGHT
                    and math.dist(r["land"][:2], n["land"][:2]) <= SAME_LAND):
                g["members"].append(n)
                break
        else:
            groups.append({"members": [n]})
    out = []
    for g in groups:
        ms = g["members"]
        mid = [sorted(m[key][i] for m in ms)[len(ms) // 2] for key, i in (("pos", 0), ("pos", 1), ("pos", 2),
                                                                         ("ang", 0), ("ang", 1))]
        rep = min(ms, key=lambda m: math.dist(m["pos"], mid[:3]) + 10 * math.dist(m["ang"], mid[3:]))
        out.append({**{k: rep[k] for k in ("type", "side", "pos", "ang", "land", "flight", "map", "style", "path")},
                    "count": len(ms), "mine": sum(bool(m["me"]) for m in ms), "won": sum(bool(m["won"]) for m in ms)})
    out.sort(key=lambda g: (-g["count"], -g["won"]))
    return out[:limit]
