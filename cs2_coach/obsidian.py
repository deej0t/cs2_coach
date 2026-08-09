"""Obsidian-Export – erzeugt Markdown mit Frontmatter und Wikilinks."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .parser import MatchResult, PlayerStats


def export_match(result: MatchResult, coach_report: str, vault_path: str,
                 subfolder: str = "CS2-Coach") -> Path:
    vault = Path(vault_path)
    coach_dir = vault / subfolder
    coach_dir.mkdir(parents=True, exist_ok=True)

    _ensure_concept_notes(vault, subfolder)

    today = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H%M")
    filename = f"{today}_{result.map_name}_Match_{time_str}.md"
    filepath = coach_dir / filename

    content = _build_markdown(result, coach_report, today)
    filepath.write_text(content, encoding="utf-8")

    return filepath


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
