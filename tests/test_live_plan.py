"""Match-Plan fuer den Live-Coach (cs2_coach/live_plan.py)."""

from __future__ import annotations

import json

from cs2_coach import live_plan


def test_callout_namen_lesbar():
    assert live_plan.pretty_place("BombsiteA") == "Bombsite A"
    assert live_plan.pretty_place("TopofMid") == "Top of Mid"
    assert live_plan.pretty_place("CTSpawn") == "CT Spawn"
    assert live_plan.pretty_place("Connector") == "Connector"


def test_landung_bekommt_den_naechsten_callout():
    pts = [(0, 0, 0, "Connector"), (1000, 0, 0, "Jungle"), (0, 0, 300, "Window")]
    assert live_plan.nearest_place(pts, [80, 40, 0]) == "Connector"
    assert live_plan.nearest_place(pts, [0, 0, 260]) == "Window"         # Hoehe zaehlt doppelt
    assert live_plan.nearest_place(pts, [5000, 5000, 0]) == ""           # zu weit weg: kein Name


def test_vorschlag_passt_zu_seite_und_inventar():
    plan = {"sides": {"T": [{"type": "smoke", "label": "Smoke", "place": "Window", "mine": 30},
                            {"type": "molotov", "label": "Molly", "place": "Connector", "mine": 12}]}}
    tip = live_plan.suggest(plan, "T", {"weapon_molotov": 1, "weapon_flashbang": 1})
    assert tip["label"] == "Molly" and tip["place"] == "Connector"
    assert live_plan.suggest(plan, "CT", {"weapon_molotov": 1}) is None
    assert live_plan.suggest(plan, "T", {"weapon_hegrenade": 1}) is None


def test_plan_aus_eigenen_wuerfen(tmp_path):
    folder = tmp_path / "CS2-Coach" / "exports"
    folder.mkdir(parents=True)
    (folder / "2026-11-14_mirage_13-7_2100_coach.json").write_text(json.dumps({
        "match": {"map": "Mirage", "result": "Sieg", "score_own": 13, "score_enemy": 7, "datetime": "2026-11-14 21:00"},
        "kill_positions": [{"t": "k", "x": 10, "y": 10, "z": 0, "p": "Connector", "ex": 900, "ey": 0, "ez": 0, "ep": "Jungle"}],
        "round_timeline": [{"round": r, "buy": "fullbuy", "death_s": None} for r in range(1, 5)],
        "utility_positions": [{"t": "m", "r": 1, "s": 5.0}, {"t": "s", "r": 2, "s": 9.0}],
    }), encoding="utf-8")
    lib = [{"type": "molotov", "side": "T", "land": [0, 0, 0], "mine": 12},
           {"type": "smoke", "side": "T", "land": [880, 0, 0], "mine": 30},
           {"type": "flash", "side": "T", "land": [0, 0, 0], "mine": 0},          # nie von dir geworfen
           {"type": "smoke", "side": "CT", "land": [880, 0, 0], "mine": 4}]
    plan = live_plan.build({"obsidian_vault_path": str(tmp_path)}, tmp_path / "config.yaml", "de_mirage",
                           collect=lambda *a, **k: [], library=lambda throws, limit=0: lib)
    assert [x["label"] for x in plan["sides"]["T"]] == ["Smoke", "Molly"]          # haeufigste zuerst
    assert plan["sides"]["T"][0]["place"] == "Jungle" and plan["sides"]["T"][1]["place"] == "Connector"
    assert plan["quote"] == {"k": 2, "n": 4, "pct": 50}
    assert plan["last"]["score"] == "13:7" and plan["name"] == "Mirage"
