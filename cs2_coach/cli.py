"""CLI-Interface für den CS2 Coach."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .coach import generate_report
from .graph import index_vault
from .obsidian import export_match
from .parser import parse_demo, MatchResult

console = Console()

DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def load_config(config_path: str | None = None) -> dict:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.exists():
        console.print(f"[red]Config nicht gefunden: {path}[/red]")
        console.print(
            "Erstelle eine config.yaml mit deinem Obsidian-Vault-Pfad.\n"
            "Siehe README für Details."
        )
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@click.group()
@click.option("--config", "-c", default=None, help="Pfad zur config.yaml")
@click.pass_context
def cli(ctx, config):
    """CS2 Coach — Demo-Analyse mit Obsidian-Integration."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(config)


@cli.command()
@click.argument("demo_path", type=click.Path(exists=True))
@click.option("--player", "-p", default=None, help="Steam-Name des Spielers")
@click.option("--steamid", "-s", default=None, help="SteamID64 des Spielers")
@click.option("--no-export", is_flag=True, help="Kein Obsidian-Export")
@click.option("--raw", is_flag=True, help="Raw-JSON-Output für externe KI-Analyse (auto-Dateiname)")
@click.option("--raw-out", default=None, help="JSON-Datei für Raw-Output (eigener Pfad)")
@click.option("--raw-stdout", is_flag=True, help="Raw-JSON auf stdout statt Datei")
@click.pass_context
def analyze(ctx, demo_path, player, steamid, no_export, raw, raw_out, raw_stdout):
    """Analysiere eine CS2-Demo-Datei."""
    cfg = ctx.obj["config"]
    player_name = player or cfg.get("player_name", "")
    steam_id = steamid or cfg.get("steam_id", "")

    if not (raw or raw_out or raw_stdout):
        console.print(Panel(
            f"[bold cyan]CS2 Coach — Demo-Analyse[/bold cyan]\n"
            f"Demo: {demo_path}",
            border_style="cyan",
        ))

    with console.status("[bold green]Demo wird geparst...") if not (raw or raw_stdout) else _nullcontext():
        result = parse_demo(demo_path, player_name, steam_id)

    if raw or raw_out or raw_stdout:
        raw_data = _build_raw_json(result)
        json_str = json.dumps(raw_data, ensure_ascii=False, indent=2)

        if raw_stdout:
            sys.stdout.buffer.write(json_str.encode("utf-8"))
            sys.stdout.buffer.write(b"\n")
            return

        # Auto-Dateiname oder benutzerdefiniert
        if raw_out:
            out_path = Path(raw_out)
        else:
            vault_path = cfg.get("obsidian_vault_path", "")
            subfolder = cfg.get("coach_subfolder", "CS2-Coach")
            out_dir = Path(vault_path) / subfolder / "exports" if vault_path else Path(".")
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / _auto_filename(result)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json_str, encoding="utf-8")
        out_str = str(out_path).replace("\\", "/")
        console.print(
            f"[bold green]OK[/bold green] Raw-JSON exportiert: {out_str}\n"
            f"[dim]Datei direkt in Gemini/ChatGPT hochladen — Prompt ist eingebettet.[/dim]"
        )
        return

    console.print(f"\n[bold]Map:[/bold] {result.map_name}")
    console.print(
        f"[bold]Ergebnis:[/bold] {result.result_str} "
        f"({result.score_team1}:{result.score_team2})"
    )
    console.print(f"[bold]Spieler:[/bold] {result.player_stats.name}")

    _print_stats_table(result)

    report = generate_report(result)
    console.print(Panel(report, title="Coach-Report", border_style="yellow"))

    if not no_export:
        vault_path = cfg.get("obsidian_vault_path", "")
        subfolder = cfg.get("coach_subfolder", "CS2-Coach")

        if not vault_path:
            console.print("[red]Kein Obsidian-Vault-Pfad konfiguriert![/red]")
            return

        filepath = export_match(result, report, vault_path, subfolder)
        filepath_str = str(filepath).replace("\\", "/")
        console.print(
            f"\n[bold green]OK[/bold green] Obsidian-Notiz exportiert: "
            f"{filepath_str}"
        )
    else:
        console.print("\n[dim]Obsidian-Export übersprungen (--no-export)[/dim]")


@cli.command()
@click.argument("demo_dir", type=click.Path(exists=True))
@click.option("--player", "-p", default=None, help="Steam-Name des Spielers")
@click.option("--steamid", "-s", default=None, help="SteamID64 des Spielers")
@click.pass_context
def batch(ctx, demo_dir, player, steamid):
    """Analysiere alle .dem-Dateien in einem Ordner und exportiere Raw-JSON."""
    cfg = ctx.obj["config"]
    player_name = player or cfg.get("player_name", "")
    steam_id = steamid or cfg.get("steam_id", "")
    vault_path = cfg.get("obsidian_vault_path", "")
    subfolder = cfg.get("coach_subfolder", "CS2-Coach")

    demo_path = Path(demo_dir)
    dem_files = sorted(demo_path.glob("*.dem"))

    if not dem_files:
        console.print(f"[red]Keine .dem-Dateien gefunden in: {demo_dir}[/red]")
        return

    console.print(Panel(
        f"[bold cyan]CS2 Coach — Batch-Analyse[/bold cyan]\n"
        f"Ordner: {demo_dir}\n"
        f"Demos: {len(dem_files)}",
        border_style="cyan",
    ))

    out_dir = Path(vault_path) / subfolder / "exports" if vault_path else Path(".")
    out_dir.mkdir(parents=True, exist_ok=True)

    results_summary = []
    errors = []

    for i, dem in enumerate(dem_files, 1):
        console.print(f"\n[bold][{i}/{len(dem_files)}][/bold] {dem.name}")
        try:
            result = parse_demo(str(dem), player_name, steam_id)

            # Raw-JSON exportieren
            raw_data = _build_raw_json(result)
            json_str = json.dumps(raw_data, ensure_ascii=False, indent=2)
            out_file = out_dir / _auto_filename(result)
            out_file.write_text(json_str, encoding="utf-8")

            # Obsidian-Notiz exportieren
            if vault_path:
                report = generate_report(result)
                export_match(result, report, vault_path, subfolder)

            s = result.player_stats
            results_summary.append({
                "file": dem.name,
                "map": result.map_name,
                "score": f"{result.score_team1}:{result.score_team2}",
                "result": result.result_str,
                "kd": s.kd_ratio,
                "adr": s.adr,
                "rating": result.rating,
                "kast": s.kast_pct,
                "rank_change": s.rank_change if s.rank_old > 0 else None,
                "json": out_file.name,
            })

            console.print(
                f"  {result.map_name} {result.score_team1}:{result.score_team2} "
                f"({result.result_str}) | "
                f"K/D {s.kills}/{s.deaths} | ADR {s.adr:.0f} | "
                f"Rating {result.rating} | KAST {s.kast_pct:.0f}%"
            )

        except Exception as e:
            errors.append((dem.name, str(e)))
            console.print(f"  [red]FEHLER: {e}[/red]")

    # Summary-Tabelle
    console.print("\n")
    summary_table = Table(title=f"Batch-Zusammenfassung ({len(results_summary)}/{len(dem_files)} Demos)", border_style="green")
    summary_table.add_column("Map", style="cyan")
    summary_table.add_column("Score")
    summary_table.add_column("K/D")
    summary_table.add_column("ADR")
    summary_table.add_column("KAST%")
    summary_table.add_column("Rating")
    summary_table.add_column("Rank")

    total_rating = 0
    total_kast = 0
    wins, losses = 0, 0

    for r in results_summary:
        total_rating += r["rating"]
        total_kast += r["kast"]
        if r["result"] == "Sieg":
            wins += 1
        elif r["result"] == "Niederlage":
            losses += 1

        rank_str = f"{r['rank_change']:+.0f}" if r["rank_change"] is not None else "-"
        result_color = "green" if r["result"] == "Sieg" else ("red" if r["result"] == "Niederlage" else "yellow")
        summary_table.add_row(
            r["map"],
            f"[{result_color}]{r['score']}[/{result_color}]",
            f"{r['kd']:.2f}",
            f"{r['adr']:.0f}",
            f"{r['kast']:.0f}%",
            f"{r['rating']}",
            rank_str,
        )

    n = max(len(results_summary), 1)
    summary_table.add_row(
        f"[bold]AVG ({wins}W/{losses}L)[/bold]", "",
        "", "",
        f"[bold]{total_kast/n:.0f}%[/bold]",
        f"[bold]{total_rating/n:.2f}[/bold]",
        "",
    )

    console.print(summary_table)

    # Export-Pfad anzeigen
    out_str = str(out_dir).replace("\\", "/")
    console.print(f"\n[bold green]OK[/bold green] {len(results_summary)} Raw-JSON-Dateien exportiert nach: {out_str}")

    if errors:
        console.print(f"[yellow]{len(errors)} Fehler:[/yellow]")
        for name, err in errors:
            console.print(f"  {name}: {err}")

    # Graphify Indexierung
    if vault_path:
        console.print("\n[bold]Graphify-Indexierung...[/bold]")
        try:
            stats = index_vault(vault_path, subfolder)
            console.print(
                f"[bold green]OK[/bold green] Graph: "
                f"{stats['nodes']} Knoten, {stats['edges']} Kanten, "
                f"{stats['clusters']} Cluster"
            )
        except Exception as e:
            console.print(f"[yellow]Graphify: {e}[/yellow]")


@cli.command()
@click.pass_context
def graph(ctx):
    """Indexiere den CS2-Coach-Vault als Wissensgraph mit Graphify."""
    cfg = ctx.obj["config"]
    vault_path = cfg.get("obsidian_vault_path", "")
    subfolder = cfg.get("coach_subfolder", "CS2-Coach")

    if not vault_path:
        console.print("[red]Kein Obsidian-Vault-Pfad konfiguriert![/red]")
        return

    console.print(Panel(
        "[bold cyan]CS2 Coach — Graphify Indexierung[/bold cyan]\n"
        f"Vault: {vault_path}/{subfolder}",
        border_style="cyan",
    ))

    with console.status("[bold green]Vault wird indexiert..."):
        try:
            stats = index_vault(vault_path, subfolder)
        except FileNotFoundError as e:
            console.print(f"[red]{e}[/red]")
            console.print("Führe zuerst eine Demo-Analyse durch, um den Coach-Ordner zu erstellen.")
            return

    table = Table(title="Graph-Statistiken", border_style="green")
    table.add_column("Metrik", style="cyan")
    table.add_column("Wert", style="white")
    table.add_row("Dateien indexiert", str(stats["files"]))
    table.add_row("Knoten", str(stats["nodes"]))
    table.add_row("Kanten", str(stats["edges"]))
    table.add_row("Cluster", str(stats["clusters"]))
    console.print(table)

    analysis = stats.get("analysis", {})
    if analysis.get("god_nodes"):
        console.print("\n[bold]Zentrale Konzepte:[/bold]")
        for node in analysis["god_nodes"]:
            name = node.get("label", node.get("node", node.get("name", "?")))
            degree = node.get("degree", "?")
            console.print(f"  [cyan]{name}[/cyan] ({degree} Verbindungen)")

    console.print(
        f"\n[bold green]OK[/bold green] Graph exportiert nach "
        f"{vault_path}/{subfolder}/_graph/"
    )
    console.print("  - cs2_graph.json (Daten)")
    console.print("  - cs2_graph.html (Visualisierung)")
    console.print("  - cs2_graph.canvas (Obsidian Canvas)")
    console.print("  - Graph-Analyse.md (Zusammenfassung)")


@cli.command()
@click.pass_context
def setup(ctx):
    """Prüfe und zeige die aktuelle Konfiguration."""
    cfg = ctx.obj["config"]
    vault_path = cfg.get("obsidian_vault_path", "")
    subfolder = cfg.get("coach_subfolder", "CS2-Coach")

    console.print(Panel(
        "[bold cyan]CS2 Coach — Setup-Check[/bold cyan]",
        border_style="cyan",
    ))

    if vault_path and Path(vault_path).exists():
        console.print(f"[green]✓[/green] Vault-Pfad: {vault_path}")
    else:
        console.print(f"[red]✗[/red] Vault-Pfad: {vault_path or '(nicht gesetzt)'}")

    coach_dir = Path(vault_path) / subfolder if vault_path else None
    if coach_dir and coach_dir.exists():
        console.print(f"[green]✓[/green] Coach-Ordner: {coach_dir}")
    else:
        console.print(f"[yellow]○[/yellow] Coach-Ordner wird beim ersten Export erstellt")

    console.print(f"\n[bold]Spieler:[/bold] {cfg.get('player_name', '(nicht gesetzt)')}")
    console.print(f"[bold]SteamID:[/bold] {cfg.get('steam_id', '(nicht gesetzt)')}")


class _nullcontext:
    def __enter__(self): return self
    def __exit__(self, *_): pass


COACH_PROMPT = """\
Du bist ein erfahrener CS2-Coach und Analyst. Analysiere die folgenden Match-Daten \
aus einer CS2-Competitive-Demo. Der Spieler mit "is_target": true ist der Spieler, \
den du coachen sollst.

Deine Analyse soll auf Deutsch sein und folgende Punkte abdecken:

1. **Gesamtbewertung**: War der Spieler Carry, Mitläufer oder Belastung? \
Kontext: Score, Rating, Scoreboard-Platzierung.

2. **Mechanik-Analyse**:
   - Counter-Strafing (counter_strafe_pct + avg_inaccuracy_move): \
Unter 80% = Arena-Shooter-Gewohnheiten. Über 90% = sauber. inaccuracy_move > 0.01 = Problem.
   - Spray-Control (avg_recoil_index): Unter 3 = gute Burst-Disziplin. \
Über 5 = zu tiefes Sprayen, Pattern bricht auseinander.
   - Headshot-Rate (hs_pct): Crosshair-Placement-Indikator.
   - AWP vs Rifle vs Pistol Split: Passt die Waffenwahl zur Rolle?

3. **Taktische Analyse**:
   - Opening Duels: Gewinnt der Spieler Eröffnungsduelle?
   - Trade Kills: Unter 10% der Kills = zu isoliert.
   - K/D vs ADR Diskrepanz: Hohes ADR + niedriges K/D = stirbt ohne Kill zu sichern. \
Niedriges ADR + positives K/D = Eco-/Exit-Fragger.
   - Engagement-Distanz: Passt die Kampfdistanz zur Waffenwahl?

4. **Utility-Analyse**:
   - Utility pro Runde (utility.per_round): Unter 1.5 = Deathmatch-Mentalität.
   - Flash-Effektivität: Mehr Team-Blinds als Gegner-Blinds = schädlich. \
avg_enemy_duration unter 1.5s = Flashes zu schwach.
   - Smoke/Molotov/HE-Verteilung.

5. **Clutch-Analyse**: Versuche vs Siege. Was sagt das über Drucksituationen?

6. **Kill-Context**: Besondere Kills (thru smoke, wallbang, noscope, while blind) — \
Highlight oder Glück?

7. **Rank-Einordnung**: Wenn rank-Daten vorhanden: Wie passt die Performance zum Elo?

8. **Vergleich mit dem Scoreboard**: Alle 10 Spieler vergleichen, nicht nur den Zielspieler.

9. **Konkrete Fehler & Verbesserungen**: 3-5 trainierbare Schwachstellen mit \
Workshop-Maps, Routinen, Mindset-Tipps.

10. **Prioritäts-Ranking**: Was hat den höchsten Impact auf die nächsten Matches?

Sei direkt und ehrlich — kein Sugarcoating. Der Spieler kommt aus einem \
Arena-Shooter-Background (Quake/UT) und kämpft mit taktischer Disziplin vs. \
mechanischem Instinkt. Berücksichtige das in deinem Feedback.\
"""


def _auto_filename(result: MatchResult) -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    time = datetime.now().strftime("%H%M")
    map_name = result.map_name.lower()
    score = f"{result.score_team1}-{result.score_team2}"
    return f"{date}_{map_name}_{score}_{time}_coach.json"


def _build_raw_json(result: MatchResult) -> dict:
    s = result.player_stats
    scoreboard = []
    for p in sorted(result.all_players, key=lambda x: x.kills, reverse=True):
        scoreboard.append(_player_to_dict(p, p.steam_id == s.steam_id))

    return {
        "prompt": COACH_PROMPT,
        "match": {
            "map": result.map_name,
            "score_own": result.score_team1,
            "score_enemy": result.score_team2,
            "total_rounds": result.total_rounds,
            "result": result.result_str,
        },
        "player": _player_to_dict(s, True, include_rating=result.rating),
        "scoreboard": scoreboard,
    }


def _player_to_dict(p, is_target: bool, include_rating: float | None = None) -> dict:
    util_total = p.flashes_thrown + p.smokes_thrown + p.he_thrown + p.molotovs_thrown
    d = {
        "name": p.name,
        "steam_id": p.steam_id,
        "team": p.team,
        "kills": p.kills,
        "deaths": p.deaths,
        "assists": p.assists,
        "kd": round(p.kd_ratio, 2),
        "adr": round(p.adr, 1),
        "hs_pct": round(p.headshot_pct, 1),
        "opening_kills": p.opening_kills,
        "opening_deaths": p.opening_deaths,
        "trade_kills": p.trade_kills,
        "counter_strafe_pct": round(p.counter_strafe_score, 1),
        "avg_inaccuracy_move": round(p.avg_inaccuracy_move, 5),
        "spray_control": {
            "burst_kills": p.burst_kills,
            "spray_kills": p.spray_kills,
            "avg_recoil_index": round(p.avg_recoil_index, 2),
        },
        "weapons": {
            "awp_kills": p.awp_kills,
            "rifle_kills": p.rifle_kills,
            "pistol_kills": p.pistol_kills,
        },
        "engagement_distance": {
            "avg": round(p.avg_fight_distance, 0),
            "close": p.close_range_kills,
            "mid": p.mid_range_kills,
            "long": p.long_range_kills,
        },
        "utility": {
            "total": util_total,
            "per_round": round(p.utility_per_round, 2),
            "flashes": p.flashes_thrown,
            "smokes": p.smokes_thrown,
            "he": p.he_thrown,
            "molotovs": p.molotovs_thrown,
        },
        "flash_effectiveness": {
            "enemies_blinded": p.flash_enemies_blinded,
            "teammates_blinded": p.flash_teammates_blinded,
            "avg_enemy_duration": round(p.flash_avg_enemy_duration, 2),
        },
        "clutches": {
            "attempts": p.clutch_attempts,
            "wins": p.clutch_wins,
            "kills": p.clutch_kills,
        },
        "kill_context": {
            "thru_smoke": p.kills_thru_smoke,
            "while_blind": p.kills_while_blind,
            "noscope": p.kills_noscope,
            "wallbang": p.kills_penetrated,
            "airborne": p.kills_airborne,
        },
        "kast_pct": round(p.kast_pct, 1),
        "survival_rate": round(p.survival_rate, 1),
        "accuracy": round(p.accuracy, 1),
        "multikills": {
            "2k": p.rounds_2k, "3k": p.rounds_3k,
            "4k": p.rounds_4k, "5k": p.rounds_5k,
        },
        "side_split": {
            "ct_kills": p.ct_kills, "ct_deaths": p.ct_deaths,
            "t_kills": p.t_kills, "t_deaths": p.t_deaths,
        },
        "death_timing": {
            "early": p.deaths_early,
            "mid": p.deaths_mid,
            "late": p.deaths_late,
        },
        "is_target": is_target,
    }
    if p.rank_old > 0 or p.rank_new > 0:
        d["rank"] = {
            "old": p.rank_old,
            "new": p.rank_new,
            "change": round(p.rank_change, 0),
            "total_wins": p.rank_wins,
        }
    if include_rating is not None:
        d["rating"] = include_rating
    return d


def _print_stats_table(result):
    table = Table(title="Match-Statistiken", border_style="blue")
    table.add_column("Metrik", style="cyan")
    table.add_column("Wert", style="white")

    s = result.player_stats
    table.add_row("K/D/A", f"{s.kills}/{s.deaths}/{s.assists}")
    table.add_row("K/D Ratio", f"{s.kd_ratio:.2f}")
    table.add_row("ADR", f"{s.adr:.1f}")
    table.add_row("HS%", f"{s.headshot_pct:.0f}%")
    table.add_row("Opening Duels", f"{s.opening_kills}W / {s.opening_deaths}L ({s.opening_duel_rating})")
    table.add_row("Trade Kills", str(s.trade_kills))
    table.add_row("Counter-Strafe", f"{s.counter_strafe_score:.0f}% (inacc: {s.avg_inaccuracy_move:.4f})")
    table.add_row("Spray-Control", f"{s.burst_spray_ratio} (avg recoil: {s.avg_recoil_index:.1f})")
    table.add_row("Waffen-Split", s.awp_rifle_split)
    table.add_row("Kampfdistanz", f"avg {s.avg_fight_distance:.0f}u (N:{s.close_range_kills}/M:{s.mid_range_kills}/W:{s.long_range_kills})")

    util_total = s.flashes_thrown + s.smokes_thrown + s.he_thrown + s.molotovs_thrown
    table.add_row("Utility", f"{util_total} ({s.utility_per_round:.1f}/Runde)")
    table.add_row("Flashes", f"{s.flashes_thrown} ({s.flash_effectiveness})")
    if s.clutch_attempts > 0:
        table.add_row("Clutches", s.clutch_rate)
    table.add_row("KAST%", f"{s.kast_pct:.0f}%")
    table.add_row("Survival", f"{s.survival_rate:.0f}%")
    table.add_row("Accuracy", f"{s.accuracy:.1f}%")
    if s.rounds_2k + s.rounds_3k + s.rounds_4k + s.rounds_5k > 0:
        table.add_row("Multi-Kills", s.multikill_str)
    table.add_row("CT/T-Split", s.ct_t_split)
    table.add_row("Death-Timing", s.death_timing_str)
    if s.rank_old > 0:
        change = f"+{s.rank_change:.0f}" if s.rank_change >= 0 else f"{s.rank_change:.0f}"
        table.add_row("Rank", f"{s.rank_old} -> {s.rank_new} ({change})")
    table.add_row("Rating", f"{result.rating}")

    console.print(table)


def main():
    cli(obj={})


if __name__ == "__main__":
    main()
