"""Obsidian-Export – erzeugt Markdown mit Frontmatter und Wikilinks + JSON-Export."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .parser import MatchResult, PlayerStats


def export_match(result: MatchResult, coach_report: str, vault_path: str,
                 subfolder: str = "CS2-Coach") -> tuple[Path, str]:
    vault = Path(vault_path)
    coach_dir = vault / subfolder
    coach_dir.mkdir(parents=True, exist_ok=True)

    _ensure_concept_notes(vault, subfolder)

    match_date = result.match_date or datetime.now().strftime("%Y-%m-%d")
    time_str = result.match_datetime.split(" ")[1].replace(":", "") if result.match_datetime else datetime.now().strftime("%H%M")
    score = f"{result.score_team1}-{result.score_team2}"

    # Markdown export
    md_filename = f"{match_date}_{result.map_name}_{score}_{time_str}.md"
    md_path = coach_dir / md_filename
    content = _build_markdown(result, coach_report, match_date)
    md_path.write_text(content, encoding="utf-8")

    # JSON export (for Trends, Exports browser, etc.)
    export_dir = coach_dir / "exports"
    export_dir.mkdir(exist_ok=True)
    json_filename = f"{match_date}_{result.map_name.lower()}_{score}_{time_str}_coach.json"
    json_path = export_dir / json_filename
    json_data = _build_export_json(result, coach_report)
    json_path.write_text(
        json.dumps(json_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return md_path, json_filename


def _compact_kill_positions(positions: list[dict], target_id: str) -> list[dict]:
    """Filter kill positions to target player's kills/deaths, compact format."""
    compact = []
    for kp in positions:
        is_kill = kp["attacker_id"] == target_id
        is_death = kp["victim_id"] == target_id
        if not is_kill and not is_death:
            continue
        compact.append({
            "t": "k" if is_kill else "d",
            "x": round(kp["attacker_x" if is_kill else "victim_x"], 1),
            "y": round(kp["attacker_y" if is_kill else "victim_y"], 1),
            "z": round(kp.get("attacker_z" if is_kill else "victim_z", 0), 1),
            "ex": round(kp["victim_x" if is_kill else "attacker_x"], 1),
            "ey": round(kp["victim_y" if is_kill else "attacker_y"], 1),
            "ez": round(kp.get("victim_z" if is_kill else "attacker_z", 0), 1),
            "w": kp["weapon"],
            "hs": kp["headshot"],
            "r": kp["round"],
            "e": kp["victim_name"] if is_kill else kp["attacker_name"],
        })
    return compact


_UTIL_SHORT = {"flash": "f", "smoke": "s", "he": "h", "molotov": "m"}


def _compact_utility_positions(positions: list[dict], target_id: str) -> list[dict]:
    """Detonationsorte der eigenen Granaten, kompakt.

    Der Parser ermittelt diese Positionen bei jeder Analyse, der Export hat
    sie bisher verworfen - damit waren sie nach dem Wechsel auf die
    Export-Detailseite gar nicht mehr erreichbar. Sie beantworten, WO
    Utility geworfen wird, nicht nur wie viel.

    Wie bei den Kill-Positionen nur die des Zielspielers, sonst waere der
    Export um ein Vielfaches groesser.
    """
    compact = []
    for up in positions:
        if str(up.get("player_id", "")) != str(target_id):
            continue
        t = _UTIL_SHORT.get(up.get("type", ""))
        if t is None:
            continue
        compact.append({
            "t": t,
            "x": round(float(up.get("x", 0)), 1),
            "y": round(float(up.get("y", 0)), 1),
            "r": int(up.get("round", 0)),
        })
    return compact


def _build_export_json(result: MatchResult, coach_report: str) -> dict:
    """Build JSON export with all stats for web UI consumption."""
    s = result.player_stats
    scoreboard = []
    for p in sorted(result.all_players, key=lambda x: x.kills, reverse=True):
        scoreboard.append(_player_to_json(p, p.steam_id == s.steam_id))

    return {
        "match": {
            "date": result.match_date,
            "datetime": result.match_datetime,
            "map": result.map_name,
            "score_own": result.score_team1,
            "score_enemy": result.score_team2,
            "total_rounds": result.total_rounds,
            "result": result.result_str,
            "demo_file": Path(result.demo_path).name if result.demo_path else "",
        },
        "player": _player_to_json(s, True, include_rating=result.rating),
        "report": coach_report,
        "scoreboard": scoreboard,
        "duel_matrix": result.duel_matrix,
        "round_timeline": result.round_timeline,
        "economy": result.economy_performance,
        "kill_positions": _compact_kill_positions(result.kill_positions, s.steam_id),
        "utility_positions": _compact_utility_positions(
            getattr(result, "utility_positions", []), s.steam_id),
    }


def _player_to_json(p: PlayerStats, is_target: bool,
                     include_rating: float | None = None) -> dict:
    util_total = p.flashes_thrown + p.smokes_thrown + p.he_thrown + p.molotovs_thrown
    d = {
        "name": p.name,
        "steam_id": p.steam_id,
        "kills": p.kills,
        "deaths": p.deaths,
        "assists": p.assists,
        "kd": round(p.kd_ratio, 2),
        "adr": round(p.adr, 1),
        "hs_pct": round(p.headshot_pct, 1),
        "kast_pct": round(p.kast_pct, 1),
        "survival_rate": round(p.survival_rate, 1),
        "accuracy": round(p.accuracy, 1),
        "opening_kills": p.opening_kills,
        "opening_deaths": p.opening_deaths,
        "trade_kills": p.trade_kills,
        "crosshair_placement": {
            "avg_degrees": round(p.crosshair_placement_avg, 1),
            "median_degrees": round(p.crosshair_placement_median, 1),
            "kills_analyzed": p.crosshair_placement_kills,
            "rating": p.crosshair_placement_rating,
            "excellent": p.crosshair_placement_excellent,
            "good": p.crosshair_placement_good,
            "average": p.crosshair_placement_average,
            "poor": p.crosshair_placement_poor,
        },
        "counter_strafe_score": round(p.counter_strafe_score, 1),
        "counter_strafe_pct": round(p.counter_strafe_score, 1),
        "avg_inaccuracy_move": round(p.avg_inaccuracy_move, 2),
        "utility_per_round": round(p.utility_per_round, 2),
        "is_target": is_target,
    }
    if include_rating is not None:
        d["rating"] = include_rating

    # Extended stats for export_detail view
    if is_target:
        if p.rounds_2k or p.rounds_3k or p.rounds_4k or p.rounds_5k:
            d["multikills"] = {
                "2k": p.rounds_2k, "3k": p.rounds_3k,
                "4k": p.rounds_4k, "5k": p.rounds_5k,
            }
        if p.burst_hits or p.spray_hits or p.avg_recoil_index:
            d["spray_control"] = {
                "burst_hits": p.burst_hits,
                "spray_hits": p.spray_hits,
                "avg_recoil_index": round(p.avg_recoil_index, 2),
            }
        if p.awp_kills or p.rifle_kills or p.pistol_kills:
            d["weapons"] = {
                "awp_kills": p.awp_kills,
                "rifle_kills": p.rifle_kills,
                "pistol_kills": p.pistol_kills,
            }
        if p.avg_fight_distance > 0:
            d["engagement_distance"] = {
                "avg": round(p.avg_fight_distance, 0),
                "close": p.close_range_kills,
                "mid": p.mid_range_kills,
                "long": p.long_range_kills,
            }
        d["utility"] = {
            "total": util_total,
            "per_round": round(p.utility_per_round, 2),
            "flashes": p.flashes_thrown,
            "smokes": p.smokes_thrown,
            "he": p.he_thrown,
            "molotovs": p.molotovs_thrown,
        }
        if p.flash_enemies_blinded or p.flash_teammates_blinded:
            d["flash_effectiveness"] = {
                "enemies_blinded": p.flash_enemies_blinded,
                "teammates_blinded": p.flash_teammates_blinded,
                "avg_enemy_duration": round(p.flash_avg_enemy_duration, 2),
            }
        if p.clutch_attempts:
            d["clutches"] = {
                "wins": p.clutch_wins,
                "attempts": p.clutch_attempts,
            }
        if p.ct_kills or p.ct_deaths or p.t_kills or p.t_deaths:
            d["side_split"] = {
                "ct_kills": p.ct_kills, "ct_deaths": p.ct_deaths,
                "t_kills": p.t_kills, "t_deaths": p.t_deaths,
            }
        if p.deaths_early or p.deaths_mid or p.deaths_late:
            d["death_timing"] = {
                "early": p.deaths_early,
                "mid": p.deaths_mid,
                "late": p.deaths_late,
            }
        kill_ctx = {}
        if p.kills_thru_smoke:
            kill_ctx["thru_smoke"] = p.kills_thru_smoke
        if p.kills_penetrated:
            kill_ctx["wallbang"] = p.kills_penetrated
        if p.kills_noscope:
            kill_ctx["noscope"] = p.kills_noscope
        if kill_ctx:
            d["kill_context"] = kill_ctx
        if p.rank_old or p.rank_new:
            d["rank"] = {
                "old": p.rank_old,
                "new": p.rank_new,
                "change": p.rank_change,
            }

    return d


def _build_markdown(result: MatchResult, coach_report: str, date: str) -> str:
    s = result.player_stats
    rating = result.rating

    tags = _generate_tags(result)

    frontmatter = (
        "---\n"
        f"date: {date}\n"
        f"map: {result.map_name}\n"
        f"result: {result.result_str}\n"
        f"score: \"{result.score_team1}:{result.score_team2}\"\n"
        f"rating: {rating}\n"
        f"kd: {s.kd_ratio:.2f}\n"
        f"adr: {s.adr:.1f}\n"
        f"player: {s.name}\n"
        f"tags: [{', '.join(tags)}]\n"
        "---\n\n"
    )

    header = (
        f"# CS2 Match — [[{result.map_name}]] — {date}\n\n"
        f"**Spieler:** {s.name}  \n"
        f"**Ergebnis:** {result.result_str} "
        f"({result.score_team1}:{result.score_team2})  \n"
        f"**Rating:** {rating}  \n\n"
    )

    stats_table = _build_stats_table(s)
    scoreboard = _build_scoreboard(result)

    links = _build_links_section(result)

    full = (
        frontmatter
        + header
        + stats_table
        + "\n---\n\n"
        + coach_report
        + "\n\n---\n\n"
        + scoreboard
        + "\n---\n\n"
        + links
    )

    return full


def _build_stats_table(s: PlayerStats) -> str:
    util_total = s.flashes_thrown + s.smokes_thrown + s.he_thrown + s.molotovs_thrown
    lines = [
        "## Statistiken\n",
        "| Metrik | Wert |",
        "|--------|------|",
        f"| Kills | {s.kills} |",
        f"| Deaths | {s.deaths} |",
        f"| Assists | {s.assists} |",
        f"| K/D | {s.kd_ratio:.2f} |",
        f"| ADR | {s.adr:.1f} |",
        f"| HS% | {s.headshot_pct:.0f}% |",
        f"| Opening Duels | {s.opening_kills}W / {s.opening_deaths}L |",
        f"| Trade Kills | {s.trade_kills} |",
        f"| Crosshair Placement | {s.crosshair_placement_median:.1f}° ({s.crosshair_placement_rating}) |",
        f"| Counter-Strafe | {s.counter_strafe_score:.0f}% |",
        f"| Spray-Control | {s.burst_spray_ratio} |",
        f"| Waffen-Split | {s.awp_rifle_split} |",
        f"| Avg Kampfdistanz | {s.avg_fight_distance:.0f} units |",
        f"| Utility | {util_total} ({s.utility_per_round:.1f}/Runde) |",
        f"| Flash-Effektivität | {s.flash_effectiveness} |",
        f"| Clutches | {s.clutch_rate} |",
        f"| KAST% | {s.kast_pct:.0f}% |",
        f"| Survival | {s.survival_rate:.0f}% |",
        f"| Accuracy | {s.accuracy:.1f}% |",
        f"| Multi-Kills | {s.multikill_str} |",
        f"| CT/T-Split | {s.ct_t_split} |",
        f"| Death-Timing | {s.death_timing_str} |",
    ]

    if s.rank_old > 0:
        change = f"+{s.rank_change:.0f}" if s.rank_change >= 0 else f"{s.rank_change:.0f}"
        lines.append(f"| Rank | {s.rank_old} -> {s.rank_new} ({change}) |")

    return "\n".join(lines) + "\n\n"


def _build_scoreboard(result: MatchResult) -> str:
    if not result.all_players:
        return ""

    lines = [
        "## Scoreboard\n",
        "| Spieler | K | D | A | ADR | K/D |",
        "|---------|---|---|---|-----|-----|",
    ]

    sorted_players = sorted(result.all_players, key=lambda p: p.kills, reverse=True)
    for p in sorted_players:
        marker = " **←**" if p.steam_id == result.player_stats.steam_id else ""
        lines.append(
            f"| {p.name}{marker} | {p.kills} | {p.deaths} | "
            f"{p.assists} | {p.adr:.0f} | {p.kd_ratio:.2f} |"
        )

    return "\n".join(lines) + "\n\n"


def _generate_tags(result: MatchResult) -> list[str]:
    tags = ["cs2", "match-analyse", result.map_name.lower()]

    s = result.player_stats
    if result.result_str == "Sieg":
        tags.append("sieg")
    elif result.result_str == "Niederlage":
        tags.append("niederlage")

    if s.crosshair_placement_kills > 0 and s.crosshair_placement_median > 15:
        tags.append("crosshair-placement-schwach")
    if s.counter_strafe_score < 70:
        tags.append("counter-strafe-problem")
    if s.adr < 70:
        tags.append("low-adr")
    if s.kd_ratio >= 1.3:
        tags.append("carry")
    if s.opening_deaths > s.opening_kills:
        tags.append("opening-duel-schwach")
    if s.utility_per_round < 1.0:
        tags.append("low-utility")
    if s.clutch_wins > 0:
        tags.append("clutch-player")
    if s.flash_teammates_blinded > s.flash_enemies_blinded and s.flashes_thrown > 3:
        tags.append("team-flasher")

    return tags


def _build_links_section(result: MatchResult) -> str:
    s = result.player_stats
    links = {f"[[{result.map_name}]]"}

    if s.awp_kills > 0:
        links.add("[[AWP]]")
    if s.rifle_kills > 0:
        links.add("[[Rifle]]")

    if s.crosshair_placement_kills > 0 and s.crosshair_placement_median > 15:
        links.add("[[Crosshair Placement]]")
    if s.counter_strafe_score < 80:
        links.add("[[Counter-Strafing]]")
    if s.opening_deaths > s.opening_kills:
        links.add("[[Opening Duels]]")
    if s.adr < 75:
        links.add("[[Positionierung]]")
        links.add("[[Trockener Peek]]")
    if s.trade_kills / max(s.kills, 1) < 0.15:
        links.add("[[Teamplay]]")
    if s.kd_ratio < 0.9:
        links.add("[[Clutch-Disziplin]]")
    if s.utility_per_round < 1.5:
        links.add("[[Utility-Usage]]")
    if s.flash_teammates_blinded > s.flash_enemies_blinded and s.flashes_thrown > 3:
        links.add("[[Flash-Technik]]")
    if s.avg_recoil_index > 4:
        links.add("[[Spray-Control]]")
    if s.clutch_attempts > 0:
        links.add("[[Clutch-Disziplin]]")

    return (
        "## Verknüpfte Konzepte\n\n"
        + " · ".join(sorted(links))
        + "\n"
    )


CONCEPT_NOTES = {
    "Crosshair Placement": (
        "# Crosshair Placement\n\n"
        "Die Position deines Fadenkreuzes BEVOR ein Fight beginnt.\n\n"
        "## Prinzip\n"
        "Das Crosshair muss immer auf Kopfhöhe zeigen und auf den "
        "nächsten erwarteten Gegner-Angle gerichtet sein. Je weniger "
        "Mausbewegung nötig ist, desto schneller der Kill.\n\n"
        "## Messung\n"
        "- Gemessen in Grad (°): Winkel zwischen Fadenkreuz-Position "
        "0.5s vor dem Kill und Gegner-Position\n"
        "- < 5° = Exzellent (Pro-Level)\n"
        "- 5-15° = Gut\n"
        "- 15-30° = Durchschnittlich (zu viel Nachkorrektur)\n"
        "- > 30° = Schwach (Crosshair zeigt komplett woanders hin)\n\n"
        "## Training\n"
        "- Auf leeren Servern durch die Map laufen und jeden Angle pre-aimen\n"
        "- Workshop: Prefire Maps (Yprac)\n"
        "- Deathmatch: Bewusst Kopfhöhe halten\n"
        "- Beim Bewegen nie auf den Boden schauen\n\n"
        "## Verwandte Konzepte\n"
        "[[Counter-Strafing]] · [[Opening Duels]] · [[Positionierung]]\n"
    ),
    "Counter-Strafing": (
        "# Counter-Strafing\n\n"
        "Die Grundmechanik für präzises Schießen in CS2.\n\n"
        "## Prinzip\n"
        "Vor dem Schuss die Gegenrichtungstaste antippen, um die "
        "Geschwindigkeit auf 0 zu bringen. Erst dann schießen.\n\n"
        "## Training\n"
        "- Yprac Maps\n"
        "- Workshop: Counter-Strafe Training\n"
        "- Deathmatch mit Fokus auf Stops\n\n"
        "## Verwandte Konzepte\n"
        "[[Trockener Peek]] · [[Positionierung]] · [[Rifle]]\n"
    ),
    "Trockener Peek": (
        "# Trockener Peek\n\n"
        "Ein Peek ohne Utility-Support — hohes Risiko, oft unnötig.\n\n"
        "## Wann vermeiden\n"
        "- CT-Side: Fast immer. Halte den Winkel, lass den T kommen.\n"
        "- T-Side: Nur wenn du Info brauchst und kein Util hast.\n\n"
        "## Verwandte Konzepte\n"
        "[[Counter-Strafing]] · [[Opening Duels]] · [[Positionierung]]\n"
    ),
    "Opening Duels": (
        "# Opening Duels\n\n"
        "Das erste Duell jeder Runde — entscheidet den Mannvorteil.\n\n"
        "## Tipps\n"
        "- Immer mit Flash/Smoke vorbereiten\n"
        "- Shoulder-Peek für Info\n"
        "- Crosshair auf Head-Level\n\n"
        "## Verwandte Konzepte\n"
        "[[Counter-Strafing]] · [[Trockener Peek]] · [[Positionierung]]\n"
    ),
    "Positionierung": (
        "# Positionierung\n\n"
        "Wo du stehst, entscheidet über Leben und Tod in CS2.\n\n"
        "## Grundregeln\n"
        "- Nie mehr als einen Winkel gleichzeitig halten\n"
        "- Off-Angles nutzen statt Default-Spots\n"
        "- Nach jedem Kill: Reposition\n\n"
        "## Verwandte Konzepte\n"
        "[[Trockener Peek]] · [[Clutch-Disziplin]] · [[Counter-Strafing]]\n"
    ),
    "Clutch-Disziplin": (
        "# Clutch-Disziplin\n\n"
        "Ruhe in 1vX-Situationen — keine Panik-Peeks.\n\n"
        "## Regeln\n"
        "- Letzter Alive = kein Hero-Play\n"
        "- Zeit nutzen, nicht verschenken\n"
        "- Sound-Cues auswerten\n"
        "- Counter-Strafe auch unter Druck\n\n"
        "## Verwandte Konzepte\n"
        "[[Counter-Strafing]] · [[Positionierung]] · [[Teamplay]]\n"
    ),
    "Teamplay": (
        "# Teamplay\n\n"
        "CS2 ist kein Deathmatch — Koordination gewinnt Runden.\n\n"
        "## Grundlagen\n"
        "- Buddy-System: Nie allein peeken\n"
        "- Trades sichern\n"
        "- Utility für Teammates einsetzen\n"
        "- Calls geben\n\n"
        "## Verwandte Konzepte\n"
        "[[Opening Duels]] · [[Positionierung]]\n"
    ),
    "AWP": (
        "# AWP\n\n"
        "Die stärkste Waffe im Spiel — aber teuer und rollendefinierend.\n\n"
        "## Regeln\n"
        "- Max. 1 AWP pro Team (Ausnahmen: Double-AWP-Setups auf bestimmten Maps)\n"
        "- Aggressives AWP-Peeking nur mit Flash-Support\n"
        "- Repositionieren nach jedem Schuss\n\n"
        "## Verwandte Konzepte\n"
        "[[Rifle]] · [[Positionierung]]\n"
    ),
    "Utility-Usage": (
        "# Utility-Usage\n\n"
        "Nades sind der wichtigste Unterschied zwischen CS2 und Deathmatch.\n\n"
        "## Ziel: 2-3 Nades pro Runde\n"
        "- Smoke: Map-Control, Sichtlinien blocken\n"
        "- Flash: Peeks vorbereiten, Retakes einleiten\n"
        "- Molotov: Area Denial, Anti-Rush, Positionen clearen\n"
        "- HE: Eco-Damage, Stack-Punish\n\n"
        "## Training\n"
        "- Workshop: Yprac Maps für Lineups\n"
        "- Jede Runde bewusst mindestens 2 Nades werfen\n\n"
        "## Verwandte Konzepte\n"
        "[[Flash-Technik]] · [[Positionierung]] · [[Teamplay]]\n"
    ),
    "Flash-Technik": (
        "# Flash-Technik\n\n"
        "Effektive Flashes blenden den Gegner, nicht das eigene Team.\n\n"
        "## Pop-Flash Prinzip\n"
        "- Flash so werfen, dass sie direkt beim Erscheinen detoniert\n"
        "- Über Hindernisse werfen (Wände, Boxen)\n"
        "- Teammate flashen = aktiv schädlich\n\n"
        "## Metriken\n"
        "- Gute Flash: Gegner > 2s geblindet\n"
        "- Schlechte Flash: Team geblindet oder Gegner < 1s\n\n"
        "## Verwandte Konzepte\n"
        "[[Utility-Usage]] · [[Opening Duels]] · [[Teamplay]]\n"
    ),
    "Spray-Control": (
        "# Spray-Control\n\n"
        "Recoil-Management in CS2 — wann sprayen, wann tappen.\n\n"
        "## Regeln\n"
        "- Burst (2-3 Schüsse) auf mittlere und weite Distanz\n"
        "- Full-Spray nur auf kurze Distanz (<400 units)\n"
        "- Nach 5+ Bullets: Spray-Reset, re-aimen\n"
        "- Recoil-Index > 5 = zu tiefer Spray\n\n"
        "## Training\n"
        "- Workshop: Recoil Master\n"
        "- Deathmatch: Bewusst nur Burst-Fire\n\n"
        "## Verwandte Konzepte\n"
        "[[Counter-Strafing]] · [[Rifle]] · [[AWP]]\n"
    ),
    "Rifle": (
        "# Rifle\n\n"
        "AK-47 / M4 — die Brot-und-Butter-Waffen in CS2.\n\n"
        "## Spray Control\n"
        "- Erste 10 Kugeln meistern\n"
        "- Burst auf Distanz\n"
        "- [[Counter-Strafing]] vor jedem Spray\n\n"
        "## Verwandte Konzepte\n"
        "[[Counter-Strafing]] · [[AWP]]\n"
    ),
}

MAP_NOTE_TEMPLATE = (
    "# {map_name}\n\n"
    "CS2 Map — Alle Match-Analysen zu dieser Map.\n\n"
    "## Matches\n"
    "```dataview\n"
    "TABLE date, result, score, rating, kd, adr\n"
    "FROM \"CS2-Coach\"\n"
    "WHERE map = \"{map_name}\"\n"
    "SORT date DESC\n"
    "```\n\n"
    "## Notizen\n"
    "_Map-spezifische Strategie-Notizen hier einfügen._\n"
)


def _ensure_concept_notes(vault: Path, subfolder: str):
    concepts_dir = vault / subfolder / "Konzepte"
    concepts_dir.mkdir(parents=True, exist_ok=True)

    for name, content in CONCEPT_NOTES.items():
        path = concepts_dir / f"{name}.md"
        if not path.exists():
            path.write_text(content, encoding="utf-8")

    maps_dir = vault / subfolder / "Maps"
    maps_dir.mkdir(parents=True, exist_ok=True)

    for map_name in ["Mirage", "Inferno", "Nuke", "Overpass", "Anubis",
                     "Ancient", "Vertigo", "Dust2"]:
        path = maps_dir / f"{map_name}.md"
        if not path.exists():
            path.write_text(
                MAP_NOTE_TEMPLATE.format(map_name=map_name),
                encoding="utf-8",
            )
