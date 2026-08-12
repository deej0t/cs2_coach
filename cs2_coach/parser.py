"""Demo-Parsing mit demoparser2 – extrahiert alle relevanten CS2-Metriken."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from demoparser2 import DemoParser


@dataclass
class PlayerStats:
    name: str
    steam_id: str
    team: str
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    headshots: int = 0
    damage: int = 0
    rounds_played: int = 0
    # Opening Duels
    opening_kills: int = 0
    opening_deaths: int = 0
    # Trade Kills
    trade_kills: int = 0
    # Waffen
    awp_kills: int = 0
    rifle_kills: int = 0
    pistol_kills: int = 0
    # Counter-Strafing (bullet_damage-basiert)
    shots_accurate: int = 0  # inaccuracy_move == 0
    shots_moving: int = 0    # inaccuracy_move > 0
    total_shots_hit: int = 0
    avg_inaccuracy_move: float = 0.0
    # Spray Control
    burst_kills: int = 0     # recoil_index <= 3
    spray_kills: int = 0     # recoil_index > 6
    avg_recoil_index: float = 0.0
    # Engagement Distance
    avg_fight_distance: float = 0.0
    close_range_kills: int = 0   # < 400 units
    mid_range_kills: int = 0     # 400-900
    long_range_kills: int = 0    # > 900
    # Utility
    flashes_thrown: int = 0
    smokes_thrown: int = 0
    he_thrown: int = 0
    molotovs_thrown: int = 0
    flash_enemies_blinded: int = 0
    flash_teammates_blinded: int = 0
    flash_avg_enemy_duration: float = 0.0
    # Clutches
    clutch_wins: int = 0
    clutch_attempts: int = 0
    clutch_kills: int = 0
    # Kill Context
    kills_thru_smoke: int = 0
    kills_while_blind: int = 0
    kills_noscope: int = 0
    kills_penetrated: int = 0
    kills_airborne: int = 0
    # Bomb
    bomb_plants: int = 0
    bomb_defuses: int = 0
    # Rank
    rank_old: int = 0
    rank_new: int = 0
    rank_change: float = 0.0
    rank_wins: int = 0
    # KAST & Survival
    kast_rounds: int = 0          # Rounds with Kill/Assist/Survived/Traded
    rounds_survived: int = 0
    # Multi-Kills
    rounds_2k: int = 0
    rounds_3k: int = 0
    rounds_4k: int = 0
    rounds_5k: int = 0
    # CT/T Side Split
    ct_kills: int = 0
    ct_deaths: int = 0
    t_kills: int = 0
    t_deaths: int = 0
    # Accuracy
    shots_fired: int = 0
    # Death Timing
    deaths_early: int = 0         # first third of round
    deaths_mid: int = 0           # middle third
    deaths_late: int = 0          # final third
    # Crosshair Placement
    crosshair_placement_sum: float = 0.0   # sum of angular errors in degrees
    crosshair_placement_kills: int = 0     # number of kills analyzed
    crosshair_placement_excellent: int = 0 # < 5°
    crosshair_placement_good: int = 0      # 5-15°
    crosshair_placement_average: int = 0   # 15-30°
    crosshair_placement_poor: int = 0      # > 30°

    @property
    def kd_ratio(self) -> float:
        return self.kills / max(self.deaths, 1)

    @property
    def adr(self) -> float:
        return self.damage / max(self.rounds_played, 1)

    @property
    def headshot_pct(self) -> float:
        return (self.headshots / max(self.kills, 1)) * 100

    @property
    def opening_duel_rating(self) -> str:
        total = self.opening_kills + self.opening_deaths
        if total == 0:
            return "Keine Daten"
        ratio = self.opening_kills / total
        if ratio >= 0.6:
            return "Stark"
        if ratio >= 0.45:
            return "Solide"
        return "Schwach"

    @property
    def counter_strafe_score(self) -> float:
        total = self.shots_accurate + self.shots_moving
        if total == 0:
            return 100.0
        return (self.shots_accurate / total) * 100

    @property
    def awp_rifle_split(self) -> str:
        total = self.awp_kills + self.rifle_kills
        if total == 0:
            return "Keine Daten"
        awp_pct = (self.awp_kills / total) * 100
        return f"AWP {awp_pct:.0f}% / Rifle {100 - awp_pct:.0f}%"

    @property
    def utility_per_round(self) -> float:
        total = self.flashes_thrown + self.smokes_thrown + self.he_thrown + self.molotovs_thrown
        return total / max(self.rounds_played, 1)

    @property
    def flash_effectiveness(self) -> str:
        total = self.flash_enemies_blinded + self.flash_teammates_blinded
        if total == 0:
            return "Keine Daten"
        enemy_pct = (self.flash_enemies_blinded / total) * 100
        return f"{enemy_pct:.0f}% Gegner / {100 - enemy_pct:.0f}% Team"

    @property
    def burst_spray_ratio(self) -> str:
        total = self.burst_kills + self.spray_kills
        if total == 0:
            return "Keine Daten"
        burst_pct = (self.burst_kills / total) * 100
        return f"Burst {burst_pct:.0f}% / Spray {100 - burst_pct:.0f}%"

    @property
    def clutch_rate(self) -> str:
        if self.clutch_attempts == 0:
            return "Keine Daten"
        pct = (self.clutch_wins / self.clutch_attempts) * 100
        return f"{self.clutch_wins}/{self.clutch_attempts} ({pct:.0f}%)"

    @property
    def kast_pct(self) -> float:
        return (self.kast_rounds / max(self.rounds_played, 1)) * 100

    @property
    def survival_rate(self) -> float:
        return (self.rounds_survived / max(self.rounds_played, 1)) * 100

    @property
    def accuracy(self) -> float:
        if self.shots_fired == 0:
            return 0.0
        return (self.total_shots_hit / self.shots_fired) * 100

    @property
    def multikill_str(self) -> str:
        parts = []
        if self.rounds_2k:
            parts.append(f"2k:{self.rounds_2k}")
        if self.rounds_3k:
            parts.append(f"3k:{self.rounds_3k}")
        if self.rounds_4k:
            parts.append(f"4k:{self.rounds_4k}")
        if self.rounds_5k:
            parts.append(f"5k:{self.rounds_5k}")
        return " ".join(parts) if parts else "Keine"

    @property
    def ct_t_split(self) -> str:
        return (
            f"CT {self.ct_kills}K/{self.ct_deaths}D | "
            f"T {self.t_kills}K/{self.t_deaths}D"
        )

    @property
    def crosshair_placement_avg(self) -> float:
        """Average crosshair placement error in degrees (lower = better)."""
        if self.crosshair_placement_kills == 0:
            return 0.0
        return self.crosshair_placement_sum / self.crosshair_placement_kills

    @property
    def crosshair_placement_rating(self) -> str:
        avg = self.crosshair_placement_avg
        if self.crosshair_placement_kills == 0:
            return "Keine Daten"
        if avg < 5:
            return "Exzellent"
        if avg < 15:
            return "Gut"
        if avg < 30:
            return "Durchschnittlich"
        return "Schwach"

    @property
    def crosshair_placement_str(self) -> str:
        if self.crosshair_placement_kills == 0:
            return "Keine Daten"
        avg = self.crosshair_placement_avg
        return (
            f"{avg:.1f}° avg ({self.crosshair_placement_excellent}× <5° | "
            f"{self.crosshair_placement_good}× 5-15° | "
            f"{self.crosshair_placement_average}× 15-30° | "
            f"{self.crosshair_placement_poor}× >30°)"
        )

    @property
    def death_timing_str(self) -> str:
        total = self.deaths_early + self.deaths_mid + self.deaths_late
        if total == 0:
            return "Keine Daten"
        return (
            f"Early {self.deaths_early} | "
            f"Mid {self.deaths_mid} | "
            f"Late {self.deaths_late}"
        )


@dataclass
class MatchResult:
    map_name: str
    score_team1: int
    score_team2: int
    player_stats: PlayerStats
    all_players: list[PlayerStats] = field(default_factory=list)
    kill_positions: list[dict] = field(default_factory=list)  # kill/death XY positions for map viz
    duel_matrix: list[dict] = field(default_factory=list)  # per-opponent duel breakdown
    round_timeline: list[dict] = field(default_factory=list)  # per-round event log
    economy_performance: dict = field(default_factory=dict)  # eco/force/fullbuy stats
    demo_path: str = ""
    match_date: str = ""  # YYYY-MM-DD from demo file mtime
    match_datetime: str = ""  # YYYY-MM-DD HH:MM

    @property
    def result_str(self) -> str:
        if self.score_team1 > self.score_team2:
            return "Sieg"
        if self.score_team1 < self.score_team2:
            return "Niederlage"
        return "Unentschieden"

    @property
    def total_rounds(self) -> int:
        return self.score_team1 + self.score_team2

    @property
    def rating(self) -> float:
        s = self.player_stats
        impact = (s.kills * 1.0 + s.assists * 0.3) / max(s.rounds_played, 1)
        survival = 1 - (s.deaths / max(s.rounds_played, 1))
        adr_norm = min(s.adr / 100.0, 1.5)
        opening_bonus = (s.opening_kills - s.opening_deaths) * 0.05
        clutch_bonus = s.clutch_wins * 0.03
        util_bonus = min(s.utility_per_round * 0.02, 0.06)
        return round(
            0.28 * impact + 0.22 * survival + 0.32 * adr_norm
            + 0.08 + opening_bonus + clutch_bonus + util_bonus,
            2,
        )


# -- Konstanten --

TRADE_WINDOW_TICKS = 3 * 64  # ~3 Sekunden (Pro-Standard)
INACCURACY_MOVE_THRESHOLD = 0.005  # Engine-Wert ab dem Bewegung zählt

AWP_WEAPONS = {"weapon_awp", "awp"}
RIFLE_WEAPONS = {
    "weapon_ak47", "ak47", "weapon_m4a1", "m4a1",
    "weapon_m4a1_silencer", "m4a1_silencer",
    "weapon_aug", "aug", "weapon_sg556", "sg556",
    "weapon_famas", "famas", "weapon_galilar", "galilar",
}
PISTOL_WEAPONS = {
    "weapon_glock", "glock", "weapon_usp_silencer", "usp_silencer",
    "weapon_p250", "p250", "weapon_fiveseven", "fiveseven",
    "weapon_tec9", "tec9", "weapon_deagle", "deagle",
    "weapon_cz75a", "cz75a", "weapon_elite", "elite",
    "weapon_revolver", "revolver",
}


def _get_event_df(parser: DemoParser, event_name: str):
    """Holt ein einzelnes Event als DataFrame."""
    results = parser.parse_events([event_name])
    if results and len(results) > 0:
        _, df = results[0]
        return df
    return None


def _get_enriched_event_df(parser: DemoParser, event_name: str, player_props: list[str]):
    """Holt ein Event mit per-Tick Spieler-Props (Position, Blickwinkel etc.)."""
    try:
        df = parser.parse_event(event_name, player=player_props)
        if df is not None and len(df) > 0:
            return df
    except Exception:
        pass
    return None


def parse_demo(demo_path: str, player_name: str = "", steam_id: str = "") -> MatchResult:
    path = Path(demo_path)
    if not path.exists():
        raise FileNotFoundError(f"Demo-Datei nicht gefunden: {demo_path}")

    # Match-Datum aus Datei-Modifikationszeit
    mtime = os.path.getmtime(path)
    match_dt = datetime.fromtimestamp(mtime)
    match_date = match_dt.strftime("%Y-%m-%d")
    match_datetime = match_dt.strftime("%Y-%m-%d %H:%M")

    parser = DemoParser(str(path))
    header = parser.parse_header()
    map_name = header.get("map_name", "unknown")

    # Spieler-Info
    player_info = parser.parse_player_info()
    player_lookup: dict[str, PlayerStats] = {}
    target_steam_id = steam_id

    for _, row in player_info.iterrows():
        sid = str(row.get("steamid", ""))
        name = str(row.get("name", ""))
        team = str(row.get("team_number", ""))
        if sid:
            player_lookup[sid] = PlayerStats(name=name, steam_id=sid, team=team)
            if not target_steam_id and player_name and player_name.lower() in name.lower():
                target_steam_id = sid

    # Events parsen
    kills_df = _get_event_df(parser, "player_death")
    rounds_df = _get_event_df(parser, "round_end")
    hurt_df = _get_event_df(parser, "player_hurt")
    bullet_df = _get_event_df(parser, "bullet_damage")
    blind_df = _get_event_df(parser, "player_blind")
    rank_df = _get_event_df(parser, "rank_update")
    bomb_plant_df = _get_event_df(parser, "bomb_planted")
    bomb_defuse_df = _get_event_df(parser, "bomb_defused")
    round_freeze_df = _get_event_df(parser, "round_freeze_end")

    total_rounds = len(rounds_df) if rounds_df is not None else 0
    round_ticks = sorted(rounds_df["tick"].tolist()) if rounds_df is not None else []
    freeze_ticks = sorted(round_freeze_df["tick"].tolist()) if round_freeze_df is not None else []

    # Determine actual starting side per player from kill events
    _assign_starting_sides(parser, player_lookup, round_ticks)

    # Team-Zugehörigkeit
    team_by_player: dict[str, str] = {sid: s.team for sid, s in player_lookup.items()}

    # 1) Kills verarbeiten
    round_first_kill: dict[int, bool] = {}
    if kills_df is not None:
        _process_kills(kills_df, round_ticks, player_lookup, round_first_kill)

    # 2) Damage
    if hurt_df is not None:
        for _, dmg in hurt_df.iterrows():
            attacker_id = str(dmg.get("attacker_steamid", ""))
            amount = int(dmg.get("dmg_health", 0))
            if attacker_id in player_lookup:
                player_lookup[attacker_id].damage += amount

    # 3) Counter-Strafing + Spray + Distance (bullet_damage)
    if bullet_df is not None:
        _process_bullet_damage(bullet_df, player_lookup)

    # 4) Utility-Usage
    _process_utility(parser, player_lookup)

    # 5) Flash-Effektivität
    if blind_df is not None:
        _process_flashes(blind_df, player_lookup, team_by_player)

    # 6) Clutches
    if kills_df is not None and rounds_df is not None:
        _process_clutches(kills_df, rounds_df, freeze_ticks, player_lookup, team_by_player)

    # 7) Bomb-Stats
    if bomb_plant_df is not None:
        for _, row in bomb_plant_df.iterrows():
            sid = str(row.get("user_steamid", ""))
            if sid in player_lookup:
                player_lookup[sid].bomb_plants += 1

    if bomb_defuse_df is not None:
        for _, row in bomb_defuse_df.iterrows():
            sid = str(row.get("user_steamid", ""))
            if sid in player_lookup:
                player_lookup[sid].bomb_defuses += 1

    # 8) Rank
    if rank_df is not None:
        for _, row in rank_df.iterrows():
            sid = str(row.get("user_steamid", ""))
            if sid in player_lookup:
                player_lookup[sid].rank_old = int(row.get("rank_old", 0))
                player_lookup[sid].rank_new = int(row.get("rank_new", 0))
                player_lookup[sid].rank_change = float(row.get("rank_change", 0))
                player_lookup[sid].rank_wins = int(row.get("num_wins", 0))

    # 9) Accuracy (shots fired)
    shots_df = _get_event_df(parser, "weapon_fire")
    if shots_df is not None:
        for _, row in shots_df.iterrows():
            sid = str(row.get("user_steamid", ""))
            if sid in player_lookup:
                player_lookup[sid].shots_fired += 1

    # 10) Crosshair Placement
    _process_crosshair_placement(parser, player_lookup)

    # 10b) Kill Positions for 2D Map
    kill_positions = _extract_kill_positions(parser, player_lookup)

    # 11) Per-Round-Analyse: KAST, Survival, Multi-Kills, CT/T-Split, Death-Timing
    if kills_df is not None and rounds_df is not None:
        _process_per_round(
            kills_df, rounds_df, freeze_ticks,
            player_lookup, team_by_player,
        )

    # Runden setzen
    for stats in player_lookup.values():
        stats.rounds_played = max(total_rounds, 1)

    # Zielspieler
    if not target_steam_id and player_lookup:
        target_steam_id = next(iter(player_lookup))

    target_stats = player_lookup.get(target_steam_id, PlayerStats(
        name=player_name or "Unknown", steam_id=target_steam_id or "", team="",
    ))
    if target_stats.rounds_played == 0:
        target_stats.rounds_played = max(total_rounds, 1)

    target_team = target_stats.team
    score_t1, score_t2 = _extract_scores(rounds_df, target_team)

    # 12) Duel Matrix
    duel_matrix = _build_duel_matrix(kills_df, round_ticks, player_lookup, target_steam_id)

    # 13) Round Timeline
    round_timeline = _build_round_timeline(
        kills_df, bomb_plant_df, bomb_defuse_df, rounds_df,
        freeze_ticks, round_ticks, player_lookup, target_steam_id, target_team,
    )

    # 14) Economy Performance
    economy_performance = _process_economy(
        parser, kills_df, hurt_df, rounds_df,
        freeze_ticks, round_ticks, target_steam_id, target_team, player_lookup,
    )

    return MatchResult(
        map_name=_clean_map_name(map_name),
        score_team1=score_t1,
        score_team2=score_t2,
        player_stats=target_stats,
        all_players=list(player_lookup.values()),
        kill_positions=kill_positions,
        duel_matrix=duel_matrix,
        round_timeline=round_timeline,
        economy_performance=economy_performance,
        demo_path=demo_path,
        match_date=match_date,
        match_datetime=match_datetime,
    )


# -- Starting Side Detection --

# In-game team_num values from kill events (NOT parse_player_info team_number)
_INGAME_T = 2
_INGAME_CT = 3


def _assign_starting_sides(parser: DemoParser, player_lookup: dict[str, PlayerStats], round_ticks: list[int]):
    """Determine each player's starting side from kill event team_num data.

    parse_player_info().team_number is a roster grouping that does NOT correspond
    to T/CT. Instead, we use parse_event('player_death', player=['team_num'])
    which gives the actual in-game team_num (2=T, 3=CT) at the time of each kill.
    We look at first-half kills to determine starting side.
    """
    try:
        enriched_kills = parser.parse_event("player_death", player=["team_num"])
    except Exception:
        return

    if enriched_kills is None or len(enriched_kills) == 0:
        return

    # Half boundary tick: use round 12 end tick if available
    half_tick = round_ticks[_MR12_HALF - 1] if len(round_ticks) >= _MR12_HALF else float("inf")

    # Collect first-half team_num for each player (as attacker or victim)
    side_votes: dict[str, int] = {}  # steamid -> team_num seen in first half

    for _, row in enriched_kills.iterrows():
        tick = int(row.get("tick", 0))
        if tick > half_tick:
            break  # Only look at first half

        for role, col in [("attacker", "attacker_team_num"), ("victim", "user_team_num")]:
            sid_col = "attacker_steamid" if role == "attacker" else "user_steamid"
            sid = str(row.get(sid_col, ""))
            team_num = row.get(col)
            if sid in player_lookup and team_num is not None and not (isinstance(team_num, float) and math.isnan(team_num)):
                team_num = int(team_num)
                if team_num in (_INGAME_T, _INGAME_CT):
                    side_votes[sid] = team_num

    # Assign starting side
    for sid, team_num in side_votes.items():
        if sid in player_lookup:
            player_lookup[sid].team = "CT" if team_num == _INGAME_CT else "T"

    # Players with no kills/deaths in first half: infer from teammates
    # Group roster teams from parse_player_info (original team field stored before overwrite)
    # Use already-assigned sides to propagate
    roster_groups: dict[str, list[str]] = {}
    for sid, stats in player_lookup.items():
        # Original roster team was overwritten, but players on same team share the same
        # team_num in kills. We can use the assigned sides to fill gaps.
        pass

    # Second pass: players without kills might share a side with assigned teammates
    # Use kill events to find teammates (same team_num in same round)
    assigned = {sid for sid in side_votes if sid in player_lookup}
    unassigned = {sid for sid in player_lookup if sid not in assigned}
    if unassigned:
        # Find co-occurrence: if two players have same team_num in same kill event
        for _, row in enriched_kills.iterrows():
            att_sid = str(row.get("attacker_steamid", ""))
            vic_sid = str(row.get("user_steamid", ""))
            att_team = row.get("attacker_team_num")
            vic_team = row.get("user_team_num")
            if att_sid in assigned and vic_sid in unassigned:
                if att_team is not None and vic_team is not None:
                    att_t = int(att_team) if not (isinstance(att_team, float) and math.isnan(att_team)) else None
                    vic_t = int(vic_team) if not (isinstance(vic_team, float) and math.isnan(vic_team)) else None
                    if att_t and vic_t:
                        # If victim is on same team as attacker -> same side, else opposite
                        if vic_t == att_t:
                            player_lookup[vic_sid].team = player_lookup[att_sid].team
                        else:
                            player_lookup[vic_sid].team = "T" if player_lookup[att_sid].team == "CT" else "CT"
                        unassigned.discard(vic_sid)
            elif vic_sid in assigned and att_sid in unassigned:
                if att_team is not None and vic_team is not None:
                    att_t = int(att_team) if not (isinstance(att_team, float) and math.isnan(att_team)) else None
                    vic_t = int(vic_team) if not (isinstance(vic_team, float) and math.isnan(vic_team)) else None
                    if att_t and vic_t:
                        if att_t == vic_t:
                            player_lookup[att_sid].team = player_lookup[vic_sid].team
                        else:
                            player_lookup[att_sid].team = "T" if player_lookup[vic_sid].team == "CT" else "CT"
                        unassigned.discard(att_sid)


# -- Kill-Processing --

def _process_kills(kills_df, round_ticks, player_lookup, round_first_kill):
    for _, kill in kills_df.iterrows():
        attacker_id = str(kill.get("attacker_steamid", ""))
        victim_id = str(kill.get("user_steamid", ""))
        weapon = str(kill.get("weapon", "")).lower()
        headshot = bool(kill.get("headshot", False))
        tick = int(kill.get("tick", 0))
        thru_smoke = bool(kill.get("thrusmoke", False))
        attacker_blind = bool(kill.get("attackerblind", False))
        noscope = bool(kill.get("noscope", False))
        penetrated = int(kill.get("penetrated", 0))
        attacker_air = bool(kill.get("attackerinair", False))

        round_num = _tick_to_round(tick, round_ticks)

        if attacker_id in player_lookup:
            stats = player_lookup[attacker_id]
            stats.kills += 1
            if headshot:
                stats.headshots += 1
            if weapon in AWP_WEAPONS:
                stats.awp_kills += 1
            elif weapon in RIFLE_WEAPONS:
                stats.rifle_kills += 1
            elif weapon in PISTOL_WEAPONS:
                stats.pistol_kills += 1
            # Kill Context
            if thru_smoke:
                stats.kills_thru_smoke += 1
            if attacker_blind:
                stats.kills_while_blind += 1
            if noscope:
                stats.kills_noscope += 1
            if penetrated > 0:
                stats.kills_penetrated += 1
            if attacker_air:
                stats.kills_airborne += 1

            # Opening Duels
            if round_num not in round_first_kill:
                round_first_kill[round_num] = True
                stats.opening_kills += 1

        if victim_id in player_lookup:
            player_lookup[victim_id].deaths += 1
            if round_num not in round_first_kill:
                round_first_kill[round_num] = True
                player_lookup[victim_id].opening_deaths += 1

    # Assists
    for _, kill in kills_df.iterrows():
        assister_id = str(kill.get("assister_steamid", ""))
        if assister_id and assister_id != "0" and assister_id in player_lookup:
            player_lookup[assister_id].assists += 1

    # Trade Kills
    _analyze_trades(kills_df, player_lookup)


# -- bullet_damage: Counter-Strafing, Spray, Distance --

def _process_bullet_damage(bullet_df, player_lookup):
    inaccuracy_sums: dict[str, list[float]] = {}
    recoil_sums: dict[str, list[float]] = {}
    distance_sums: dict[str, list[float]] = {}

    for _, row in bullet_df.iterrows():
        attacker_id = str(row.get("attacker_steamid", ""))
        if attacker_id not in player_lookup:
            continue

        stats = player_lookup[attacker_id]
        inacc_move = float(row.get("inaccuracy_move", 0))
        recoil = float(row.get("recoil_index", 0))
        distance = float(row.get("distance", 0))

        stats.total_shots_hit += 1

        # Counter-Strafing
        if inacc_move <= INACCURACY_MOVE_THRESHOLD:
            stats.shots_accurate += 1
        else:
            stats.shots_moving += 1
        inaccuracy_sums.setdefault(attacker_id, []).append(inacc_move)

        # Spray Control
        recoil_sums.setdefault(attacker_id, []).append(recoil)
        if recoil <= 3.0:
            stats.burst_kills += 1
        elif recoil > 6.0:
            stats.spray_kills += 1

        # Distance
        distance_sums.setdefault(attacker_id, []).append(distance)
        if distance < 400:
            stats.close_range_kills += 1
        elif distance <= 900:
            stats.mid_range_kills += 1
        else:
            stats.long_range_kills += 1

    # Durchschnitte berechnen
    for sid, stats in player_lookup.items():
        if sid in inaccuracy_sums and inaccuracy_sums[sid]:
            stats.avg_inaccuracy_move = sum(inaccuracy_sums[sid]) / len(inaccuracy_sums[sid])
        if sid in recoil_sums and recoil_sums[sid]:
            stats.avg_recoil_index = sum(recoil_sums[sid]) / len(recoil_sums[sid])
        if sid in distance_sums and distance_sums[sid]:
            stats.avg_fight_distance = sum(distance_sums[sid]) / len(distance_sums[sid])


# -- Utility --

def _process_utility(parser, player_lookup):
    event_map = {
        "flashbang_detonate": "flashes_thrown",
        "smokegrenade_detonate": "smokes_thrown",
        "hegrenade_detonate": "he_thrown",
        "inferno_startburn": "molotovs_thrown",
    }
    for event_name, attr in event_map.items():
        df = _get_event_df(parser, event_name)
        if df is None:
            continue
        for _, row in df.iterrows():
            sid = str(row.get("user_steamid", ""))
            if sid in player_lookup:
                setattr(player_lookup[sid], attr, getattr(player_lookup[sid], attr) + 1)


# -- Flash-Effektivität --

def _process_flashes(blind_df, player_lookup, team_by_player):
    enemy_durations: dict[str, list[float]] = {}

    for _, row in blind_df.iterrows():
        attacker_id = str(row.get("attacker_steamid", ""))
        victim_id = str(row.get("user_steamid", ""))
        duration = float(row.get("blind_duration", 0))

        if attacker_id not in player_lookup:
            continue
        if victim_id == attacker_id:
            continue  # Self-flash ignorieren

        attacker_team = team_by_player.get(attacker_id, "")
        victim_team = team_by_player.get(victim_id, "")

        if attacker_team and victim_team:
            if attacker_team != victim_team:
                player_lookup[attacker_id].flash_enemies_blinded += 1
                enemy_durations.setdefault(attacker_id, []).append(duration)
            else:
                player_lookup[attacker_id].flash_teammates_blinded += 1

    for sid, durations in enemy_durations.items():
        if durations:
            player_lookup[sid].flash_avg_enemy_duration = sum(durations) / len(durations)


# -- Clutch-Detection --

def _process_clutches(kills_df, rounds_df, freeze_ticks, player_lookup, team_by_player):
    round_ticks = sorted(rounds_df["tick"].tolist())
    kills_sorted = kills_df.sort_values("tick").reset_index(drop=True)

    # Runden-Grenzen: [(start_tick, end_tick), ...]
    round_ranges = []
    for i, end_tick in enumerate(round_ticks):
        start = freeze_ticks[i] if i < len(freeze_ticks) else (round_ticks[i - 1] if i > 0 else 0)
        round_ranges.append((start, end_tick))

    for round_idx, (start, end) in enumerate(round_ranges):
        round_kills = kills_sorted[
            (kills_sorted["tick"] >= start) & (kills_sorted["tick"] <= end)
        ].to_dict("records")

        if not round_kills:
            continue

        # Tracker: wer ist noch alive?
        alive: dict[str, set[str]] = {}
        for sid, stats in player_lookup.items():
            team = team_by_player.get(sid, "")
            alive.setdefault(team, set()).add(sid)

        alive_tracker = {t: set(players) for t, players in alive.items()}
        clutch_player = None
        clutch_started_at_kills = 0

        for kill in round_kills:
            victim_id = str(kill.get("user_steamid", ""))
            victim_team = team_by_player.get(victim_id, "")

            if victim_team in alive_tracker:
                alive_tracker[victim_team].discard(victim_id)

            # Check: Ist jemand allein in seinem Team?
            if clutch_player is None:
                for team, members in alive_tracker.items():
                    if len(members) == 1:
                        other_teams_alive = sum(
                            len(m) for t, m in alive_tracker.items() if t != team
                        )
                        if other_teams_alive >= 1:
                            clutch_player = next(iter(members))
                            clutch_started_at_kills = 0
                            if clutch_player in player_lookup:
                                player_lookup[clutch_player].clutch_attempts += 1
                            break

            if clutch_player:
                attacker_id = str(kill.get("attacker_steamid", ""))
                if attacker_id == clutch_player:
                    clutch_started_at_kills += 1

        # Runde gewonnen? Checke winner
        if clutch_player and clutch_player in player_lookup:
            if round_idx < len(rounds_df):
                winner = str(rounds_df.iloc[round_idx].get("winner", ""))
                player_team = team_by_player.get(clutch_player, "")
                if _is_own_round(round_idx, player_team, winner):
                    player_lookup[clutch_player].clutch_wins += 1
                    player_lookup[clutch_player].clutch_kills += clutch_started_at_kills


# -- Trade Kills --

def _analyze_trades(kills_df, player_lookup):
    kills_sorted = kills_df.sort_values("tick").reset_index(drop=True)
    kill_list = kills_sorted.to_dict("records")

    for i, kill in enumerate(kill_list):
        tick = int(kill.get("tick", 0))
        attacker_id = str(kill.get("attacker_steamid", ""))

        for j in range(i + 1, len(kill_list)):
            other = kill_list[j]
            other_tick = int(other.get("tick", 0))
            if other_tick - tick > TRADE_WINDOW_TICKS:
                break
            other_victim = str(other.get("user_steamid", ""))
            other_attacker = str(other.get("attacker_steamid", ""))
            if other_victim == attacker_id and other_attacker in player_lookup:
                player_lookup[other_attacker].trade_kills += 1
                break


# -- Helpers --

def _tick_to_round(tick: int, round_ticks: list[int]) -> int:
    for i, rt in enumerate(round_ticks):
        if tick <= rt:
            return i
    return len(round_ticks)


# CS2 MR12 constants
_MR12_HALF = 12
_OT_HALF = 3


def _own_side_for_round(round_idx: int, starting_side: str) -> str:
    """Return the side ("T"/"CT") the player is on for a given round.

    starting_side must be "T" or "CT" (the player's side in the first half).
    CS2 MR12: first 12 rounds on starting side, next 12 on swapped side.
    Overtime MR3: swap every 3 rounds from starting side again.
    """
    start_side = starting_side if starting_side in ("T", "CT") else "T"
    swap_side = "CT" if start_side == "T" else "T"

    if round_idx < _MR12_HALF:
        return start_side
    if round_idx < _MR12_HALF * 2:
        return swap_side
    # Overtime
    ot_idx = round_idx - _MR12_HALF * 2
    return start_side if (ot_idx // _OT_HALF) % 2 == 0 else swap_side


def _is_own_round(round_idx: int, starting_side: str, winner: str) -> bool:
    """Check if the round winner matches the player's side for that round.

    starting_side: "T" or "CT" (player's first-half side).
    winner: "T" or "CT" from round_end event.
    """
    own_side = _own_side_for_round(round_idx, starting_side)
    return winner == own_side


def _extract_scores(rounds_df, target_team: str) -> tuple[int, int]:
    """Extract (own_score, enemy_score) with correct halftime handling."""
    if rounds_df is None or len(rounds_df) == 0:
        return 0, 0

    own_score = 0
    enemy_score = 0

    for i, (_, r) in enumerate(rounds_df.iterrows()):
        winner = str(r.get("winner", ""))
        if not winner or winner == "0":
            continue
        if _is_own_round(i, target_team, winner):
            own_score += 1
        else:
            enemy_score += 1

    return own_score, enemy_score


def _process_per_round(kills_df, rounds_df, freeze_ticks, player_lookup, team_by_player):
    """KAST%, Survival, Multi-Kills, CT/T-Split, Death-Timing pro Spieler."""
    round_ticks = sorted(rounds_df["tick"].tolist())
    kills_sorted = kills_df.sort_values("tick").reset_index(drop=True)
    n_rounds = min(len(freeze_ticks), len(round_ticks))

    for i in range(n_rounds):
        start = freeze_ticks[i]
        end = round_ticks[i]
        round_dur = max(end - start, 1)

        rk = kills_sorted[
            (kills_sorted["tick"] >= start) & (kills_sorted["tick"] <= end)
        ]

        # Per-player round stats
        kill_counts: dict[str, int] = {}
        died_this_round: set[str] = set()
        assisted_this_round: set[str] = set()
        traded_this_round: set[str] = set()

        kill_records = rk.to_dict("records")
        for kill in kill_records:
            attacker_id = str(kill.get("attacker_steamid", ""))
            victim_id = str(kill.get("user_steamid", ""))
            assister_id = str(kill.get("assister_steamid", ""))

            if attacker_id in player_lookup:
                kill_counts[attacker_id] = kill_counts.get(attacker_id, 0) + 1
            if victim_id in player_lookup:
                died_this_round.add(victim_id)
            if assister_id and assister_id != "0" and assister_id in player_lookup:
                assisted_this_round.add(assister_id)

        # Trade detection for KAST (simplified)
        for idx_k, kill in enumerate(kill_records):
            victim_id = str(kill.get("user_steamid", ""))
            kill_tick = int(kill.get("tick", 0))
            for j in range(idx_k + 1, len(kill_records)):
                other = kill_records[j]
                if int(other.get("tick", 0)) - kill_tick > TRADE_WINDOW_TICKS:
                    break
                if str(other.get("user_steamid", "")) == str(kill.get("attacker_steamid", "")):
                    traded_this_round.add(victim_id)
                    break

        for sid, stats in player_lookup.items():
            if sid not in team_by_player:
                continue

            k = kill_counts.get(sid, 0)
            died = sid in died_this_round
            assisted = sid in assisted_this_round
            was_traded = sid in traded_this_round

            # Survival
            if not died:
                stats.rounds_survived += 1

            # KAST
            if k > 0 or assisted or not died or was_traded:
                stats.kast_rounds += 1

            # Multi-kills
            if k == 2:
                stats.rounds_2k += 1
            elif k == 3:
                stats.rounds_3k += 1
            elif k == 4:
                stats.rounds_4k += 1
            elif k >= 5:
                stats.rounds_5k += 1

            # CT/T Side Split (handles halftime + overtime)
            is_ct = _own_side_for_round(i, stats.team) == "CT"

            if is_ct:
                stats.ct_kills += k
                if died:
                    stats.ct_deaths += 1
            else:
                stats.t_kills += k
                if died:
                    stats.t_deaths += 1

            # Death Timing
            if died:
                death_rows = rk[rk["user_steamid"] == sid]
                if len(death_rows) > 0:
                    death_tick = int(death_rows.iloc[0].get("tick", start))
                    elapsed_pct = (death_tick - start) / round_dur
                    if elapsed_pct < 0.33:
                        stats.deaths_early += 1
                    elif elapsed_pct < 0.66:
                        stats.deaths_mid += 1
                    else:
                        stats.deaths_late += 1


# -- Crosshair Placement --

# Weapons to exclude from crosshair placement analysis
_EXCLUDE_CP_WEAPONS = {
    "weapon_knife", "knife", "weapon_knife_t", "knife_t",
    "weapon_bayonet", "bayonet",
    "weapon_hegrenade", "hegrenade",
    "weapon_molotov", "molotov",
    "weapon_incgrenade", "incgrenade",
    "weapon_inferno", "inferno",
    "weapon_c4", "c4",
    "weapon_taser", "taser",
}


def _angular_error(
    att_x: float, att_y: float, att_z: float,
    att_yaw: float, att_pitch: float,
    vic_x: float, vic_y: float, vic_z: float,
) -> float:
    """Berechnet den Winkel in Grad zwischen Blickrichtung und Zielposition.

    Niedrigerer Wert = bessere Crosshair Placement.
    """
    dx = vic_x - att_x
    dy = vic_y - att_y
    dz = vic_z - att_z

    dist_2d = math.sqrt(dx * dx + dy * dy)
    if dist_2d < 1.0:
        return 0.0  # Basically on top of each other

    # Ideal angles to look directly at victim
    ideal_yaw = math.degrees(math.atan2(dy, dx))
    ideal_pitch = -math.degrees(math.atan2(dz, dist_2d))  # Source: negative = up

    # Yaw difference (handle wrap-around at ±180°)
    dyaw = att_yaw - ideal_yaw
    dyaw = (dyaw + 180) % 360 - 180

    # Pitch difference
    dpitch = att_pitch - ideal_pitch

    # Total angular error (spherical)
    return math.sqrt(dyaw * dyaw + dpitch * dpitch)


PRE_AIM_OFFSET_TICKS = 32  # ~0.5s before kill (64 ticks/s)


def _process_crosshair_placement(parser: DemoParser, player_lookup: dict[str, "PlayerStats"]):
    """Analysiert Crosshair Placement: Blickwinkel ~0.5s VOR dem Kill.

    Misst den Winkel zwischen der Pre-Aim-Position des Fadenkreuzes und
    der tatsächlichen Gegner-Position. Niedriger = bessere Vorausrichtung.
    """
    # 1) Get kills with victim positions at kill time
    df = _get_enriched_event_df(
        parser, "player_death",
        ["X", "Y", "Z", "pitch", "yaw"],
    )
    if df is None:
        return

    required_cols = {"attacker_steamid", "attacker_pitch", "attacker_yaw",
                     "user_X", "user_Y", "user_Z"}
    if not required_cols.issubset(set(df.columns)):
        return

    # 2) Collect kill info and pre-aim ticks
    kill_infos = []
    pre_aim_ticks = set()

    for _, row in df.iterrows():
        attacker_id = str(row.get("attacker_steamid", ""))
        if attacker_id not in player_lookup:
            continue

        weapon = str(row.get("weapon", "")).lower()
        if weapon in _EXCLUDE_CP_WEAPONS:
            continue
        if int(row.get("penetrated", 0)) > 0:
            continue

        tick = int(row.get("tick", 0))
        pre_tick = max(tick - PRE_AIM_OFFSET_TICKS, 0)

        try:
            vic_x = float(row["user_X"])
            vic_y = float(row["user_Y"])
            vic_z = float(row["user_Z"])
        except (ValueError, KeyError):
            continue

        kill_infos.append({
            "attacker_id": attacker_id,
            "tick": tick,
            "pre_tick": pre_tick,
            "vic_x": vic_x,
            "vic_y": vic_y,
            "vic_z": vic_z,
        })
        pre_aim_ticks.add(pre_tick)

    if not kill_infos:
        return

    # 3) Fetch attacker view angles at pre-aim ticks via parse_ticks
    try:
        tick_df = parser.parse_ticks(
            ["pitch", "yaw", "X", "Y", "Z"],
            ticks=sorted(pre_aim_ticks),
        )
    except Exception:
        return

    if tick_df is None or len(tick_df) == 0:
        return

    # Build lookup: (steamid, tick) -> (pitch, yaw, x, y, z)
    tick_lookup: dict[tuple[str, int], tuple[float, float, float, float, float]] = {}
    for _, trow in tick_df.iterrows():
        sid = str(trow.get("steamid", ""))
        t = int(trow.get("tick", 0))
        try:
            tick_lookup[(sid, t)] = (
                float(trow["pitch"]),
                float(trow["yaw"]),
                float(trow["X"]),
                float(trow["Y"]),
                float(trow["Z"]),
            )
        except (ValueError, KeyError):
            continue

    # 4) Compute angular error for each kill
    for ki in kill_infos:
        key = (ki["attacker_id"], ki["pre_tick"])
        if key not in tick_lookup:
            continue

        pre_pitch, pre_yaw, pre_x, pre_y, pre_z = tick_lookup[key]

        error = _angular_error(
            pre_x, pre_y, pre_z,
            pre_yaw, pre_pitch,
            ki["vic_x"], ki["vic_y"], ki["vic_z"],
        )

        if error > 180:
            continue

        stats = player_lookup[ki["attacker_id"]]
        stats.crosshair_placement_sum += error
        stats.crosshair_placement_kills += 1

        if error < 5:
            stats.crosshair_placement_excellent += 1
        elif error < 15:
            stats.crosshair_placement_good += 1
        elif error < 30:
            stats.crosshair_placement_average += 1
        else:
            stats.crosshair_placement_poor += 1


def _extract_kill_positions(
    parser: DemoParser, player_lookup: dict[str, "PlayerStats"],
) -> list[dict]:
    """Extract attacker/victim XY positions for each kill (2D map visualization)."""
    df = _get_enriched_event_df(
        parser, "player_death",
        ["X", "Y", "Z"],
    )
    if df is None:
        return []

    positions = []
    for _, row in df.iterrows():
        attacker_id = str(row.get("attacker_steamid", ""))
        victim_id = str(row.get("user_steamid", ""))

        try:
            att_x = float(row.get("attacker_X", 0))
            att_y = float(row.get("attacker_Y", 0))
            vic_x = float(row.get("user_X", 0))
            vic_y = float(row.get("user_Y", 0))
        except (ValueError, TypeError):
            continue

        att_name = player_lookup[attacker_id].name if attacker_id in player_lookup else "?"
        vic_name = player_lookup[victim_id].name if victim_id in player_lookup else "?"

        positions.append({
            "attacker_id": attacker_id,
            "attacker_name": att_name,
            "victim_id": victim_id,
            "victim_name": vic_name,
            "attacker_x": att_x,
            "attacker_y": att_y,
            "victim_x": vic_x,
            "victim_y": vic_y,
            "weapon": str(row.get("weapon", "")),
            "headshot": bool(row.get("headshot", False)),
        })

    return positions


# -- Duel Matrix --

def _build_duel_matrix(
    kills_df, round_ticks: list[int], player_lookup: dict[str, PlayerStats], target_steam_id: str
) -> list[dict]:
    """Build per-opponent duel breakdown for the target player."""
    if kills_df is None or not target_steam_id:
        return []

    opponents: dict[str, dict] = {}  # opponent_steamid -> stats

    for _, kill in kills_df.iterrows():
        attacker_id = str(kill.get("attacker_steamid", ""))
        victim_id = str(kill.get("user_steamid", ""))
        weapon = str(kill.get("weapon", ""))
        headshot = bool(kill.get("headshot", False))

        if attacker_id == target_steam_id and victim_id in player_lookup:
            opp = opponents.setdefault(victim_id, {
                "name": player_lookup[victim_id].name, "steam_id": victim_id,
                "kills": 0, "deaths": 0, "hs_kills": 0, "hs_deaths": 0, "weapons_used": {},
            })
            opp["kills"] += 1
            if headshot:
                opp["hs_kills"] += 1
            opp["weapons_used"][weapon] = opp["weapons_used"].get(weapon, 0) + 1

        elif victim_id == target_steam_id and attacker_id in player_lookup:
            opp = opponents.setdefault(attacker_id, {
                "name": player_lookup[attacker_id].name, "steam_id": attacker_id,
                "kills": 0, "deaths": 0, "hs_kills": 0, "hs_deaths": 0, "weapons_used": {},
            })
            opp["deaths"] += 1
            if headshot:
                opp["hs_deaths"] += 1

    result = []
    for sid, data in opponents.items():
        k, d = data["kills"], data["deaths"]
        top_weapons = sorted(data["weapons_used"].items(), key=lambda x: -x[1])[:3]
        result.append({
            "name": data["name"],
            "steam_id": sid,
            "kills": k,
            "deaths": d,
            "kd": round(k / max(d, 1), 2),
            "hs_kills": data["hs_kills"],
            "hs_deaths": data["hs_deaths"],
            "top_weapons": [w[0] for w in top_weapons],
            "total_duels": k + d,
        })
    return sorted(result, key=lambda x: -x["total_duels"])


# -- Round Timeline --

def _build_round_timeline(
    kills_df, bomb_plant_df, bomb_defuse_df, rounds_df, freeze_ticks: list[int],
    round_ticks: list[int], player_lookup: dict[str, PlayerStats],
    target_steam_id: str, target_team: str,
) -> list[dict]:
    """Build per-round event timeline for the target player."""
    if kills_df is None or rounds_df is None:
        return []

    n_rounds = min(len(freeze_ticks), len(round_ticks))
    kills_sorted = kills_df.sort_values("tick").reset_index(drop=True)
    timeline = []

    for i in range(n_rounds):
        start = freeze_ticks[i]
        end = round_ticks[i]
        round_dur = max(end - start, 1)

        winner = str(rounds_df.iloc[i].get("winner", "")) if i < len(rounds_df) else ""
        won = _is_own_round(i, target_team, winner) if winner else False
        side = _own_side_for_round(i, target_team)

        rk = kills_sorted[(kills_sorted["tick"] >= start) & (kills_sorted["tick"] <= end)]

        events = []
        player_kills = 0
        player_died = False
        player_death_pct = None

        for _, kill in rk.iterrows():
            tick = int(kill.get("tick", start))
            elapsed_pct = round((tick - start) / round_dur * 100, 1)
            attacker_id = str(kill.get("attacker_steamid", ""))
            victim_id = str(kill.get("user_steamid", ""))
            weapon = str(kill.get("weapon", ""))
            headshot = bool(kill.get("headshot", False))

            if attacker_id == target_steam_id:
                events.append({
                    "type": "kill", "pct": elapsed_pct, "weapon": weapon,
                    "headshot": headshot, "target": player_lookup.get(victim_id, PlayerStats("?", "", "")).name,
                })
                player_kills += 1
            elif victim_id == target_steam_id:
                events.append({
                    "type": "death", "pct": elapsed_pct, "weapon": weapon,
                    "headshot": headshot, "killer": player_lookup.get(attacker_id, PlayerStats("?", "", "")).name,
                })
                player_died = True
                player_death_pct = elapsed_pct

        # Bomb events
        if bomb_plant_df is not None:
            for _, bp in bomb_plant_df.iterrows():
                tick = int(bp.get("tick", 0))
                if start <= tick <= end:
                    planter_id = str(bp.get("user_steamid", ""))
                    is_self = planter_id == target_steam_id
                    events.append({
                        "type": "bomb_plant", "pct": round((tick - start) / round_dur * 100, 1),
                        "player": player_lookup.get(planter_id, PlayerStats("?", "", "")).name,
                        "is_self": is_self,
                    })

        if bomb_defuse_df is not None:
            for _, bd in bomb_defuse_df.iterrows():
                tick = int(bd.get("tick", 0))
                if start <= tick <= end:
                    defuser_id = str(bd.get("user_steamid", ""))
                    is_self = defuser_id == target_steam_id
                    events.append({
                        "type": "bomb_defuse", "pct": round((tick - start) / round_dur * 100, 1),
                        "player": player_lookup.get(defuser_id, PlayerStats("?", "", "")).name,
                        "is_self": is_self,
                    })

        events.sort(key=lambda e: e["pct"])

        timeline.append({
            "round": i + 1,
            "side": side,
            "won": won,
            "events": events,
            "player_kills": player_kills,
            "player_died": player_died,
            "died_early": player_death_pct is not None and player_death_pct < 33,
        })

    return timeline


# -- Economy Performance --

_ECO_THRESHOLD = 1500
_FORCE_THRESHOLD = 3700


def _process_economy(
    parser: DemoParser, kills_df, hurt_df, rounds_df,
    freeze_ticks: list[int], round_ticks: list[int],
    target_steam_id: str, target_team: str,
    player_lookup: dict[str, PlayerStats],
) -> dict:
    """Classify rounds by economy and compute per-category performance."""
    if not target_steam_id or rounds_df is None or not freeze_ticks:
        return {}

    n_rounds = min(len(freeze_ticks), len(round_ticks))

    # Get equipment value at freeze-end ticks
    try:
        tick_data = parser.parse_ticks(["current_equip_value"], ticks=freeze_ticks[:n_rounds])
    except Exception:
        return {}

    if tick_data is None or len(tick_data) == 0:
        return {}

    # Build per-round equip value for target player
    player_equip = {}
    for _, row in tick_data.iterrows():
        sid = str(row.get("steamid", ""))
        if sid != target_steam_id:
            continue
        tick = int(row.get("tick", 0))
        val = int(row.get("current_equip_value", 0))
        # Map tick to round index
        for ri, ft in enumerate(freeze_ticks[:n_rounds]):
            if tick == ft:
                player_equip[ri] = val
                break

    if not player_equip:
        return {}

    # Classify each round
    categories = {"pistol": [], "eco": [], "force": [], "fullbuy": []}
    for ri in range(n_rounds):
        equip = player_equip.get(ri, 0)
        is_pistol = (ri == 0) or (ri == _MR12_HALF)
        if is_pistol:
            cat = "pistol"
        elif equip < _ECO_THRESHOLD:
            cat = "eco"
        elif equip < _FORCE_THRESHOLD:
            cat = "force"
        else:
            cat = "fullbuy"
        categories[cat].append(ri)

    # Compute per-category stats
    kills_sorted = kills_df.sort_values("tick").reset_index(drop=True) if kills_df is not None else None
    result = {}

    for cat, round_indices in categories.items():
        if not round_indices:
            continue

        total_kills = 0
        total_deaths = 0
        total_damage = 0
        rounds_won = 0

        for ri in round_indices:
            if ri >= n_rounds:
                continue
            start = freeze_ticks[ri]
            end = round_ticks[ri]

            # Round win
            winner = str(rounds_df.iloc[ri].get("winner", "")) if ri < len(rounds_df) else ""
            if winner and _is_own_round(ri, target_team, winner):
                rounds_won += 1

            # Kills/deaths
            if kills_sorted is not None:
                rk = kills_sorted[(kills_sorted["tick"] >= start) & (kills_sorted["tick"] <= end)]
                for _, kill in rk.iterrows():
                    if str(kill.get("attacker_steamid", "")) == target_steam_id:
                        total_kills += 1
                    if str(kill.get("user_steamid", "")) == target_steam_id:
                        total_deaths += 1

            # Damage
            if hurt_df is not None:
                rd = hurt_df[(hurt_df["tick"] >= start) & (hurt_df["tick"] <= end)]
                for _, dmg in rd.iterrows():
                    if str(dmg.get("attacker_steamid", "")) == target_steam_id:
                        total_damage += int(dmg.get("dmg_health", 0))

        n = len(round_indices)
        result[cat] = {
            "rounds": n,
            "kills": total_kills,
            "deaths": total_deaths,
            "kd": round(total_kills / max(total_deaths, 1), 2),
            "adr": round(total_damage / max(n, 1), 1),
            "win_rate": round(rounds_won / max(n, 1) * 100, 1),
            "rounds_won": rounds_won,
        }

    return result


def _clean_map_name(raw: str) -> str:
    name = raw.replace("de_", "").replace("cs_", "")
    return name.capitalize()
