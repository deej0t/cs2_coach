"""Tests fuer den Export der Wurfpositionen.

Der Parser ermittelt bei jeder Analyse, wo jede Granate detoniert ist.
Der Export hat diese Daten bisher verworfen - und weil die Analyse
inzwischen auf die Export-Detailseite umleitet, waren sie ueberhaupt
nicht mehr erreichbar. Sie beantworten, WO Utility geworfen wird, nicht
nur wie viel.
"""

from __future__ import annotations

import pytest

from cs2_coach.obsidian import _UTIL_SHORT, _compact_utility_positions


def up(kind, pid, x=100.0, y=200.0, rnd=1):
    return {"type": kind, "player_id": pid, "player_name": "p",
            "x": x, "y": y, "round": rnd}


def test_only_target_player_is_exported():
    """Wie bei den Kill-Positionen: sonst waere der Export ein Vielfaches."""
    pos = [up("smoke", "A"), up("he", "B"), up("flash", "A")]
    out = _compact_utility_positions(pos, "A")
    assert len(out) == 2
    assert {o["t"] for o in out} == {"s", "f"}


def test_all_four_types_map_to_short_codes():
    pos = [up(k, "A") for k in ("flash", "smoke", "he", "molotov")]
    out = _compact_utility_positions(pos, "A")
    assert [o["t"] for o in out] == ["f", "s", "h", "m"]
    assert set(_UTIL_SHORT.values()) == {"f", "s", "h", "m"}


def test_unknown_type_is_dropped_not_guessed():
    out = _compact_utility_positions([up("decoy", "A")], "A")
    assert out == []


def test_coordinates_are_rounded_but_signed():
    out = _compact_utility_positions([up("he", "A", x=1.267, y=-2.345)], "A")
    assert out[0]["x"] == 1.3
    assert out[0]["y"] == -2.3


def test_round_is_preserved():
    out = _compact_utility_positions([up("he", "A", rnd=17)], "A")
    assert out[0]["r"] == 17


def test_numeric_and_string_ids_both_match():
    """steam_id kann als Zahl oder String vorliegen."""
    assert len(_compact_utility_positions([up("he", 123)], "123")) == 1
    assert len(_compact_utility_positions([up("he", "123")], 123)) == 1


def test_empty_input_is_survivable():
    assert _compact_utility_positions([], "A") == []


def test_entry_stays_compact():
    """Nur vier Felder - der Export waechst sonst unnoetig."""
    out = _compact_utility_positions([up("smoke", "A")], "A")
    assert set(out[0]) == {"t", "x", "y", "r"}


# ── Aggregation ueber alle Matches ──────────────────────────────────────

def build(tmp_path, matches):
    import json
    d = tmp_path / "CS2-Coach" / "exports"
    d.mkdir(parents=True)
    for i, (result, ups) in enumerate(matches):
        (d / f"2026-08-{i+1:02d}_mirage_13-6_2000_coach.json").write_text(
            json.dumps({
                "match": {"map": "Mirage", "result": result, "date": f"2026-08-{i+1:02d}"},
                "player": {"name": "deej0t"},
                "utility_positions": ups,
            }), encoding="utf-8")
    return {"obsidian_vault_path": str(tmp_path), "coach_subfolder": "CS2-Coach"}


def test_aggregates_per_map(tmp_path):
    from cs2_coach.web.app import _build_utility_map_data
    cfg = build(tmp_path, [
        ("Sieg", [{"t": "s", "x": -2000, "y": 500, "r": 1}]),
        ("Niederlage", [{"t": "f", "x": -2000, "y": 500, "r": 2}]),
    ])
    d = _build_utility_map_data(cfg)
    assert d["has_data"]
    m = d["maps"][0]
    assert m["matches"] == 2 and m["wins"] == 1 and m["losses"] == 1


def test_outcome_is_carried_per_dot(tmp_path):
    """Ohne das laesst sich Sieg und Niederlage nicht trennen."""
    from cs2_coach.web.app import _build_utility_map_data
    cfg = build(tmp_path, [
        ("Sieg", [{"t": "s", "x": -2000, "y": 500, "r": 1}]),
        ("Niederlage", [{"t": "s", "x": -2000, "y": 500, "r": 1}]),
    ])
    dots = _build_utility_map_data(cfg)["maps"][0]["dots"]
    assert sorted(d["won"] for d in dots) == [False, True]


def test_old_exports_without_positions_are_counted(tmp_path):
    """Aeltere Exports kennen das Feld nicht - das muss sichtbar sein."""
    import json
    from cs2_coach.web.app import _build_utility_map_data
    cfg = build(tmp_path, [("Sieg", [{"t": "s", "x": -2000, "y": 500, "r": 1}])])
    d = tmp_path / "CS2-Coach" / "exports"
    (d / "2026-07-01_mirage_13-6_2000_coach.json").write_text(
        json.dumps({"match": {"map": "Mirage", "result": "Sieg"}, "player": {}}),
        encoding="utf-8")
    assert _build_utility_map_data(cfg)["matches_without_data"] == 1


def test_unknown_map_is_skipped(tmp_path):
    from cs2_coach.web.app import _build_utility_map_data
    import json
    d = tmp_path / "CS2-Coach" / "exports"
    d.mkdir(parents=True)
    (d / "x_coach.json").write_text(json.dumps({
        "match": {"map": "de_phantasie", "result": "Sieg"},
        "player": {}, "utility_positions": [{"t": "s", "x": 0, "y": 0, "r": 1}],
    }), encoding="utf-8")
    cfg = {"obsidian_vault_path": str(tmp_path), "coach_subfolder": "CS2-Coach"}
    assert _build_utility_map_data(cfg)["maps"] == []


def test_no_vault_is_survivable():
    from cs2_coach.web.app import _build_utility_map_data
    assert _build_utility_map_data({})["has_data"] is False
