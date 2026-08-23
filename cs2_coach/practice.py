"""Practice Server Config Generator — creates CS2 practice configs from demo data.

Reads kill/death positions from exports and generates:
- Per-map prefire configs with peek positions + view angles from real deaths
- Death-spot practice configs (practice your weakest positions)
- server.cfg for the practice Docker container
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path


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


def _calc_view_angle(from_x: float, from_y: float, from_z: float,
                     to_x: float, to_y: float, to_z: float) -> tuple[float, float]:
    """Calculate CS2 view angles (pitch, yaw) from one point to another."""
    dx = to_x - from_x
    dy = to_y - from_y
    dz = to_z - from_z
    dist_xy = math.sqrt(dx * dx + dy * dy)
    yaw = math.degrees(math.atan2(dy, dx))
    pitch = -math.degrees(math.atan2(dz, dist_xy)) if dist_xy > 0 else 0
    return round(pitch, 1), round(yaw, 1)


def _cluster_positions(positions: list[dict], radius: float = 150.0) -> list[dict]:
    """Cluster nearby positions into hotspots.

    Uses 3D distance for accurate clustering. Tracks both enemy positions
    (bot placement) and player positions (peek points).

    Returns list of clusters sorted by count (descending):
    {x, y, z, peek_x, peek_y, peek_z, count, positions: [...]}
    - x/y/z: average enemy position (where bots go)
    - peek_x/y/z: average player death position (where to peek from)
    """
    if not positions:
        return []

    clusters: list[dict] = []

    for pos in positions:
        ex, ey = pos["ex"], pos["ey"]  # enemy position (bot target)
        ez = pos.get("ez", 0)
        px, py = pos["x"], pos["y"]  # player death position (peek point)
        pz = pos.get("z", 0)
        merged = False
        for c in clusters:
            dx = ex - c["x"]
            dy = ey - c["y"]
            dz = ez - c["z"]
            if (dx * dx + dy * dy + dz * dz) < radius * radius:
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
                "count": 1,
                "positions": [pos],
            })

    clusters.sort(key=lambda c: -c["count"])
    return clusters


def _weapon_to_loadout(weapon: str) -> str:
    """Map weapon name to CS2 give command."""
    # Common weapon mappings
    mapping = {
        "ak47": "weapon_ak47",
        "m4a1": "weapon_m4a1",
        "m4a1_silencer": "weapon_m4a1_silencer",
        "awp": "weapon_awp",
        "deagle": "weapon_deagle",
        "usp_silencer": "weapon_usp_silencer",
        "glock": "weapon_glock",
        "p250": "weapon_p250",
        "famas": "weapon_famas",
        "galil": "weapon_galilar",
        "ssg08": "weapon_ssg08",
        "aug": "weapon_aug",
        "sg556": "weapon_sg556",
        "mac10": "weapon_mac10",
        "mp9": "weapon_mp9",
        "mp5sd": "weapon_mp5sd",
        "ump45": "weapon_ump45",
        "p90": "weapon_p90",
        "hkp2000": "weapon_hkp2000",
        "elite": "weapon_elite",
        "tec9": "weapon_tec9",
        "cz75a": "weapon_cz75a",
        "xm1014": "weapon_xm1014",
        "mag7": "weapon_mag7",
        "negev": "weapon_negev",
        "m249": "weapon_m249",
        "nova": "weapon_nova",
        "sawedoff": "weapon_sawedoff",
        "mp7": "weapon_mp7",
        "bizon": "weapon_bizon",
        "revolver": "weapon_revolver",
        "hegrenade": "weapon_hegrenade",
    }
    return mapping.get(weapon, f"weapon_{weapon}")


def generate_practice_cfg(map_name: str, positions: dict, mode: str = "prefire") -> str:
    """Generate a CS2 practice config for a specific map.

    Two-phase approach:
    1. SETUP: F2 cycles through spots, teleports player to enemy position,
       player looks at ground and presses F3 to place a bot there.
    2. PRACTICE: F4 freezes bots, disables respawn, teleports player to
       first peek position with correct view angle toward the bot.
       F2 now cycles through peek positions (where you typically die).

    mode: "prefire" — bots at enemy positions where you die
          "retake" — bots on site at common post-plant spots
    """
    deaths = positions.get("deaths", [])
    kills = positions.get("kills", [])

    if mode == "prefire":
        clusters = _cluster_positions(deaths, radius=150.0)
    else:
        clusters = _cluster_positions(kills, radius=150.0)

    if not clusters:
        return f"// No data for {map_name}\necho \"Keine Daten fuer {map_name}\"\n"

    max_bots = min(len(clusters), 10)

    lines = [
        f"// ═══════════════════════════════════════════════════",
        f"// CS2 Coach — Practice Config: {map_name}",
        f"// Mode: {'Prefire Training' if mode == 'prefire' else 'Retake Practice'}",
        f"// Generated from {len(deaths)} deaths + {len(kills)} kills",
        f"// {max_bots} Bot-Spots from {len(clusters)} Hotspots",
        f"// ═══════════════════════════════════════════════════",
        "",
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
        "mp_death_drop_gun 0",
        "sv_infinite_ammo 1",
        "ammo_grenade_limit_total 5",
        "sv_showimpacts 1",
        "sv_showimpacts_time 1",
        "mp_respawn_on_death_ct 1",
        "mp_respawn_on_death_t 1",
        "",
        "// ── Clean Slate ──",
        "bot_kick",
        "mp_autoteambalance 0",
        "mp_limitteams 0",
        "",
    ]

    # Build spot info for each cluster
    spots = []
    for i, cluster in enumerate(clusters[:max_bots]):
        # Enemy position (where the bot goes)
        bot_x = round(cluster["x"], 1)
        bot_y = round(cluster["y"], 1)
        bot_z = round(cluster["z"], 1)
        # Peek position (where player typically dies from this enemy spot)
        peek_x = round(cluster["peek_x"], 1)
        peek_y = round(cluster["peek_y"], 1)
        peek_z = round(cluster["peek_z"], 1)
        count = cluster["count"]

        # Calculate view angle from peek position toward bot
        pitch, yaw = _calc_view_angle(peek_x, peek_y, peek_z,
                                       bot_x, bot_y, bot_z)

        wpn_counts: dict[str, int] = defaultdict(int)
        for pos in cluster["positions"]:
            w = pos.get("w", "")
            if w:
                wpn_counts[w] += 1
        cluster_weapon = max(wpn_counts, key=wpn_counts.get) if wpn_counts else "ak47"

        enemy_counts: dict[str, int] = defaultdict(int)
        for pos in cluster["positions"]:
            e = pos.get("e", "")
            if e:
                enemy_counts[e] += 1
        top_enemy = max(enemy_counts, key=enemy_counts.get) if enemy_counts else "?"

        spots.append({
            "bot_x": bot_x, "bot_y": bot_y, "bot_z": bot_z,
            "peek_x": peek_x, "peek_y": peek_y, "peek_z": peek_z,
            "pitch": pitch, "yaw": yaw,
            "count": count,
            "weapon": cluster_weapon, "enemy": top_enemy,
        })

    # ── Phase 1: SETUP aliases — fly to bot spots, place bots ──
    lines.append(f"// ── Phase 1: Bot-Placement ({max_bots} Spots) ──")
    lines.append(f"// F2 = Naechster Spot (teleportiert zum Gegner-Spot)")
    lines.append(f"// F3 = Bot platzieren + weiter")
    lines.append(f"// F4 = Fertig → Practice starten")
    lines.append(f"// F5 = Noclip an/aus")
    lines.append(f"// F6 = Reset (alle Bots kicken, neu starten)")
    lines.append("")

    for i, spot in enumerate(spots):
        next_alias = f"cs2c_s{i+2}" if i < len(spots) - 1 else "cs2c_setup_done"
        lines.append(
            f'alias "cs2c_s{i+1}" '
            f'"setpos {spot["bot_x"]} {spot["bot_y"]} {spot["bot_z"]}; '
            f'bot_add_t; '
            f'echo ; '
            f'echo === Spot {i+1}/{max_bots}: {spot["count"]}x deaths ===; '
            f'echo Gegner: {spot["enemy"]} ({spot["weapon"]}); '
            f'echo Schau auf den Boden und druecke F3; '
            f'alias cs2c_next {next_alias}"'
        )

    lines.append(
        'alias "cs2c_setup_done" '
        '"echo ; echo Alle Spots besucht!; '
        'echo Druecke F4 um Practice zu starten.; '
        'alias cs2c_next cs2c_setup_done"'
    )
    lines.append(
        'alias "cs2c_place" "bot_place; cs2c_next"'
    )
    lines.append("")

    # ── Phase 2: PRACTICE aliases — teleport to peek positions with view angles ──
    lines.append(f"// ── Phase 2: Practice (Peek-Positionen) ──")
    for i, spot in enumerate(spots):
        next_peek = f"cs2c_p{i+2}" if i < len(spots) - 1 else "cs2c_p1"
        lines.append(
            f'alias "cs2c_p{i+1}" '
            f'"setpos {spot["peek_x"]} {spot["peek_y"]} {spot["peek_z"]}; '
            f'setang {spot["pitch"]} {spot["yaw"]} 0; '
            f'echo ; '
            f'echo === Peek {i+1}/{max_bots} ===; '
            f'echo Du stirbst hier {spot["count"]}x - peeke Richtung {spot["enemy"]}; '
            f'echo Waffe: {spot["weapon"]}; '
            f'alias cs2c_peek {next_peek}"'
        )
    lines.append("")

    # F4 = start practice mode
    lines.append(
        f'alias "cs2c_start" '
        f'"bot_stop 1; '
        f'mp_respawn_on_death_t 0; '
        f'noclip; '  # Toggle off (was on from setup)
        f'echo ; '
        f'echo ======================================; '
        f'echo  PRACTICE MODUS AKTIV; '
        f'echo  Bots eingefroren - uebe deine Peeks!; '
        f'echo  F2 = Naechste Peek-Position; '
        f'echo  F6 = Reset; '
        f'echo ======================================; '
        f'echo ; '
        f'bind F2 cs2c_peek; '  # Rebind F2 to peek mode
        f'bind F4 cs2c_unfreeze; '  # Rebind F4 to unfreeze
        f'cs2c_p1"'  # Teleport to first peek position
    )

    # F4 in practice mode = unfreeze bots for live practice
    lines.append(
        'alias "cs2c_unfreeze" '
        '"bot_stop 0; bot_dont_shoot 0; '
        'echo ; echo Bots AKTIV - viel Glueck!; '
        'echo F6 zum Reset"'
    )

    # F6 = full reset
    lines.append(
        'alias "cs2c_reset" '
        '"bot_kick; '
        'mp_respawn_on_death_t 1; '
        'alias cs2c_next cs2c_s1; '
        'alias cs2c_peek cs2c_p1; '
        'bind F2 cs2c_next; '  # Rebind F2 back to setup mode
        'bind F4 cs2c_start; '  # Rebind F4 back to start
        'noclip; '
        'echo ; echo Reset - druecke F2 fuer Spot 1"'
    )
    lines.append("")

    # Start aliases
    lines.append("alias cs2c_next cs2c_s1")
    lines.append("alias cs2c_peek cs2c_p1")
    lines.append("")

    # Key bindings
    lines.append("// ── Keybinds ──")
    lines.append('bind "F2" "cs2c_next"')
    lines.append('bind "F3" "cs2c_place"')
    lines.append('bind "F4" "cs2c_start"')
    lines.append('bind "F5" "noclip"')
    lines.append('bind "F6" "cs2c_reset"')
    lines.append("")

    # Enable noclip for setup phase
    lines.append("// ── Start: Noclip an, druecke F2 fuer Spot 1 ──")
    lines.append("noclip")
    lines.append("")

    # Spot reference list with peek angles
    lines.append("// ── Spot-Uebersicht ──")
    for i, spot in enumerate(spots):
        lines.append(
            f"// {i+1}. Bot({spot['bot_x']}, {spot['bot_y']}, {spot['bot_z']}) "
            f"Peek({spot['peek_x']}, {spot['peek_y']}, {spot['peek_z']}) "
            f"Angle({spot['pitch']}, {spot['yaw']}) "
            f"— {spot['count']}x — {spot['enemy']} ({spot['weapon']})"
        )
    lines.append("")

    # Console output
    lines.extend([
        'echo ""',
        'echo "══════════════════════════════════════"',
        f'echo " CS2 Coach Practice: {map_name}"',
        f'echo " {max_bots} Spots aus deinen Demos"',
        'echo ""',
        'echo " SETUP:"',
        'echo "   F2 = Naechster Bot-Spot (Noclip)"',
        'echo "   F3 = Bot platzieren"',
        'echo "   F4 = Practice starten"',
        'echo ""',
        'echo " PRACTICE:"',
        'echo "   F2 = Naechste Peek-Position"',
        'echo "        (teleportiert + richtet Blick aus)"',
        'echo "   F4 = Bots aktivieren (live)"',
        'echo "   F5 = Noclip"',
        'echo "   F6 = Reset"',
        'echo ""',
        'echo " Druecke F2 um zu starten!"',
        'echo "══════════════════════════════════════"',
        'echo ""',
        "",
    ])

    return "\n".join(lines)


def generate_warmup_cfg(positions: dict[str, dict]) -> str:
    """Generate a general warmup config with stats summary."""
    total_deaths = sum(len(p["deaths"]) for p in positions.values())
    total_kills = sum(len(p["kills"]) for p in positions.values())

    # Find weakest maps (most deaths relative to kills)
    map_ratios = []
    for m, p in positions.items():
        k, d = len(p["kills"]), len(p["deaths"])
        if d > 0:
            map_ratios.append((m, k, d, round(k / max(d, 1), 2)))
    map_ratios.sort(key=lambda x: x[3])

    lines = [
        "// ═══════════════════════════════════════════════════",
        "// CS2 Coach — Warmup Config",
        f"// Total: {total_kills} Kills, {total_deaths} Deaths aus Demos",
        "// ═══════════════════════════════════════════════════",
        "",
        "sv_cheats 1",
        "mp_warmup_end",
        "mp_freezetime 0",
        "mp_roundtime 60",
        "mp_roundtime_defuse 60",
        "mp_buy_anywhere 1",
        "mp_buytime 9999",
        "mp_startmoney 65535",
        "mp_free_armor 1",
        "sv_infinite_ammo 1",
        "sv_showimpacts 1",
        "sv_showimpacts_time 0.5",
        "mp_respawn_on_death_ct 1",
        "mp_respawn_on_death_t 1",
        "",
        "// ── Bot Setup ──",
        "bot_quota 5",
        "bot_difficulty 3",
        "bot_dont_shoot 0",
        "bot_stop 0",
        "",
    ]

    if map_ratios:
        lines.append("// ── Deine schwaechtsten Maps ──")
        for m, k, d, ratio in map_ratios[:3]:
            lines.append(f"// {m}: {k}K / {d}D (K/D {ratio})")
        lines.append("")

    lines.extend([
        "echo \"\"",
        "echo \"══════════════════════════════════════\"",
        "echo \" CS2 Coach — Warmup Mode\"",
        "echo \" Bot Difficulty: Expert\"",
        "echo \" Infinite Ammo + Free Armor\"",
        "echo \"══════════════════════════════════════\"",
        "echo \"\"",
    ])

    return "\n".join(lines)


def _map_to_bspname(map_name: str) -> str:
    """Convert display map name to CS2 BSP name."""
    mapping = {
        "Mirage": "de_mirage",
        "Cache": "de_cache",
        "Dust2": "de_dust2",
        "Inferno": "de_inferno",
        "Nuke": "de_nuke",
        "Ancient": "de_ancient",
        "Anubis": "de_anubis",
        "Vertigo": "de_vertigo",
        "Overpass": "de_overpass",
        "Train": "de_train",
    }
    return mapping.get(map_name, f"de_{map_name.lower()}")


def build_practice_data(cfg: dict) -> dict:
    """Build complete practice data for web UI.

    Returns: {
        has_data: bool,
        maps: [{name, kills, deaths, hotspots, kd, cfg_preview}],
        total_positions: int,
        server_cfg: str,
    }
    """
    positions = _load_positions(cfg)
    if not positions:
        return {"has_data": False}

    maps = []
    total_pos = 0

    for map_name, pos_data in sorted(positions.items(),
                                      key=lambda x: -(len(x[1]["kills"]) + len(x[1]["deaths"]))):
        kills = pos_data["kills"]
        deaths = pos_data["deaths"]
        total = len(kills) + len(deaths)
        total_pos += total

        if total < 5:
            continue

        # Cluster deaths for hotspot count
        clusters = _cluster_positions(deaths, radius=150.0)
        significant_hotspots = [c for c in clusters if c["count"] >= 2]

        # Generate preview of config
        cfg_text = generate_practice_cfg(map_name, pos_data, mode="prefire")

        # Top death weapons (what kills you most on this map)
        death_weapons: dict[str, int] = defaultdict(int)
        for d in deaths:
            w = d.get("w", "")
            if w:
                death_weapons[w] += 1
        top_death_wpns = sorted(death_weapons.items(), key=lambda x: -x[1])[:3]

        # Top enemies on this map
        death_enemies: dict[str, int] = defaultdict(int)
        for d in deaths:
            e = d.get("e", "")
            if e:
                death_enemies[e] += 1
        top_enemies = sorted(death_enemies.items(), key=lambda x: -x[1])[:3]

        maps.append({
            "name": map_name,
            "bsp": _map_to_bspname(map_name),
            "kills": len(kills),
            "deaths": len(deaths),
            "kd": round(len(kills) / max(len(deaths), 1), 2),
            "hotspots": len(significant_hotspots),
            "total_clusters": len(clusters),
            "top_death_weapons": [{"name": w, "count": c} for w, c in top_death_wpns],
            "top_enemies": [{"name": e, "count": c} for e, c in top_enemies],
            "cfg": cfg_text,
        })

    warmup_cfg = generate_warmup_cfg(positions)

    return {
        "has_data": True,
        "maps": maps,
        "total_positions": total_pos,
        "warmup_cfg": warmup_cfg,
    }
