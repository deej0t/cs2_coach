"""Warum du stirbst: jeder Tod aus den Demos, eingeordnet.

Bisher misst der Coach vor allem, wie gut du schiesst (Fadenkreuz, Counter-
Strafing, Reaktion) - Entscheidungen misst er nicht. Hier pro Tod:
- traded: hat ein Mitspieler deinen Killer innerhalb TRADE_SECONDS erwischt?
- mate_dist: Abstand zum naechsten lebenden Mitspieler im Moment des Todes
- opening: erster Tod der Runde ueberhaupt
- second: Rundensekunde (ab Freeze-Ende)
- won: hat dein Team die Runde trotzdem gewonnen?
- nades: Granaten, die du beim Tod noch hattest ("mit ins Grab", ohne Decoy)

Ausgewertet wird aus der Demo (demoparser2), je Demo zwischengespeichert in
death_cache/ neben der Config - wie die Trainings-Szenarien.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

TICKRATE = 64
TRADE_SECONDS = 5
CACHE_DIR = "death_cache"
CACHE_KEY = "deaths-v2"  # v2: nades = Granaten im Inventar beim Tod (12.11.)


def _event(p, name: str, columns: list[str]):
    import pandas as pd
    df = p.parse_event(name)
    return df if hasattr(df, "iterrows") else pd.DataFrame(columns=columns)


_NADES = ("grenade", "molotov", "incendiary", "flashbang")


def _is_nade(name: str) -> bool:
    n = name.lower()
    return any(k in n for k in _NADES) and "decoy" not in n


def scan_demo(demo_path, me: str, parser_factory=None) -> list[dict]:
    if parser_factory is None:
        from demoparser2 import DemoParser as parser_factory
    p = parser_factory(str(demo_path))
    deaths = _event(p, "player_death", ["tick", "user_steamid", "attacker_steamid"])
    freeze = sorted(int(t) for t in _event(p, "round_freeze_end", ["tick"])["tick"])
    ends = _event(p, "round_end", ["tick", "winner"])
    ends = sorted((int(r["tick"]), str(r["winner"])) for _, r in ends.iterrows())
    deaths = deaths.sort_values("tick")
    mine = deaths[deaths["user_steamid"].astype(str) == me]
    if mine.empty:
        return []
    ticks = sorted({int(t) for t in mine["tick"]})
    rows = p.parse_ticks(["X", "Y", "Z", "team_num", "is_alive"], ticks=ticks)
    by_tick = {int(t): g for t, g in rows.groupby("tick")}
    # Inventar einen Tick vor dem Tod (im Todestick ist es schon geleert)
    inv = p.parse_ticks(["inventory"], ticks=[t - 1 for t in ticks])
    inv = inv[inv["steamid"].astype(str) == me]
    nades_at = {int(r["tick"]) + 1: sum(1 for w in (r["inventory"] if r["inventory"] is not None else [])
                                         if _is_nade(str(w))) for _, r in inv.iterrows()}
    out = []
    for _, d in mine.iterrows():
        t = int(d["tick"])
        killer = str(d.get("attacker_steamid") or "")
        if not killer or killer == me or killer in ("0", "nan", "None"):
            continue  # Selbstmord, Welt, Fallschaden
        g = by_tick.get(t)
        if g is None:
            continue
        me_row = g[g["steamid"].astype(str) == me]
        if me_row.empty:
            continue
        me_row = me_row.iloc[0]
        team = int(me_row["team_num"])
        killer_row = g[g["steamid"].astype(str) == killer]
        if not killer_row.empty and int(killer_row.iloc[0]["team_num"]) == team:
            continue  # Teamkill
        start = max([f for f in freeze if f <= t], default=0)
        end_tick, winner = next(((e, w) for e, w in ends if e >= t), (None, ""))
        mates = g[(g["team_num"] == team) & (g["steamid"].astype(str) != me) & (g["is_alive"].astype(bool))]
        dist = min((math.dist((float(r["X"]), float(r["Y"])), (float(me_row["X"]), float(me_row["Y"])))
                    for _, r in mates.iterrows()), default=None)
        window = deaths[(deaths["tick"] > t) & (deaths["tick"] <= t + TRADE_SECONDS * TICKRATE)]
        traded = bool(((window["user_steamid"].astype(str) == killer)).any())
        first = deaths[(deaths["tick"] >= start) & (deaths["tick"] <= t)]
        won = {"CT": team == 3, "T": team == 2, "3": team == 3, "2": team == 2}.get(winner.upper())
        out.append({"tick": t, "second": round((t - start) / TICKRATE, 1), "traded": traded,
                    "mate_dist": round(dist) if dist is not None else None, "alone": not len(mates),
                    "opening": len(first) == 1, "won": won, "side": "CT" if team == 3 else "T",
                    "nades": nades_at.get(t)})
    return out


def collect(cfg: dict, config_path: Path, parser_factory=None, log=print, max_new: int = 200) -> list[dict]:
    """Alle Tode aller eigenen Matches mit vorhandener Demo; je Match Datum, Map, Ergebnis."""
    vault = (cfg.get("obsidian_vault_path") or "").strip()
    folder = (cfg.get("demo_folder") or "").strip()
    me = str(cfg.get("steam_id") or "")
    if not vault or not folder or not me:
        return []
    cache_dir = Path(config_path).parent / CACHE_DIR
    out, scanned = [], 0
    for f in sorted((Path(vault) / cfg.get("coach_subfolder", "CS2-Coach") / "exports").glob("*_coach.json")):
        try:
            match = json.loads(f.read_text(encoding="utf-8")).get("match", {})
        except (OSError, ValueError):
            continue
        demo = Path(folder) / (match.get("demo_file") or "")
        if not match.get("demo_file"):
            continue
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
            except Exception as e:  # noqa: BLE001 - kaputte Demo: naechste
                log(f"[deaths] {demo.name}: {e}")
                continue
            cache_dir.mkdir(exist_ok=True)
            cache.write_text(json.dumps(known), encoding="utf-8")
        for d in known[CACHE_KEY]:
            out.append({**d, "export": f.name, "map": match.get("map"), "result": match.get("result")})
    return out


def summary(deaths: list[dict]) -> dict:
    """Anteile ueber alle Tode, je Map und Sieg/Niederlage, plus Vergleich mit evidence.py."""
    from .evidence import compare_rates

    def rates(ds):
        n = len(ds)
        return {"n": n, "traded": sum(d["traded"] for d in ds), "opening": sum(d["opening"] for d in ds),
                "far": sum(1 for d in ds if d["mate_dist"] is None or d["mate_dist"] > 1000)}

    wins = [d for d in deaths if d.get("result") == "Sieg"]
    losses = [d for d in deaths if d.get("result") == "Niederlage"]
    rw, rl = rates(wins), rates(losses)
    by_map = {}
    for d in deaths:
        by_map.setdefault(d.get("map") or "?", []).append(d)
    return {"all": rates(deaths), "wins": rw, "losses": rl,
            "maps": {m: rates(ds) for m, ds in sorted(by_map.items(), key=lambda kv: -len(kv[1]))},
            "traded_cmp": compare_rates(rw["traded"], rw["n"], rl["traded"], rl["n"]),
            "opening_cmp": compare_rates(rw["opening"], rw["n"], rl["opening"], rl["n"]),
            "far_cmp": compare_rates(rw["far"], rw["n"], rl["far"], rl["n"])}
