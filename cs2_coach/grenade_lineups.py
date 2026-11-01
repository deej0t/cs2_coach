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
CACHE_KEY = "nades-v1"
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


def scan_demo(demo_path, me: str, parser_factory=None) -> list[dict]:
    if parser_factory is None:
        from demoparser2 import DemoParser as parser_factory
    p = parser_factory(str(demo_path))
    fire = _event(p, "weapon_fire", ["tick", "user_steamid", "weapon"], player=["X", "Y", "Z", "pitch", "yaw"])
    fire = fire[fire["weapon"].astype(str).isin(WEAPONS)]
    if fire.empty:
        return []
    lands = []
    for name, typ in DETONATE.items():
        for _, r in _event(p, name, ["tick", "user_steamid", "x", "y", "z"]).iterrows():
            lands.append((int(r["tick"]), str(r["user_steamid"]), typ, (float(r["x"]), float(r["y"]), float(r["z"]))))
    lands.sort()
    ends = sorted((int(r["tick"]), str(r["winner"])) for _, r in
                  _event(p, "round_end", ["tick", "winner"]).iterrows())
    ticks = sorted({int(t) for t in fire["tick"]} | {int(t) - 1 for t in fire["tick"]})
    rows = p.parse_ticks(["X", "Y", "team_num"], ticks=ticks)
    pos = {(int(r["tick"]), str(r["steamid"])): (float(r["X"]), float(r["Y"]), int(r["team_num"] or 0))
           for _, r in rows.iterrows()}
    used, out = set(), []
    for _, f in fire.iterrows():
        t, sid, typ = int(f["tick"]), str(f["user_steamid"]), WEAPONS[str(f["weapon"])]
        land = next((l for l in lands if l[0] > t and l[0] <= t + MAX_FLIGHT * TICKRATE and l[1] == sid
                     and l[2] == typ and l not in used), None)
        if land is None:
            continue  # nie detoniert (z. B. Molotov in der Luft, Runde vorbei)
        used.add(land)
        here, before = pos.get((t, sid)), pos.get((t - 1, sid))
        speed = math.dist(here[:2], before[:2]) * TICKRATE if here and before else None
        team = here[2] if here else 0
        winner = next((w for e, w in ends if e >= t), "")
        out.append({"type": typ, "me": sid == me, "side": "CT" if team == 3 else "T",
                    "pos": [round(float(f["user_X"]), 1), round(float(f["user_Y"]), 1), round(float(f["user_Z"]), 1)],
                    "ang": [round(float(f["user_pitch"]), 2), round(float(f["user_yaw"]), 2)],
                    "land": [round(c, 1) for c in land[3]], "speed": round(speed) if speed is not None else None,
                    "won": {"CT": team == 3, "T": team == 2}.get(winner.upper()),
                    "flight": round((land[0] - t) / TICKRATE, 2)})
    return out


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


def library(throws: list[dict], limit: int = 40) -> list[dict]:
    """Wuerfe aus dem Stand zu Lineups buendeln; die haeufigsten zuerst."""
    groups: list[dict] = []
    for n in throws:
        if n.get("speed") is None or n["speed"] > STAND_SPEED:
            continue
        for g in groups:
            if (g["type"] == n["type"] and g["side"] == n["side"] and math.dist(g["pos"][:2], n["pos"][:2]) <= SAME_SPOT
                    and math.dist(g["land"][:2], n["land"][:2]) <= SAME_LAND):
                g["count"] += 1
                g["mine"] += n["me"]
                g["won"] += bool(n["won"])
                break
        else:
            groups.append({**{k: n[k] for k in ("type", "side", "pos", "ang", "land", "flight", "map")},
                           "count": 1, "mine": int(n["me"]), "won": int(bool(n["won"]))})
    groups.sort(key=lambda g: (-g["count"], -g["won"]))
    return groups[:limit]
