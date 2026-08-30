"""Tests fuer die Counter-Strafing-Messung.

Frueher wurde die Quote aus bullet_damage.inaccuracy_move bestimmt. Dieses
Event feuert aber nur bei Treffern, und Schuesse aus der Bewegung gehen
ueberdurchschnittlich oft daneben. In einer echten Demo waren dadurch 81
Prozent aller Schuesse unsichtbar und die Quote fiel bis zu 17 Punkte zu
gut aus.

Jetzt zaehlt jeder Schuss aus weapon_fire; die Geschwindigkeit wird aus
dem Positionsdelta zum Vortick rekonstruiert.
"""

from __future__ import annotations

import pandas as pd
import pytest

from cs2_coach.parser import (
    COUNTER_STRAFE_SPEED_THRESHOLD,
    PlayerStats,
    _TICKRATE,
    _process_counter_strafe,
)


class FakeParser:
    """Liefert weapon_fire-Events und Positionen je Tick."""

    def __init__(self, shots, positions, tick_fail=False):
        self._shots = shots
        self._positions = positions
        self._tick_fail = tick_fail

    def parse_events(self, names, **kw):
        """_get_event_df() nutzt parse_events und erwartet [(name, df)]."""
        if "weapon_fire" not in names:
            return []
        return [("weapon_fire", pd.DataFrame(self._shots))]

    def parse_ticks(self, props, ticks=None):
        if self._tick_fail:
            raise RuntimeError("keine Tickdaten")
        rows = [r for r in self._positions if ticks is None or r["tick"] in ticks]
        return pd.DataFrame(rows, columns=["tick", "steamid", "X", "Y"])


def shot(tick, sid, weapon="ak47"):
    return {"tick": tick, "user_steamid": sid, "weapon": weapon}


def at(tick, sid, x, y):
    return {"tick": tick, "steamid": sid, "X": x, "Y": y}


def lookup(*sids):
    return {s: PlayerStats(name=f"p{s}", steam_id=s, team="") for s in sids}


# Distanz pro Tick, die genau der Schwelle entspricht
STEP_AT_THRESHOLD = COUNTER_STRAFE_SPEED_THRESHOLD / _TICKRATE


def test_standing_shot_counts_as_standing():
    players = lookup("A")
    _process_counter_strafe(
        FakeParser([shot(100, "A")], [at(99, "A", 0, 0), at(100, "A", 0, 0)]),
        players,
    )
    assert players["A"].shots_standing == 1
    assert players["A"].shots_running == 0


def test_fast_movement_counts_as_running():
    """Deutlich ueber der Schwelle bewegt."""
    players = lookup("A")
    far = STEP_AT_THRESHOLD * 3
    _process_counter_strafe(
        FakeParser([shot(100, "A")], [at(99, "A", 0, 0), at(100, "A", far, 0)]),
        players,
    )
    assert players["A"].shots_running == 1
    assert players["A"].shots_standing == 0


def test_threshold_is_inclusive():
    """Genau auf der Schwelle gilt noch als stehend."""
    players = lookup("A")
    _process_counter_strafe(
        FakeParser([shot(100, "A")],
                   [at(99, "A", 0, 0), at(100, "A", STEP_AT_THRESHOLD, 0)]),
        players,
    )
    assert players["A"].shots_standing == 1


def test_misses_are_counted_too():
    """Kern der Korrektur: auch Schuesse ohne Treffer zaehlen.

    Drei Schuesse in voller Bewegung, kein einziger Treffer noetig.
    """
    players = lookup("A")
    far = STEP_AT_THRESHOLD * 4
    shots = [shot(t, "A") for t in (100, 200, 300)]
    pos = []
    for t in (100, 200, 300):
        pos += [at(t - 1, "A", 0, 0), at(t, "A", far, 0)]
    _process_counter_strafe(FakeParser(shots, pos), players)

    assert players["A"].shots_running == 3
    assert players["A"].counter_strafe_score == 0.0


def test_grenades_and_knife_are_ignored():
    """Nur Schusswaffen sind Aim-Duelle."""
    players = lookup("A")
    far = STEP_AT_THRESHOLD * 4
    shots = [shot(100, "A", "hegrenade"), shot(200, "A", "knife"),
             shot(300, "A", "flashbang"), shot(400, "A", "ak47")]
    pos = []
    for t in (100, 200, 300, 400):
        pos += [at(t - 1, "A", 0, 0), at(t, "A", far, 0)]
    _process_counter_strafe(FakeParser(shots, pos), players)

    assert players["A"].shots_running == 1, "nur der ak47-Schuss zaehlt"


def test_multiple_players_are_kept_apart():
    players = lookup("A", "B")
    far = STEP_AT_THRESHOLD * 4
    shots = [shot(100, "A"), shot(100, "B")]
    pos = [at(99, "A", 0, 0), at(100, "A", 0, 0),
           at(99, "B", 0, 0), at(100, "B", far, 0)]
    _process_counter_strafe(FakeParser(shots, pos), players)

    assert players["A"].shots_standing == 1 and players["A"].shots_running == 0
    assert players["B"].shots_running == 1 and players["B"].shots_standing == 0


def test_unknown_player_is_ignored():
    players = lookup("A")
    _process_counter_strafe(
        FakeParser([shot(100, "FREMD")], [at(99, "FREMD", 0, 0), at(100, "FREMD", 0, 0)]),
        players,
    )
    assert players["A"].shots_standing == 0


def test_missing_previous_tick_is_skipped():
    """Ohne Vortick laesst sich keine Geschwindigkeit bilden."""
    players = lookup("A")
    _process_counter_strafe(
        FakeParser([shot(100, "A")], [at(100, "A", 0, 0)]),
        players,
    )
    assert players["A"].shots_standing == 0
    assert players["A"].shots_running == 0


def test_tick_parsing_failure_is_survivable():
    players = lookup("A")
    _process_counter_strafe(
        FakeParser([shot(100, "A")], [], tick_fail=True), players
    )
    assert players["A"].shots_standing == 0


def test_no_shots_is_survivable():
    players = lookup("A")
    _process_counter_strafe(FakeParser([], []), players)
    assert players["A"].counter_strafe_score == 100.0


def test_score_mixes_both_counters():
    players = lookup("A")
    far = STEP_AT_THRESHOLD * 4
    shots = [shot(t, "A") for t in (100, 200, 300, 400)]
    pos = []
    for i, t in enumerate((100, 200, 300, 400)):
        moved = far if i < 1 else 0     # 1 von 4 in Bewegung
        pos += [at(t - 1, "A", 0, 0), at(t, "A", moved, 0)]
    _process_counter_strafe(FakeParser(shots, pos), players)

    assert players["A"].shots_standing == 3
    assert players["A"].shots_running == 1
    assert players["A"].counter_strafe_score == pytest.approx(75.0)


# ── Accuracy ────────────────────────────────────────────────────────────

def test_non_gun_weapons_excluded_from_shot_list():
    """Granaten und Messer duerfen den Accuracy-Nenner nicht aufblaehen.

    Gemessen ueber fuenf Demos: 28 bis 50 Wuerfe pro Match, was die
    Accuracy um rund zwoelf Prozent relativ zu schlecht aussehen liess.
    """
    from cs2_coach.parser import _NON_GUN_WEAPONS
    for w in ("weapon_hegrenade", "weapon_flashbang", "weapon_smokegrenade",
              "weapon_molotov", "weapon_incgrenade", "weapon_knife_skeleton",
              "weapon_taser", "weapon_decoy"):
        assert any(frag in w for frag in _NON_GUN_WEAPONS), w


def test_gun_weapons_survive_the_filter():
    from cs2_coach.parser import _NON_GUN_WEAPONS
    for w in ("weapon_ak47", "weapon_m4a1", "weapon_awp", "weapon_deagle",
              "weapon_usp_silencer", "weapon_mp9", "weapon_famas"):
        assert not any(frag in w for frag in _NON_GUN_WEAPONS), w
