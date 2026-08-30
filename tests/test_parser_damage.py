"""Tests fuer die Schadensberechnung (Grundlage des ADR).

player_hurt.dmg_health meldet den rohen Waffenschaden, nicht die
tatsaechlich entfernte Gesundheit. Ungedeckelt summiert ergab das einen um
rund 30 Prozent zu hohen ADR - gemessen ueber sechs echte Demos, mit
Ausreissern bis 47 Prozent und einem Einzeltreffer von 438 Schaden.
"""

from __future__ import annotations

import pandas as pd
import pytest

from cs2_coach.parser import PlayerStats, _process_damage


def hurt(tick, attacker, victim, dmg):
    return {
        "tick": tick,
        "attacker_steamid": attacker,
        "user_steamid": victim,
        "dmg_health": dmg,
    }


def lookup(*sids) -> dict[str, PlayerStats]:
    return {s: PlayerStats(name=f"p{s}", steam_id=s, team="") for s in sids}


# Runde 1 endet bei 1000, Runde 2 bei 2000, ...
ROUND_TICKS = [1000, 2000, 3000]


def run(rows, players, ticks=ROUND_TICKS):
    _process_damage(pd.DataFrame(rows), ticks, players)
    return players


def test_normal_damage_is_summed():
    p = run([hurt(100, "A", "B", 30), hurt(200, "A", "B", 25)], lookup("A", "B"))
    assert p["A"].damage == 55


def test_lethal_hit_is_capped_at_remaining_health():
    """Kern des Fehlers: 108 Schaden auf ein Opfer mit 100 HP sind 100."""
    p = run([hurt(100, "A", "B", 108)], lookup("A", "B"))
    assert p["A"].damage == 100


def test_explosion_overkill_is_capped():
    """Realer Fall aus einer Demo: ein Treffer mit dmg_health 438."""
    p = run([hurt(100, "A", "B", 438)], lookup("A", "B"))
    assert p["A"].damage == 100


def test_cap_uses_remaining_health_not_just_hundred():
    """Nach 80 Schaden bleiben 20 HP - ein 108er-Treffer zaehlt nur 20."""
    p = run([hurt(100, "A", "B", 80), hurt(200, "A", "B", 108)], lookup("A", "B"))
    assert p["A"].damage == 100


def test_damage_from_two_attackers_shares_one_health_pool():
    """B hat 100 HP. A macht 60, C toetet mit 108 -> C bekommt 40."""
    players = lookup("A", "B", "C")
    run([hurt(100, "A", "B", 60), hurt(200, "C", "B", 108)], players)
    assert players["A"].damage == 60
    assert players["C"].damage == 40


def test_health_resets_each_round():
    """Gleiches Opfer, neue Runde: die HP stehen wieder auf 100."""
    p = run([
        hurt(100, "A", "B", 100),    # Runde 1, toedlich
        hurt(1500, "A", "B", 100),   # Runde 2, wieder volle HP
    ], lookup("A", "B"))
    assert p["A"].damage == 200


def test_damage_beyond_death_in_same_round_is_ignored():
    p = run([
        hurt(100, "A", "B", 100),
        hurt(200, "A", "B", 50),   # B ist bereits tot
    ], lookup("A", "B"))
    assert p["A"].damage == 100


def test_self_damage_is_not_credited():
    """Fallschaden oder eigene Granate ist keine Leistung."""
    p = run([hurt(100, "A", "A", 40)], lookup("A"))
    assert p["A"].damage == 0


def test_self_damage_still_reduces_own_health_pool():
    """A springt sich auf 60 HP; B toetet ihn und bekommt nur 60."""
    players = lookup("A", "B")
    run([hurt(100, "A", "A", 40), hurt(200, "B", "A", 108)], players)
    assert players["A"].damage == 0
    assert players["B"].damage == 60


def test_events_are_processed_in_tick_order():
    """Unsortierte Eingabe darf das Ergebnis nicht veraendern."""
    rows = [hurt(200, "A", "B", 108), hurt(100, "A", "B", 80)]
    p = run(rows, lookup("A", "B"))
    assert p["A"].damage == 100


def test_zero_and_negative_damage_ignored():
    p = run([hurt(100, "A", "B", 0), hurt(200, "A", "B", -5)], lookup("A", "B"))
    assert p["A"].damage == 0


def test_unknown_attacker_does_not_crash_but_still_consumes_health():
    """Schaden eines nicht getrackten Spielers zaehlt trotzdem gegen die HP."""
    players = lookup("B")
    run([hurt(100, "UNBEKANNT", "B", 70), hurt(200, "B", "B", 0)], players)
    # B hat nur noch 30 HP - das prueft der naechste Treffer
    players2 = lookup("A", "B")
    run([hurt(100, "X", "B", 70), hurt(200, "A", "B", 108)], players2)
    assert players2["A"].damage == 30


def test_empty_frame_is_survivable():
    players = lookup("A")
    _process_damage(pd.DataFrame(columns=["tick", "attacker_steamid", "user_steamid", "dmg_health"]),
                    ROUND_TICKS, players)
    assert players["A"].damage == 0


def test_adr_property_uses_capped_damage():
    players = lookup("A", "B")
    run([hurt(100, "A", "B", 438)], players)
    players["A"].rounds_played = 10
    assert players["A"].adr == pytest.approx(10.0)
