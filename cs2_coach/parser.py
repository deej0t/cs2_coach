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

    # 10) Per-Round-Analyse: KAST, Survival, Multi-Kills, CT/T-Split, Death-Timing
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

    return MatchResult(
        map_name=_clean_map_name(map_name),
        score_team1=score_t1,
        score_team2=score_t2,
        player_stats=target_stats,
        all_players=list(player_lookup.values()),
        demo_path=demo_path,
        match_date=match_date,
        match_datetime=match_datetime,
    )


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
                team_to_winner = {"2": "T", "3": "CT"}
                if team_to_winner.get(player_team, "") == winner:
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


def _extract_scores(rounds_df, target_team: str) -> tuple[int, int]:
    if rounds_df is None or len(rounds_df) == 0:
        return 0, 0

    team_scores: dict[str, int] = {}
    for _, r in rounds_df.iterrows():
        winner = str(r.get("winner", ""))
        team_scores[winner] = team_scores.get(winner, 0) + 1

    team_to_winner = {"2": "T", "3": "CT"}
    target_winner = team_to_winner.get(target_team, target_team)

    scores = sorted(team_scores.values(), reverse=True)
    if target_winner in team_scores:
        own = team_scores[target_winner]
        total = sum(team_scores.values())
        return own, total - own

    if len(scores) >= 2:
        return scores[0], scores[1]
    if len(scores) == 1:
        return scores[0], 0
    return 0, 0


def _process_per_round(kills_df, rounds_df, freeze_ticks, player_lookup, team_by_player):
    """KAST%, Survival, Multi-Kills, CT/T-Split, Death-Timing pro Spieler."""
    round_ticks = sorted(rounds_df["tick"].tolist())
    kills_sorted = kills_df.sort_values("tick").reset_index(drop=True)
    n_rounds = min(len(freeze_ticks), len(round_ticks))

    # Half-time: first 12 rounds = starting side
    HALF = 12

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

            # CT/T Side Split
            is_first_half = i < HALF
            # Starting side: team 2 starts T, team 3 starts CT
            starting_team = stats.team
            if starting_team == "3":  # CT start
                is_ct = is_first_half
            else:  # T start
                is_ct = not is_first_half

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


def _clean_map_name(raw: str) -> str:
    name = raw.replace("de_", "").replace("cs_", "")
    return name.capitalize()
