"""Tests fuer die Utility-Zaehlung.

Gezaehlt werden Detonations-Events, nicht Wuerfe. An fuenf echten Demos
gegengeprueft stimmt das bis auf hoechstens eine Granate je Match ueberein
- die Differenz sind Granaten, die nie detonierten, etwa weil der Spieler
beim Wurf starb. Diese nicht zu zaehlen ist richtig.

Wichtig fuer Molotovs: Feuer wird ueber inferno_startburn erfasst, und
dieses Event traegt entgegen der Erwartung eine Spielerzuordnung.
"""

from __future__ import annotations

import pandas as pd

from cs2_coach.parser import PlayerStats, _process_utility


class FakeParser:
    """Liefert die vier Detonations-Events."""

    def __init__(self, events: dict[str, list[dict]]):
        self._events = events

    def parse_events(self, names, **kw):
        name = names[0]
        rows = self._events.get(name)
        if rows is None:
            return []
        return [(name, pd.DataFrame(rows, columns=["user_steamid"]))]


def lookup(*sids):
    return {s: PlayerStats(name=f"p{s}", steam_id=s, team="") for s in sids}


def thrown_by(sid, n):
    return [{"user_steamid": sid} for _ in range(n)]


def test_each_grenade_type_lands_on_its_counter():
    players = lookup("A")
    _process_utility(FakeParser({
        "flashbang_detonate": thrown_by("A", 3),
        "smokegrenade_detonate": thrown_by("A", 2),
        "hegrenade_detonate": thrown_by("A", 4),
        "inferno_startburn": thrown_by("A", 1),
    }), players)

    a = players["A"]
    assert (a.flashes_thrown, a.smokes_thrown, a.he_thrown, a.molotovs_thrown) == (3, 2, 4, 1)


def test_utility_per_round_uses_all_four_types():
    players = lookup("A")
    _process_utility(FakeParser({
        "flashbang_detonate": thrown_by("A", 5),
        "smokegrenade_detonate": thrown_by("A", 5),
        "hegrenade_detonate": thrown_by("A", 5),
        "inferno_startburn": thrown_by("A", 5),
    }), players)
    players["A"].rounds_played = 20
    assert players["A"].utility_per_round == 1.0


def test_molotovs_are_attributed_via_inferno_startburn():
    """Regression: ohne Spielerzuordnung waeren alle Molotovs verloren."""
    players = lookup("A", "B")
    _process_utility(FakeParser({
        "inferno_startburn": thrown_by("A", 3) + thrown_by("B", 1),
    }), players)
    assert players["A"].molotovs_thrown == 3
    assert players["B"].molotovs_thrown == 1


def test_players_are_kept_apart():
    players = lookup("A", "B")
    _process_utility(FakeParser({
        "flashbang_detonate": thrown_by("A", 2) + thrown_by("B", 5),
    }), players)
    assert players["A"].flashes_thrown == 2
    assert players["B"].flashes_thrown == 5


def test_unknown_player_is_ignored():
    players = lookup("A")
    _process_utility(FakeParser({"hegrenade_detonate": thrown_by("FREMD", 4)}), players)
    assert players["A"].he_thrown == 0


def test_missing_events_are_survivable():
    players = lookup("A")
    _process_utility(FakeParser({}), players)
    assert players["A"].utility_per_round == 0.0


def test_partial_events_still_counted():
    """Eine Demo ohne Molotovs darf die uebrigen Zaehler nicht verhindern."""
    players = lookup("A")
    _process_utility(FakeParser({"flashbang_detonate": thrown_by("A", 2)}), players)
    assert players["A"].flashes_thrown == 2
    assert players["A"].molotovs_thrown == 0
