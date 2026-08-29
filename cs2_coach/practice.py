"""Practice Server Config Generator — creates CS2 practice configs from demo data.

Generates per-map configs in 5 modes:
  prefire   — Bots frozen at your death spots, practice peeking
  retake    — Bots at enemy hold positions, retake the site
  spray     — Bots in a line for spray-transfer drills
  challenge — Timed run, bots shoot back, speed-clear all spots
  utility   — Grenade trajectories, smoke/flash practice, no bots

Plus: server.cfg, warmup.cfg, practice planner with training routine.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

# ── Rifles (for spray-transfer relevance check) ──
_RIFLES = frozenset({
    "ak47", "m4a1", "m4a1_silencer", "galil", "famas", "aug", "sg556",
})


# ═══════════════════════════════════════════════════════════════════
#  Data loading & clustering
# ═══════════════════════════════════════════════════════════════════

def _load_positions(cfg: dict) -> dict[str, dict]:
    """Load all kill/death positions grouped by map.

    Returns: {map_name: {"kills": [...], "deaths": [...]}}
    Each position has: x/y/z (player pos), ex/ey/ez (enemy pos), w, hs, r, e
    """
    vault_path = cfg.get("obsidian_vault_path", "")
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    export_dir = Path(vault_path) / sub / "exports" if vault_path else None
    if not export_dir or not export_dir.exists():
        return {}

    by_map: dict[str, dict] = {}

    for f in sorted(export_dir.glob("*_coach.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        map_name = data.get("match", {}).get("map", "?")
        if map_name == "?":
            continue
        if map_name not in by_map:
            by_map[map_name] = {"kills": [], "deaths": []}

        for kp in data.get("kill_positions", []):
            pos = {
                "x": kp.get("x", 0), "y": kp.get("y", 0),
                "z": kp.get("z", 0),
                "ex": kp.get("ex", 0), "ey": kp.get("ey", 0),
                "ez": kp.get("ez", 0),
                "w": kp.get("w", ""), "hs": kp.get("hs", False),
                "r": kp.get("r", 0), "e": kp.get("e", ""),
            }
            if kp.get("t") == "k":
                by_map[map_name]["kills"].append(pos)
            elif kp.get("t") == "d":
                by_map[map_name]["deaths"].append(pos)

    return by_map


def _calc_view_angle(fx: float, fy: float, fz: float,
                     tx: float, ty: float, tz: float) -> tuple[float, float]:
    """Calculate CS2 view angles (pitch, yaw) from one point to another."""
    dx, dy, dz = tx - fx, ty - fy, tz - fz
    dist_xy = math.sqrt(dx * dx + dy * dy)
    yaw = math.degrees(math.atan2(dy, dx))
    pitch = -math.degrees(math.atan2(dz, dist_xy)) if dist_xy > 0 else 0
    return round(pitch, 1), round(yaw, 1)


def _cluster_positions(positions: list[dict], radius: float = 150.0) -> list[dict]:
    """Cluster nearby positions into hotspots (3D).

    Returns sorted list (most deaths first):
      {x, y, z, peek_x, peek_y, peek_z, count, positions: [...]}
    """
    if not positions:
        return []

    clusters: list[dict] = []
    for pos in positions:
        ex, ey, ez = pos["ex"], pos["ey"], pos.get("ez", 0)
        px, py, pz = pos["x"], pos["y"], pos.get("z", 0)
        merged = False
        for c in clusters:
            d2 = (ex - c["x"]) ** 2 + (ey - c["y"]) ** 2 + (ez - c["z"]) ** 2
            if d2 < radius * radius:
                n = c["count"]
                c["x"] = (c["x"] * n + ex) / (n + 1)
                c["y"] = (c["y"] * n + ey) / (n + 1)
                c["z"] = (c["z"] * n + ez) / (n + 1)
                c["peek_x"] = (c["peek_x"] * n + px) / (n + 1)
                c["peek_y"] = (c["peek_y"] * n + py) / (n + 1)
                c["peek_z"] = (c["peek_z"] * n + pz) / (n + 1)
                c["count"] += 1
                c["positions"].append(pos)
                merged = True
                break
        if not merged:
            clusters.append({
                "x": ex, "y": ey, "z": ez,
                "peek_x": px, "peek_y": py, "peek_z": pz,
                "count": 1, "positions": [pos],
            })

    clusters.sort(key=lambda c: -c["count"])
    return clusters


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════

def _weapon_to_loadout(weapon: str) -> str:
    """Map weapon name to CS2 give command."""
    mapping = {
        "ak47": "weapon_ak47", "m4a1": "weapon_m4a1",
        "m4a1_silencer": "weapon_m4a1_silencer", "awp": "weapon_awp",
        "deagle": "weapon_deagle", "usp_silencer": "weapon_usp_silencer",
        "glock": "weapon_glock", "p250": "weapon_p250",
        "famas": "weapon_famas", "galil": "weapon_galilar",
        "ssg08": "weapon_ssg08", "aug": "weapon_aug", "sg556": "weapon_sg556",
        "mac10": "weapon_mac10", "mp9": "weapon_mp9", "mp5sd": "weapon_mp5sd",
        "ump45": "weapon_ump45", "p90": "weapon_p90",
        "hkp2000": "weapon_hkp2000", "elite": "weapon_elite",
        "tec9": "weapon_tec9", "cz75a": "weapon_cz75a",
        "xm1014": "weapon_xm1014", "mag7": "weapon_mag7",
        "negev": "weapon_negev", "m249": "weapon_m249", "nova": "weapon_nova",
        "sawedoff": "weapon_sawedoff", "mp7": "weapon_mp7",
        "bizon": "weapon_bizon", "revolver": "weapon_revolver",
        "hegrenade": "weapon_hegrenade",
    }
    return mapping.get(weapon, f"weapon_{weapon}")


def _map_to_bspname(map_name: str) -> str:
    """Convert display map name to CS2 BSP name."""
    mapping = {
        "Mirage": "de_mirage", "Cache": "de_cache", "Dust2": "de_dust2",
        "Inferno": "de_inferno", "Nuke": "de_nuke", "Ancient": "de_ancient",
        "Anubis": "de_anubis", "Vertigo": "de_vertigo",
        "Overpass": "de_overpass", "Train": "de_train",
    }
    return mapping.get(map_name, f"de_{map_name.lower()}")


def _build_spots(clusters: list[dict], max_bots: int) -> list[dict]:
    """Build enriched spot data from clusters."""
    spots = []
    for cluster in clusters[:max_bots]:
        bx = round(cluster["x"], 1)
        by_ = round(cluster["y"], 1)
        bz = round(cluster["z"], 1)
        px = round(cluster["peek_x"], 1)
        py = round(cluster["peek_y"], 1)
        pz = round(cluster["peek_z"], 1)
        cnt = cluster["count"]

        pitch, yaw = _calc_view_angle(px, py, pz, bx, by_, bz)

        wpn: dict[str, int] = defaultdict(int)
        enemy: dict[str, int] = defaultdict(int)
        for p in cluster["positions"]:
            if p.get("w"):
                wpn[p["w"]] += 1
            if p.get("e"):
                enemy[p["e"]] += 1

        hs_n = sum(1 for p in cluster["positions"] if p.get("hs"))

        spots.append({
            "bot_x": bx, "bot_y": by_, "bot_z": bz,
            "peek_x": px, "peek_y": py, "peek_z": pz,
            "pitch": pitch, "yaw": yaw, "count": cnt,
            "weapon": max(wpn, key=wpn.get) if wpn else "ak47",
            "enemy": max(enemy, key=enemy.get) if enemy else "?",
            "hs_count": hs_n,
            "hs_pct": round(hs_n / max(cnt, 1) * 100),
        })
    return spots


# ── Shared cfg blocks ──

def _cfg_server_block(roundtime: int = 60, respawn_ct: int = 0,
                      respawn_t: int = 0) -> list[str]:
    """Common server setup lines used by every mode."""
    return [
        "sv_cheats 1",
        "mp_warmup_end",
        "mp_freezetime 0",
        f"mp_roundtime {roundtime}",
        f"mp_roundtime_defuse {roundtime}",
        "mp_buy_anywhere 1",
        "mp_buytime 9999",
        "mp_maxmoney 65535",
        "mp_startmoney 65535",
        "mp_free_armor 1",
        "mp_death_drop_gun 0",
        "sv_infinite_ammo 1",
        "ammo_grenade_limit_total 5",
        "sv_showimpacts 1",
        "sv_showimpacts_time 0.8",
        f"mp_respawn_on_death_ct {respawn_ct}",
        f"mp_respawn_on_death_t {respawn_t}",
        "mp_autoteambalance 0",
        "mp_limitteams 0",
        "mp_solid_teammates 0",
        "sv_talk_enemy_dead 1",
        "sv_talk_enemy_living 1",
        "sv_deadtalk 1",
        "mp_ct_default_secondary weapon_usp_silencer",
        "mp_t_default_secondary weapon_glock",
    ]


def _cfg_difficulty_aliases() -> list[str]:
    return [
        'alias "cs2c_easy" "bot_difficulty 0; echo Schwierigkeit: LEICHT"',
        'alias "cs2c_medium" "bot_difficulty 1; echo Schwierigkeit: MITTEL"',
        'alias "cs2c_hard" "bot_difficulty 2; echo Schwierigkeit: SCHWER"',
        'alias "cs2c_expert" "bot_difficulty 3; echo Schwierigkeit: EXPERTE"',
    ]


def _cfg_autoplace_chain(spots: list[dict], done_extra: str = "") -> list[str]:
    """Build alias chain for automatic bot placement at spots."""
    n = len(spots)
    L: list[str] = []
    for i, s in enumerate(spots):
        nxt = f"cs2c_ab{i + 2}" if i < n - 1 else "cs2c_setup_done"
        L.append(
            f'alias "cs2c_ab{i + 1}" '
            f'"setpos {s["bot_x"]} {s["bot_y"]} {s["bot_z"]}; '
            f'bot_place; '
            f'echo [Bot {i + 1}/{n}] {s["count"]}x — '
            f'{s["enemy"]} ({s["weapon"]}); '
            f'{nxt}"'
        )

    done_cmd = (
        f'"noclip; bot_stop 1; bot_dont_shoot 1; '
        f'setpos {spots[0]["peek_x"]} {spots[0]["peek_y"]} {spots[0]["peek_z"]}; '
        f'setang {spots[0]["pitch"]} {spots[0]["yaw"]} 0; '
        f'bind F2 cs2c_peek; '
        f'{done_extra}'
        f'echo ; echo ══════ SETUP FERTIG ══════; '
        f'echo F2=Naechster Peek  F3=Bots toggle"'
    )
    L.append(f'alias "cs2c_setup_done" {done_cmd}')
    L.append('alias "cs2c_setup" "noclip; cs2c_ab1"')
    return L


def _cfg_peek_aliases(spots: list[dict]) -> list[str]:
    """Build peek position cycling aliases."""
    n = len(spots)
    L: list[str] = []
    for i, s in enumerate(spots):
        nxt = f"cs2c_p{i + 2}" if i < n - 1 else "cs2c_p1"
        L.append(
            f'alias "cs2c_p{i + 1}" '
            f'"setpos {s["peek_x"]} {s["peek_y"]} {s["peek_z"]}; '
            f'setang {s["pitch"]} {s["yaw"]} 0; '
            f'echo ; '
            f'echo ══ Peek {i + 1}/{n}: {s["count"]}x deaths ══; '
            f'echo Gegner: {s["enemy"]} mit {s["weapon"]}; '
            f'alias cs2c_peek {nxt}"'
        )
    L.append("alias cs2c_peek cs2c_p1")
    return L


def _cfg_bot_toggle() -> list[str]:
    return [
        'alias "cs2c_freeze" '
        '"bot_stop 1; bot_dont_shoot 1; '
        'echo Bots EINGEFROREN; alias cs2c_toggle cs2c_unfreeze"',
        'alias "cs2c_unfreeze" '
        '"bot_stop 0; bot_dont_shoot 0; '
        'echo Bots AKTIV — viel Glueck!; alias cs2c_toggle cs2c_freeze"',
        'alias "cs2c_toggle" "cs2c_unfreeze"',
    ]


# ═══════════════════════════════════════════════════════════════════
#  Mode generators
# ═══════════════════════════════════════════════════════════════════

def _apply_overrides(spots: list[dict], clusters: list[dict],
                     overrides: dict | None) -> tuple[list[dict], int, int]:
    """Apply AI overrides to spot list. Returns (spots, bot_difficulty, max_bots)."""
    ov = overrides or {}
    difficulty = ov.get("bot_difficulty", 3)
    max_bots = ov.get("bot_count", len(spots))
    max_bots = min(max_bots, len(clusters), 10)

    # Re-order spots if AI provided priority list (1-indexed spot numbers)
    prio = ov.get("priority_spots")
    if prio and len(prio) > 0:
        indexed = {i + 1: s for i, s in enumerate(spots)}
        reordered = []
        for idx in prio:
            if idx in indexed:
                reordered.append(indexed.pop(idx))
        # Append remaining spots not in the priority list
        for s in spots:
            if s not in reordered:
                reordered.append(s)
        spots = reordered

    spots = spots[:max_bots]
    return spots, difficulty, max_bots


def generate_practice_cfg(map_name: str, positions: dict,
                          mode: str = "prefire",
                          overrides: dict | None = None) -> str:
    """Prefire mode — bots frozen at death spots, practice your peeks."""
    deaths = positions.get("deaths", [])
    kills = positions.get("kills", [])
    clusters = _cluster_positions(deaths, radius=150.0)
    if not clusters:
        return f'// No data for {map_name}\necho "Keine Daten fuer {map_name}"\n'

    nb = min(len(clusters), 10)
    spots = _build_spots(clusters, nb)
    spots, difficulty, nb = _apply_overrides(spots, clusters, overrides)
    mn = map_name.lower()

    L: list[str] = [
        f"// {'=' * 55}",
        f"// CS2 Coach — {map_name} PREFIRE (Auto-Setup)",
        f"// {nb} Bots aus {len(deaths)} Deaths + {len(kills)} Kills",
        f"// {'=' * 55}", "",
        "// ── Server Setup ──",
        *_cfg_server_block(), "",
        "// ── Bots ──",
        "bot_kick", "bot_quota 0", f"bot_difficulty {difficulty}",
        "bot_dont_shoot 1", "",
    ]
    for _ in range(nb):
        L.append("bot_add_t")
    L += ["", "bot_stop 1", "",
          f"// ── Auto-Setup: {nb} Spots ──",
          *_cfg_autoplace_chain(spots), "",
          f"// ── Peek-Positionen ──",
          *_cfg_peek_aliases(spots), "",
          "// ── Bot-Kontrolle ──",
          *_cfg_bot_toggle(), "",
          "// ── Schwierigkeit ──",
          *_cfg_difficulty_aliases(), "",
          f'alias "cs2c_reset" "exec coach/practice_{mn}"', "",
          "// ── Keybinds ──",
          'bind "F2" "cs2c_setup"',
          'bind "F3" "cs2c_toggle"',
          'bind "F4" "noclip"',
          'bind "F5" "cs2c_reset"', "",
          "mp_restartgame 1", "",
          ]

    # Console output
    L += [
        'echo ""',
        'echo "══════════════════════════════════════"',
        f'echo " PREFIRE: {map_name} — {nb} Bots"',
        'echo " F2=Auto-Setup → Peek-Zyklus"',
        'echo " F3=Bots toggle  F4=Noclip  F5=Reset"',
        'echo "══════════════════════════════════════"',
        'echo ""', "",
    ]
    return "\n".join(L)


def generate_retake_cfg(map_name: str, positions: dict,
                        overrides: dict | None = None) -> str:
    """Retake mode — bots at enemy hold positions (from your kill data).

    Uses kill positions: where enemies stood when you killed them = common
    hold positions. You approach from your own kill position (entry point).
    """
    kills = positions.get("kills", [])
    deaths = positions.get("deaths", [])
    if not kills:
        return f'// No kill data for {map_name}\necho "Keine Kill-Daten fuer {map_name}"\n'

    # For retake: cluster enemy positions from player's kills
    # Here "ex/ey/ez" = where the enemy was (bot position)
    # "x/y/z" = where the player was (entry position)
    clusters = _cluster_positions(kills, radius=150.0)
    if not clusters:
        return f'// Not enough data for {map_name}\n'

    nb = min(len(clusters), 8)
    spots = _build_spots(clusters, nb)
    spots, difficulty, nb = _apply_overrides(spots, clusters, overrides)
    mn = map_name.lower()

    L: list[str] = [
        f"// {'=' * 55}",
        f"// CS2 Coach — {map_name} RETAKE",
        f"// {nb} Bots an gegnerischen Hold-Positionen",
        f"// {'=' * 55}", "",
        "// ── Server Setup ──",
        *_cfg_server_block(), "",
        "// ── Bots (CT-Seite: verteidigen Positionen) ──",
        "bot_kick", "bot_quota 0", f"bot_difficulty {difficulty}",
        "bot_dont_shoot 0",  # Retake: bots shoot back
        "",
    ]
    for _ in range(nb):
        L.append("bot_add_ct")
    L += ["", "bot_stop 1", "",
          f"// ── Auto-Setup: {nb} Hold-Positionen ──",
          *_cfg_autoplace_chain(spots, done_extra="bot_stop 0; bot_dont_shoot 0; "), "",
          f"// ── Peek-Positionen (deine Entry-Points) ──",
          *_cfg_peek_aliases(spots), "",
          "// ── Bot-Kontrolle ──",
          *_cfg_bot_toggle(), "",
          "// ── Schwierigkeit ──",
          *_cfg_difficulty_aliases(), "",
          f'alias "cs2c_reset" "exec coach/retake_{mn}"', "",
          "// ── Keybinds ──",
          'bind "F2" "cs2c_setup"',
          'bind "F3" "cs2c_toggle"',
          'bind "F4" "noclip"',
          'bind "F5" "cs2c_reset"', "",
          "mp_restartgame 1", "",
          ]

    L += [
        'echo ""',
        'echo "══════════════════════════════════════"',
        f'echo " RETAKE: {map_name} — {nb} Bots"',
        'echo " Bots halten Positionen — retake!"',
        'echo " F2=Setup  F3=Toggle  F4=Noclip  F5=Reset"',
        'echo "══════════════════════════════════════"',
        'echo ""', "",
    ]
    return "\n".join(L)


def generate_spray_cfg(map_name: str, positions: dict,
                       overrides: dict | None = None) -> str:
    """Spray-Transfer mode — bots lined up for spray-transfer drills.

    Takes the biggest death cluster, calculates a perpendicular line,
    places bots along it at even spacing for spray transfer practice.
    """
    ov = overrides or {}
    deaths = positions.get("deaths", [])
    clusters = _cluster_positions(deaths, radius=200.0)
    if not clusters:
        return f'// No data for {map_name}\n'

    # Use the biggest cluster as reference
    c = clusters[0]
    bx, by_, bz = c["x"], c["y"], c["z"]
    px, py, pz = c["peek_x"], c["peek_y"], c["peek_z"]

    # Direction from peek to bot
    dx, dy = bx - px, by_ - py
    dist = math.sqrt(dx * dx + dy * dy)
    if dist < 1:
        return f'// Insufficient distance data for {map_name}\n'

    # Normalize and get perpendicular
    ndx, ndy = dx / dist, dy / dist
    perp_x, perp_y = -ndy, ndx  # 90° rotation

    spacing = ov.get("spray_spacing", 100.0)
    n_bots = min(ov.get("bot_count", 5), 10)
    spots = []
    for i in range(n_bots):
        offset = (i - n_bots // 2) * spacing
        sx = round(bx + perp_x * offset, 1)
        sy = round(by_ + perp_y * offset, 1)
        pitch, yaw = _calc_view_angle(px, py, pz, sx, sy, bz)
        spots.append({
            "bot_x": sx, "bot_y": sy, "bot_z": round(bz, 1),
            "peek_x": round(px, 1), "peek_y": round(py, 1), "peek_z": round(pz, 1),
            "pitch": pitch, "yaw": yaw,
            "count": c["count"], "weapon": "ak47", "enemy": "Target",
            "hs_count": 0, "hs_pct": 0,
        })

    mn = map_name.lower()
    center_pitch, center_yaw = _calc_view_angle(px, py, pz, bx, by_, bz)

    L: list[str] = [
        f"// {'=' * 55}",
        f"// CS2 Coach — {map_name} SPRAY-TRANSFER",
        f"// {n_bots} Bots in Reihe — Spray-Kontrolle ueben",
        f"// {'=' * 55}", "",
        "// ── Server Setup ──",
        *_cfg_server_block(respawn_ct=1, respawn_t=1), "",
        "// ── Bots (eingefroren in Reihe) ──",
        "bot_kick", "bot_quota 0", "bot_difficulty 0",
        "bot_dont_shoot 1", "",
    ]
    for _ in range(n_bots):
        L.append("bot_add_t")
    L += ["", "bot_stop 1", ""]

    # Auto-place chain
    L.append(f"// ── Auto-Setup: {n_bots} Bots in Reihe ──")
    for i, s in enumerate(spots):
        nxt = f"cs2c_ab{i + 2}" if i < n_bots - 1 else "cs2c_spray_done"
        L.append(
            f'alias "cs2c_ab{i + 1}" '
            f'"setpos {s["bot_x"]} {s["bot_y"]} {s["bot_z"]}; '
            f'bot_place; echo [Bot {i + 1}/{n_bots}]; {nxt}"'
        )
    L.append(
        f'alias "cs2c_spray_done" '
        f'"noclip; bot_stop 1; bot_dont_shoot 1; '
        f'setpos {round(px, 1)} {round(py, 1)} {round(pz, 1)}; '
        f'setang {center_pitch} {center_yaw} 0; '
        f'echo ; echo ══════ SPRAY DRILL BEREIT ══════; '
        f'echo Ziel auf den linken Bot — Spray nach rechts transferieren"'
    )
    L.append('alias "cs2c_setup" "noclip; cs2c_ab1"')
    L += ["",
          "// ── Zurueck zur Schussposition ──",
          f'alias "cs2c_origin" '
          f'"setpos {round(px, 1)} {round(py, 1)} {round(pz, 1)}; '
          f'setang {center_pitch} {center_yaw} 0; '
          f'echo Zurueck zur Startposition"', "",
          f'alias "cs2c_reset" "exec coach/spray_{mn}"', "",
          "// ── Keybinds ──",
          'bind "F2" "cs2c_setup"   // Auto-Setup',
          'bind "F3" "cs2c_origin"  // Zurueck zur Startposition',
          'bind "F4" "noclip"',
          'bind "F5" "cs2c_reset"', "",
          "mp_restartgame 1", "",
          ]

    L += [
        'echo ""',
        'echo "══════════════════════════════════════"',
        f'echo " SPRAY-TRANSFER: {map_name}"',
        f'echo " {n_bots} Bots in Reihe"',
        'echo " F2=Setup  F3=Startposition  F5=Reset"',
        'echo ""',
        'echo " Tipp: Links anfangen, nach rechts"',
        'echo " transferieren. Burst-Kontrolle!"',
        'echo "══════════════════════════════════════"',
        'echo ""', "",
    ]
    return "\n".join(L)


def generate_challenge_cfg(map_name: str, positions: dict,
                           overrides: dict | None = None) -> str:
    """Challenge mode — timed prefire run, bots shoot back.

    Same bot positions as prefire, but:
    - Short round timer (configurable, default 45s)
    - Bots active (shooting, configurable difficulty)
    - Respawn disabled — if you die, round lost
    - Goal: clear all bots as fast as possible
    """
    deaths = positions.get("deaths", [])
    kills = positions.get("kills", [])
    clusters = _cluster_positions(deaths, radius=150.0)
    if not clusters:
        return f'// No data for {map_name}\n'

    nb = min(len(clusters), 8)  # Fewer bots for challenge
    spots = _build_spots(clusters, nb)
    spots, difficulty, nb = _apply_overrides(spots, clusters, overrides)
    challenge_time = (overrides or {}).get("challenge_time", 45)
    mn = map_name.lower()

    L: list[str] = [
        f"// {'=' * 55}",
        f"// CS2 Coach — {map_name} CHALLENGE (Speed-Run)",
        f"// {nb} Bots — {challenge_time} Sekunden — schaff sie alle!",
        f"// {'=' * 55}", "",
        "// ── Server Setup (kurzer Timer!) ──",
        *_cfg_server_block(roundtime=challenge_time), "",
        "// ── Bots (aktiv, Expert) ──",
        "bot_kick", "bot_quota 0",
        f"bot_difficulty {difficulty}",
        "bot_dont_shoot 0",  # Bots shoot back!
        "",
    ]
    for _ in range(nb):
        L.append("bot_add_t")
    L += ["", "bot_stop 1", ""]  # Frozen until setup

    # Auto-place chain — after setup, UNFREEZE bots
    L.append(f"// ── Auto-Setup: {nb} Spots + UNFREEZE ──")
    for i, s in enumerate(spots):
        nxt = f"cs2c_ab{i + 2}" if i < nb - 1 else "cs2c_challenge_go"
        L.append(
            f'alias "cs2c_ab{i + 1}" '
            f'"setpos {s["bot_x"]} {s["bot_y"]} {s["bot_z"]}; '
            f'bot_place; '
            f'echo [Bot {i + 1}/{nb}]; {nxt}"'
        )

    # After placement: unfreeze, teleport to first peek, start!
    L.append(
        f'alias "cs2c_challenge_go" '
        f'"noclip; '
        f'setpos {spots[0]["peek_x"]} {spots[0]["peek_y"]} {spots[0]["peek_z"]}; '
        f'setang {spots[0]["pitch"]} {spots[0]["yaw"]} 0; '
        f'bot_stop 0; bot_dont_shoot 0; '
        f'mp_restartgame 1; '
        f'echo ; '
        f'echo ══════ CHALLENGE START ══════; '
        f'echo {nb} Bots — {challenge_time} Sekunden — GO GO GO!; '
        f'bind F2 cs2c_peek"'
    )
    L.append('alias "cs2c_setup" "noclip; cs2c_ab1"')
    L += ["",
          f"// ── Peek-Positionen (Hilfe) ──",
          *_cfg_peek_aliases(spots), "",
          "// ── Schwierigkeit ──",
          *_cfg_difficulty_aliases(), "",
          f'alias "cs2c_reset" "exec coach/challenge_{mn}"', "",
          "// ── Keybinds ──",
          'bind "F2" "cs2c_setup"',
          'bind "F4" "noclip"',
          'bind "F5" "cs2c_reset"', "",
          "mp_restartgame 1", "",
          ]

    L += [
        'echo ""',
        'echo "══════════════════════════════════════"',
        f'echo " CHALLENGE: {map_name} — {nb} Bots"',
        f'echo " {challenge_time} Sekunden — Bots schiessen zurueck!"',
        'echo " F2=Setup+Start  F5=Reset"',
        'echo ""',
        'echo " Schwierigkeit: cs2c_easy..cs2c_expert"',
        'echo "══════════════════════════════════════"',
        'echo ""', "",
    ]
    return "\n".join(L)


def generate_utility_cfg() -> str:
    """Utility practice mode — grenade trajectories, no bots."""
    L: list[str] = [
        f"// {'=' * 55}",
        "// CS2 Coach — UTILITY PRACTICE",
        "// Smoke, Flash, Molotov, HE — mit Trajectories",
        f"// {'=' * 55}", "",
        "// ── Server Setup ──",
        "sv_cheats 1",
        "mp_warmup_end",
        "mp_freezetime 0",
        "mp_roundtime 60",
        "mp_roundtime_defuse 60",
        "mp_buy_anywhere 1",
        "mp_buytime 9999",
        "mp_maxmoney 65535",
        "mp_startmoney 65535",
        "mp_free_armor 1",
        "sv_infinite_ammo 1",
        "mp_autoteambalance 0",
        "mp_limitteams 0",
        "mp_respawn_on_death_ct 1",
        "mp_respawn_on_death_t 1", "",
        "// ── Granaten-Einstellungen ──",
        "ammo_grenade_limit_total 5",
        "ammo_grenade_limit_flashbang 2",
        "sv_grenade_trajectory_prac_pipreview 1",
        "sv_grenade_trajectory_prac_trailtime 8",
        "cl_sim_grenade_trajectory 1",
        "sv_rethrow_last_grenade 1", "",
        "// ── Bots entfernen ──",
        "bot_kick",
        "bot_quota 0", "",
        "// ── Granaten-Binds ──",
        '// F1-F4: Schnell Granaten kaufen',
        'alias "cs2c_smoke" "buy weapon_smokegrenade; echo Smoke gekauft"',
        'alias "cs2c_flash" "buy weapon_flashbang; echo Flash gekauft"',
        'alias "cs2c_molly" "buy weapon_molotov; buy weapon_incgrenade; echo Molotov gekauft"',
        'alias "cs2c_he" "buy weapon_hegrenade; echo HE gekauft"', "",
        "// ── Rethrow (letzte Granate wiederholen) ──",
        '// Wichtig: sv_rethrow_last_grenade muss 1 sein',
        'alias "cs2c_rethrow" "sv_rethrow_last_grenade 1; echo Rethrow aktiv — wirf die Granate nochmal!"', "",
        "// ── Keybinds ──",
        'bind "F1" "cs2c_smoke"',
        'bind "F2" "cs2c_flash"',
        'bind "F3" "cs2c_molly"',
        'bind "F4" "cs2c_he"',
        'bind "F5" "noclip"', "",
        "mp_restartgame 1", "",
        'echo ""',
        'echo "══════════════════════════════════════"',
        'echo " UTILITY PRACTICE"',
        'echo " Granaten-Trajectories aktiv!"',
        'echo ""',
        'echo " F1=Smoke  F2=Flash  F3=Molly  F4=HE"',
        'echo " F5=Noclip"',
        'echo ""',
        'echo " Rethrow: sv_rethrow_last_grenade"',
        'echo " Preview: cl_sim_grenade_trajectory"',
        'echo "══════════════════════════════════════"',
        'echo ""', "",
    ]
    return "\n".join(L)


# ═══════════════════════════════════════════════════════════════════
#  Server & Warmup configs
# ═══════════════════════════════════════════════════════════════════

def generate_server_cfg() -> str:
    """Comprehensive practice server.cfg for Docker / LAN."""
    return "\n".join([
        "// ═══════════════════════════════════════════════════",
        "// CS2 Coach — Practice Server Configuration",
        "// ═══════════════════════════════════════════════════", "",
        'hostname "CS2 Coach — Practice Server"',
        'sv_password ""',
        "sv_lan 1", "sv_region 3", "",
        "game_type 0", "game_mode 1", "",
        "sv_cheats 1",
        "mp_warmuptime 3", "mp_freezetime 0",
        "mp_roundtime 60", "mp_roundtime_defuse 60",
        "mp_maxrounds 100", "mp_overtime_enable 0",
        "mp_buy_anywhere 1", "mp_buytime 9999",
        "mp_maxmoney 65535", "mp_startmoney 65535",
        "mp_free_armor 1", "mp_death_drop_gun 0", "",
        "sv_infinite_ammo 1",
        "ammo_grenade_limit_total 5", "ammo_grenade_limit_flashbang 2",
        "sv_grenade_trajectory_prac_pipreview 1",
        "sv_grenade_trajectory_prac_trailtime 4", "",
        "sv_showimpacts 1", "sv_showimpacts_time 0.8", "",
        "bot_quota 0", "bot_quota_mode normal",
        "bot_difficulty 3", "mp_autoteambalance 0", "mp_limitteams 0", "",
        "sv_maxrate 0", "sv_minrate 786432", "",
        "sv_talk_enemy_dead 1", "sv_talk_enemy_living 1",
        "sv_deadtalk 1", "sv_alltalk 1", "",
        "mp_respawn_on_death_ct 0", "mp_respawn_on_death_t 0",
        "mp_solid_teammates 0", "",
        "log on", "sv_logbans 1", "sv_logecho 1", "sv_logfile 1", "",
        'echo " CS2 Coach Practice Server — bereit!"',
        'echo " Configs: exec coach/practice"',
    ])


def generate_warmup_cfg(positions: dict[str, dict]) -> str:
    """Warmup config with moving bots."""
    total_d = sum(len(p["deaths"]) for p in positions.values())
    total_k = sum(len(p["kills"]) for p in positions.values())

    map_ratios = []
    for m, p in positions.items():
        k, d = len(p["kills"]), len(p["deaths"])
        if d > 0:
            map_ratios.append((m, k, d, round(k / max(d, 1), 2)))
    map_ratios.sort(key=lambda x: x[3])

    L = [
        "// CS2 Coach — Warmup",
        f"// {total_k} Kills, {total_d} Deaths aus Demos", "",
        *_cfg_server_block(respawn_ct=1, respawn_t=1), "",
        "bot_quota 5", "bot_difficulty 3", "bot_dont_shoot 0", "bot_stop 0", "",
        *_cfg_difficulty_aliases(), "",
        'bind "F4" "noclip"', "",
    ]
    if map_ratios:
        L.append("// Schwaechtste Maps:")
        for m, k, d, r in map_ratios[:3]:
            L.append(f"// {m}: {k}K/{d}D (K/D {r})")
        L.append("")

    L += [
        'echo " CS2 Coach Warmup — Expert Bots"',
        'echo " cs2c_easy / cs2c_expert fuer Schwierigkeit"',
    ]
    return "\n".join(L)


# ═══════════════════════════════════════════════════════════════════
#  Practice planner
# ═══════════════════════════════════════════════════════════════════

def _build_practice_planner(maps_data: list[dict],
                            positions: dict[str, dict]) -> dict:
    """Analyze demo data and generate a practice plan + recommendations."""
    recommendations: list[dict] = []

    # 1. Weak maps (low K/D)
    for m in sorted(maps_data, key=lambda x: x["kd"]):
        if m["kd"] < 1.0 and m["deaths"] >= 10:
            recommendations.append({
                "type": "map", "priority": "high",
                "icon": "crosshair",
                "title": f'{m["name"]} — K/D {m["kd"]}',
                "desc": (f'{m["deaths"]} Deaths bei {m["kills"]} Kills. '
                         f'Prefire-Training auf dieser Map priorisieren.'),
                "cmd": f'exec coach/practice_{m["name"].lower()}',
                "mode": "prefire",
            })
            if len([r for r in recommendations if r["type"] == "map"]) >= 2:
                break

    # 2. High HS death rate → crosshair placement
    for m in maps_data:
        if m["hs_death_pct"] >= 45 and m["deaths"] >= 10:
            recommendations.append({
                "type": "crosshair", "priority": "high",
                "icon": "target",
                "title": f'{m["name"]} — {m["hs_death_pct"]}% HS-Deaths',
                "desc": ("Du wirst ueberdurchschnittlich oft per Headshot getoetet. "
                         "Challenge-Modus trainiert Reaktion unter Druck."),
                "cmd": f'exec coach/challenge_{m["name"].lower()}',
                "mode": "challenge",
            })
            break

    # 3. Repeated deaths at same spots
    for m in maps_data:
        if m["spots"] and m["spots"][0]["count"] >= 5:
            s = m["spots"][0]
            recommendations.append({
                "type": "spot", "priority": "medium",
                "icon": "alert-triangle",
                "title": f'{m["name"]} — {s["count"]}x am selben Spot',
                "desc": (f'Du stirbst wiederholt durch {s["enemy"]} '
                         f'({s["weapon"]}). Diesen Winkel gezielt ueben.'),
                "cmd": f'exec coach/practice_{m["name"].lower()}',
                "mode": "prefire",
            })
            break

    # 4. Rifle deaths → spray transfer
    total_deaths = sum(m["deaths"] for m in maps_data)
    rifle_deaths = 0
    for pos_data in positions.values():
        for d in pos_data.get("deaths", []):
            if d.get("w", "") in _RIFLES:
                rifle_deaths += 1
    if total_deaths > 0 and rifle_deaths / total_deaths > 0.35:
        best_map = maps_data[0]["name"] if maps_data else "Mirage"
        recommendations.append({
            "type": "spray", "priority": "medium",
            "icon": "zap",
            "title": f'Spray-Transfer — {round(rifle_deaths / total_deaths * 100)}% Rifle-Deaths',
            "desc": ("Ein Grossteil deiner Deaths kommt durch Rifles. "
                     "Spray-Transfer-Drill verbessert deine Kontrolle."),
            "cmd": f'exec coach/spray_{best_map.lower()}',
            "mode": "spray",
        })

    # 5. Always recommend utility practice
    recommendations.append({
        "type": "utility", "priority": "low",
        "icon": "flame",
        "title": "Utility-Practice",
        "desc": ("Smokes, Flashes, Mollys mit Trajectory-Preview ueben. "
                 "Jede Session 5 Minuten Granaten einplanen."),
        "cmd": "exec coach/utility",
        "mode": "utility",
    })

    # Practice routine
    routine: list[dict] = []
    if maps_data:
        worst = min(maps_data, key=lambda m: m["kd"])
        second = sorted(maps_data, key=lambda m: m["kd"])[1] if len(maps_data) > 1 else worst
        routine = [
            {"step": 1, "duration": "5 Min", "activity": "Warmup",
             "desc": "Aim aufwaermen mit beweglichen Bots",
             "cmd": "exec coach/warmup", "mode": "warmup"},
            {"step": 2, "duration": "10 Min", "activity": f"Prefire — {worst['name']}",
             "desc": f"Schwaechtste Map (K/D {worst['kd']}), Peeks ueben",
             "cmd": f"exec coach/practice_{worst['name'].lower()}", "mode": "prefire"},
            {"step": 3, "duration": "5 Min", "activity": f"Spray — {worst['name']}",
             "desc": "Spray-Transfer auf 5 Bots in Reihe",
             "cmd": f"exec coach/spray_{worst['name'].lower()}", "mode": "spray"},
            {"step": 4, "duration": "10 Min", "activity": f"Challenge — {second['name']}",
             "desc": f"Speed-Run: {second['name']} in 45 Sekunden clearen",
             "cmd": f"exec coach/challenge_{second['name'].lower()}", "mode": "challenge"},
            {"step": 5, "duration": "5 Min", "activity": "Utility",
             "desc": "Smokes + Flashes mit Trajectory-Preview",
             "cmd": "exec coach/utility", "mode": "utility"},
        ]

    return {
        "recommendations": sorted(
            recommendations, key=lambda r: {"high": 0, "medium": 1, "low": 2}[r["priority"]]
        ),
        "routine": routine,
        "total_time": "35 Min",
    }


# ═══════════════════════════════════════════════════════════════════
#  Main data builder
# ═══════════════════════════════════════════════════════════════════

def build_practice_data(cfg: dict) -> dict:
    """Build complete practice data for web UI — all 5 modes + planner."""
    positions = _load_positions(cfg)
    if not positions:
        return {"has_data": False}

    maps = []
    total_pos = 0

    for map_name, pos_data in sorted(
        positions.items(),
        key=lambda x: -(len(x[1]["kills"]) + len(x[1]["deaths"]))
    ):
        kills, deaths = pos_data["kills"], pos_data["deaths"]
        total = len(kills) + len(deaths)
        total_pos += total
        if total < 5:
            continue

        clusters = _cluster_positions(deaths, radius=150.0)
        significant = [c for c in clusters if c["count"] >= 2]
        spots = _build_spots(clusters, min(len(clusters), 10))

        # Death weapons
        dw: dict[str, int] = defaultdict(int)
        for d in deaths:
            if d.get("w"):
                dw[d["w"]] += 1
        top_wpns = sorted(dw.items(), key=lambda x: -x[1])[:5]

        # Death enemies
        de: dict[str, int] = defaultdict(int)
        for d in deaths:
            if d.get("e"):
                de[d["e"]] += 1
        top_enemies = sorted(de.items(), key=lambda x: -x[1])[:5]

        hs_deaths = sum(1 for d in deaths if d.get("hs"))

        # Generate all mode configs
        cfg_prefire = generate_practice_cfg(map_name, pos_data)
        cfg_retake = generate_retake_cfg(map_name, pos_data)
        cfg_spray = generate_spray_cfg(map_name, pos_data)
        cfg_challenge = generate_challenge_cfg(map_name, pos_data)

        maps.append({
            "name": map_name,
            "bsp": _map_to_bspname(map_name),
            "kills": len(kills), "deaths": len(deaths),
            "kd": round(len(kills) / max(len(deaths), 1), 2),
            "hotspots": len(significant),
            "total_clusters": len(clusters),
            "hs_death_pct": round(hs_deaths / max(len(deaths), 1) * 100),
            "spots": spots,
            "top_death_weapons": [{"name": w, "count": c} for w, c in top_wpns],
            "top_enemies": [{"name": e, "count": c} for e, c in top_enemies],
            "cfg_prefire": cfg_prefire,
            "cfg_retake": cfg_retake,
            "cfg_spray": cfg_spray,
            "cfg_challenge": cfg_challenge,
        })

    total_deaths = sum(m["deaths"] for m in maps)
    total_kills = sum(m["kills"] for m in maps)
    warmup_cfg = generate_warmup_cfg(positions)
    server_cfg = generate_server_cfg()
    utility_cfg = generate_utility_cfg()
    planner = _build_practice_planner(maps, positions)

    return {
        "has_data": True,
        "maps": maps,
        "total_positions": total_pos,
        "total_deaths": total_deaths,
        "total_kills": total_kills,
        "warmup_cfg": warmup_cfg,
        "server_cfg": server_cfg,
        "utility_cfg": utility_cfg,
        "planner": planner,
    }
