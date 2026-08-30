"""Tests fuer die Startseiten-Erkennung.

Diese Logik ist besonders fehleranfaellig und zugleich folgenreich: aus
PlayerStats.team leiten sich saemtliche CT/T-Auswertungen ab. Liegt sie
falsch, sind die Seiten-Insights vertauscht und sehen trotzdem plausibel
aus - der Nutzer trainiert dann das Falsche.

MR12: Seitenwechsel nach Runde 12. team_num stammt aus den Kill-Events
(2=T, 3=CT zum Zeitpunkt des Kills), NICHT aus parse_player_info().
"""

from __future__ import annotations

import pandas as pd
import pytest

from cs2_coach.parser import PlayerStats, _assign_starting_sides, _INGAME_CT, _INGAME_T


class FakeParser:
    """Minimaler DemoParser-Ersatz: liefert nur die Kill-Events."""

    def __init__(self, rows: list[dict], raises: bool = False):
        self._rows = rows
        self._raises = raises

    def parse_event(self, name, player=None, **kwargs):
        assert name == "player_death"
        if self._raises:
            raise RuntimeError("demo kaputt")
        return pd.DataFrame(self._rows)


def kill(tick, attacker, att_team, victim, vic_team):
    return {
        "tick": tick,
        "attacker_steamid": attacker,
        "attacker_team_num": att_team,
        "user_steamid": victim,
        "user_team_num": vic_team,
    }


def lookup(*steamids) -> dict[str, PlayerStats]:
    return {sid: PlayerStats(name=f"p{sid}", steam_id=sid, team="") for sid in steamids}


# Runde 12 endet bei Tick 12000; alles danach ist zweite Haelfte.
ROUND_TICKS = [i * 1000 for i in range(1, 25)]
HALF_TICK = ROUND_TICKS[11]


def test_starting_side_from_first_half_kill():
    players = lookup("A", "B")
    p = FakeParser([kill(500, "A", _INGAME_T, "B", _INGAME_CT)])

    _assign_starting_sides(p, players, ROUND_TICKS)

    assert players["A"].team == "T"
    assert players["B"].team == "CT"


def test_second_half_kills_do_not_override_first_half():
    """Nach dem Swap taucht derselbe Spieler mit der anderen team_num auf.

    Die Startseite muss die aus Haelfte 1 bleiben.
    """
    players = lookup("A", "B")
    p = FakeParser([
        kill(500, "A", _INGAME_T, "B", _INGAME_CT),          # Haelfte 1
        kill(HALF_TICK + 500, "A", _INGAME_CT, "B", _INGAME_T),  # nach Swap
    ])

    _assign_starting_sides(p, players, ROUND_TICKS)

    assert players["A"].team == "T", "Startseite darf nicht aus Haelfte 2 stammen"
    assert players["B"].team == "CT"


def test_player_without_first_half_kill_is_not_assigned_from_second_half():
    """Regression: Spieler ohne Kill/Death in Haelfte 1.

    Die Luecken-Fuellung darf die Seite nicht aus Post-Swap-Events ableiten,
    sonst kommt die invertierte Startseite heraus.

    C steht in Haelfte 2 auf team_num CT, war also in Haelfte 1 T.
    """
    players = lookup("A", "B", "C")
    p = FakeParser([
        kill(500, "A", _INGAME_T, "B", _INGAME_CT),
        # C taucht erstmals nach dem Swap auf, dort auf CT
        kill(HALF_TICK + 500, "C", _INGAME_CT, "B", _INGAME_T),
    ])

    _assign_starting_sides(p, players, ROUND_TICKS)

    assert players["A"].team == "T"
    assert players["B"].team == "CT"
    assert players["C"].team == "T", (
        "C war in Haelfte 2 auf CT, muss also als T gestartet sein"
    )


def test_teammate_propagation_within_first_half():
    """Spieler ohne eigenes Event erbt die Seite ueber ein Event der ersten Haelfte."""
    players = lookup("A", "B", "C")
    p = FakeParser([
        kill(500, "A", _INGAME_T, "B", _INGAME_CT),
        kill(900, "C", _INGAME_T, "B", _INGAME_CT),
    ])

    _assign_starting_sides(p, players, ROUND_TICKS)

    assert players["C"].team == "T"


def test_unknown_team_num_is_ignored():
    """team_num ausserhalb {2,3} darf keine Seite setzen."""
    players = lookup("A", "B")
    p = FakeParser([
        kill(500, "A", 0, "B", 1),          # Spectator/unassigned
        kill(700, "A", _INGAME_CT, "B", _INGAME_T),
    ])

    _assign_starting_sides(p, players, ROUND_TICKS)

    assert players["A"].team == "CT"
    assert players["B"].team == "T"


def test_nan_team_num_does_not_crash():
    players = lookup("A", "B")
    p = FakeParser([
        kill(500, "A", float("nan"), "B", float("nan")),
        kill(700, "A", _INGAME_T, "B", _INGAME_CT),
    ])

    _assign_starting_sides(p, players, ROUND_TICKS)

    assert players["A"].team == "T"


def test_parse_event_failure_is_survivable():
    """Kaputte Demo darf die gesamte Analyse nicht abbrechen."""
    players = lookup("A")
    before = players["A"].team
    _assign_starting_sides(FakeParser([], raises=True), players, ROUND_TICKS)
    assert players["A"].team == before


def test_empty_events_leave_players_untouched():
    players = lookup("A")
    before = players["A"].team
    _assign_starting_sides(FakeParser([]), players, ROUND_TICKS)
    assert players["A"].team == before


def test_short_match_without_half_boundary():
    """Weniger als 12 Runden: alle Events zaehlen als erste Haelfte."""
    players = lookup("A", "B")
    p = FakeParser([kill(500, "A", _INGAME_CT, "B", _INGAME_T)])

    _assign_starting_sides(p, players, [1000, 2000, 3000])

    assert players["A"].team == "CT"
    assert players["B"].team == "T"
