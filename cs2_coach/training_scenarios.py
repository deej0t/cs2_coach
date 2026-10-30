"""Trainingsszenarien mit mehreren Gegnern aus den eigenen Demos: Situation, Retake, Site halten.

Wunsch: "Szenarien wie Retake, Site Hold - gerne mit mehreren Bots, die
nacheinander oder gleichzeitig kommen, halt realitaetsnah". Realitaetsnah
heisst hier: nichts wird erfunden. Jeder Gegner startet dort, wo er in der
Demo stand, und laeuft seinen echten Weg (bis er dich sieht, dann
uebernimmt die Bot-KI - Plugin 1.5.0). Ob sie nacheinander oder gleichzeitig
kommen, entscheidet also, wie es damals war.

- situation: dein Tod, mit allen Gegnern, die damals in der Naehe waren
  (nicht nur der Killer). Start LEAD Sekunden vorher.
- retake: Bombe gelegt, du bist CT und lebst. Start RETAKE_DELAY s nach dem
  Plant; die Ts stehen, wo sie hielten. (Ohne Bombe auf dem Server - legen
  kann der Bot sie per Konsole nicht.)
- halten: du bist CT, mehrere Ts kommen. Start HOLD_LEAD s vor deinem ersten
  Kontakt (erster Treffer von oder an dir in der Runde).

Gemessen am 30.10. an der Mirage-Demo vom 27.10.: bomb_planted liefert
tick + site (Entity-Index, nicht A/B), parse_ticks liefert team_num,
is_alive und health fuer alle zehn Spieler; Events 4,1 s, Ticks 0,3 s.
Ergebnisse werden je Demo in training_cache/ zwischengespeichert.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

MODES = {"duell": "Duell", "situation": "Situation", "retake": "Retake", "halten": "Site halten"}
MULTI = ("situation", "retake", "halten")
MODE_HELP = {"duell": "nur dein Killer", "situation": "alle Gegner in der Naehe",
             "retake": "Bombe liegt, du bist CT", "halten": "du haeltst als CT, mehrere Ts kommen"}
MAX_ENEMIES = 5
NEAR = 2500  # Units: Gegner, die zur Situation gehoeren (der Killer immer)
HOLD_LEAD = 5
RETAKE_DELAY = 1
PATH_AFTER = {"retake": 10, "halten": 12}  # Sekunden Weg ab Start; Situation: bis zu deinem Tod
TICKRATE = 64
PATH_HZ = 16
CT = 3
SCAN_VERSION = 1
MAX_NEW_SCANS = 15  # beim ersten Mal nicht ewig lesen: Retakes brauchten 92 s fuer 20 (30.10.)


def _row(rows, sid: str):
    r = rows[rows["steamid"].astype(str) == sid]
    return None if r.empty else r.iloc[0]


def _point(r) -> list[float]:
    from .training_server import _angle
    return [round(float(r["X"]), 1), round(float(r["Y"]), 1), round(float(r["Z"]), 1),
            _angle(r["pitch"]), _angle(r["yaw"])]


def _inventory(r) -> list[str]:
    """[aktive Waffe, alle Items] als weapon_*-Namen (wie training_server.pre_positions)."""
    from .training_server import classname
    raw = r.get("inventory")
    try:
        inv = [classname(x) for x in raw] if not isinstance(raw, str) else []
    except TypeError:
        inv = []
    return [classname(r.get("active_weapon_name") or "")] + [x for x in inv if x]


def scan_demo(demo_path, me: str, lead: float, parser_factory=None) -> dict[str, list[dict]]:
    """Alle Szenarien einer Demo, je Modus. Ohne Datei-Cache (siehe collect)."""
    if parser_factory is None:
        from demoparser2 import DemoParser as parser_factory
    import pandas as pd
    p = parser_factory(str(demo_path))

    def event(name: str, columns: list[str]):
        """demoparser2 liefert [] statt einer leeren Tabelle, wenn ein Ereignis nie vorkommt
        (am 30.10. bei zwei Demos ohne Bomben-Plant: "'list' object has no attribute 'iterrows'")."""
        df = p.parse_event(name)
        return df if hasattr(df, "iterrows") else pd.DataFrame(columns=columns)

    deaths = event("player_death", ["tick", "user_steamid", "attacker_steamid"])
    plants = event("bomb_planted", ["tick", "site", "user_steamid"])
    hurts = event("player_hurt", ["tick", "user_steamid", "attacker_steamid"])
    freeze = sorted(int(t) for t in event("round_freeze_end", ["tick"])["tick"])

    def round_start(tick):
        before = [f for f in freeze if f <= tick]
        return before[-1] if before else 0

    def round_no(tick):
        return sum(1 for f in freeze if f <= tick)

    anchors = []  # (mode, start, end, focus_steamid)
    for _, d in deaths[deaths["user_steamid"].astype(str) == me].iterrows():
        t = int(d["tick"])
        anchors.append(("situation", max(t - int(lead * TICKRATE), round_start(t)), t, str(d["attacker_steamid"])))
    for _, pl in plants.iterrows():
        t = int(pl["tick"]) + RETAKE_DELAY * TICKRATE
        anchors.append(("retake", t, t + PATH_AFTER["retake"] * TICKRATE, None))
    mine = hurts[(hurts["user_steamid"].astype(str) == me) | (hurts["attacker_steamid"].astype(str) == me)]
    seen = set()
    for _, h in mine.sort_values("tick").iterrows():
        t = int(h["tick"])
        rs = round_start(t)
        if rs in seen:
            continue  # nur der erste Kontakt der Runde
        seen.add(rs)
        start = max(t - HOLD_LEAD * TICKRATE, rs)
        anchors.append(("halten", start, start + PATH_AFTER["halten"] * TICKRATE, None))
    if not anchors:
        return {}

    step = TICKRATE // PATH_HZ
    ticks = sorted({x for _, s, e, _ in anchors for x in range(s, e + 1, step)} | {s for _, s, _, _ in anchors})
    df = p.parse_ticks(["X", "Y", "Z", "pitch", "yaw", "team_num", "is_alive", "inventory",
                        "active_weapon_name"], ticks=ticks)
    by_tick = {int(t): g for t, g in df.groupby("tick")}

    out: dict[str, list[dict]] = {m: [] for m in MULTI}
    for mode, start, end, focus in anchors:
        rows = by_tick.get(start)
        if rows is None:
            continue
        me_row = _row(rows, me)
        if me_row is None or not bool(me_row["is_alive"]):
            continue
        my_team = int(me_row["team_num"])
        if mode in ("retake", "halten") and my_team != CT:
            continue
        cands = []
        for _, r in rows.iterrows():
            team = int(r["team_num"]) if r["team_num"] == r["team_num"] else 0
            if team in (my_team, 0, 1) or not bool(r["is_alive"]):
                continue
            sid = str(r["steamid"])
            dist = math.dist((float(r["X"]), float(r["Y"])), (float(me_row["X"]), float(me_row["Y"])))
            if mode != "retake" and dist > NEAR and sid != focus:
                continue
            cands.append((sid != focus, dist, sid, r))
        if not cands or (mode == "halten" and len(cands) < 2):
            continue
        cands.sort(key=lambda c: (c[0], c[1]))  # Killer zuerst, dann die naechsten
        enemies = []
        for _, _, sid, r in cands[:MAX_ENEMIES]:
            path = []
            for x in range(start, end + 1, step):
                rr = by_tick.get(x)
                q = _row(rr, sid) if rr is not None else None
                if q is None or not bool(q["is_alive"]):
                    break
                path.append(_point(q))
            enemies.append({"name": str(r["name"]), "start": _point(r), "path": path, "items": _inventory(r)})
        out[mode].append({"mode": mode, "round": round_no(start), "tick": start, "me": _point(me_row),
                          "me_items": _inventory(me_row), "enemies": enemies})
    return out


def collect(cfg: dict, config_path: Path, bsp: str, mode: str, limit: int = 20,
            parser_factory=None, log=print) -> list[dict]:
    """Szenarien eines Modus fuer eine Map, aus den neuesten Matches zuerst, je Demo zwischengespeichert."""
    from .practice import _map_to_bspname
    from .training_server import lead_seconds

    vault = (cfg.get("obsidian_vault_path") or "").strip()
    folder = (cfg.get("demo_folder") or "").strip()
    me = str(cfg.get("steam_id") or "")
    if not vault or not folder or not me:
        return []
    lead = lead_seconds(cfg)
    cache_dir = Path(config_path).parent / "training_cache"
    key = f"scan-v{SCAN_VERSION}-lead{lead:g}"
    out: list[dict] = []
    scanned = 0
    exports = sorted((Path(vault) / cfg.get("coach_subfolder", "CS2-Coach") / "exports").glob("*_coach.json"),
                     reverse=True)
    for f in exports:
        if len(out) >= limit:
            break
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        match = data.get("match", {})
        if _map_to_bspname(match.get("map", "")) != bsp or not match.get("demo_file"):
            continue
        demo = Path(folder) / match["demo_file"]
        cache = cache_dir / f"{demo.stem}.json"
        try:
            known = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            known = {}
        if key not in known:
            if not demo.exists() or scanned >= MAX_NEW_SCANS:
                continue
            scanned += 1
            try:
                known[key] = scan_demo(demo, me, lead, parser_factory=parser_factory)
            except Exception as e:  # noqa: BLE001 - kaputte Demo: naechste nehmen
                log(f"[training] {demo.name}: {e}")
                continue
            cache_dir.mkdir(exist_ok=True)
            cache.write_text(json.dumps(known), encoding="utf-8")
        date = match.get("date", "")
        label = f"{date[8:10]}.{date[5:7]}." if len(date) >= 10 else f.stem[:10]
        for sc in known[key].get(mode, []):
            out.append({**sc, "export": f.name, "source": f"{label} R{sc['round']}"})
            if len(out) >= limit:
                break
    return out
