"""Teuerste Tode: Preis je eigenem Tod, Nachbesprechung, Reihenfolge im Server-Training."""

import json
from types import SimpleNamespace

from cs2_coach import costly_moments as cm
from cs2_coach import report_ai, training_server, winprob

STEM = "match730_x"


def _death(rnd, own, cost, enemy=5, planted=False, side="CT", place="Connector"):
    return {"sid": "me", "round": rnd, "own": own, "enemy": enemy, "planted": planted, "side": side,
            "second": 20.5, "cost": cost, "before": 51.0, "place": place, "demo": STEM}


def _export():
    kps = [{"t": "d", "r": r, "tk": 1000 * r, "p": "BombsiteB" if r == 3 else "Connector", "w": "ak47", "e": "foe"}
           for r in (1, 2, 3, 4)]
    kps.append({"t": "k", "r": 1, "tk": 990})
    return {"match": {"demo_file": STEM + ".dem", "map": "Mirage"}, "kill_positions": kps}


OWN = {(STEM, 1): _death(1, 5, 15.6), (STEM, 2): _death(2, 1, 62.0),          # letzter Mann
       (STEM, 3): _death(3, 3, 24.0, enemy=2, planted=True, side="T", place="BombsiteB"),
       (STEM, 4): _death(4, 2, 30.2)}


def test_for_match_ranks_by_price_without_last_man_deaths():
    ms = cm.for_match(_export(), OWN)
    assert [m["round"] for m in ms] == [4, 3, 1]          # Runde 2 (1v5, letzter Mann) faellt weg
    assert (ms[0]["tick"], ms[0]["weapon"], ms[0]["opponent"]) == (4000, "ak47", "foe")
    assert cm.for_match(_export(), OWN, top=1)[0]["cost"] == 30.2
    assert cm.for_match({"match": {}}, OWN) == []


def test_text_lines_carry_every_number_and_the_demo_tick():
    ms = cm.for_match(_export(), OWN)
    assert cm.describe(ms[1]) == ("Runde 3 als T in Bombsite B im 3 gegen 2 (Bombe lag) nach 20.5 s: "
                                  "24 Punkte Siegchance (vorher 51 %)")
    assert cm.facts_line(ms).startswith("TEUERSTE TODE") and cm.facts_line([]) == ""
    assert cm.goto_tick(ms[0]) == 4000 - 5 * 64
    assert "`demo_gototick 3680`" in cm.notify_line(ms) and "Runde 4" in cm.notify_line(ms)
    assert cm.goto_tick({**ms[0], "tick": 0}) is None


def test_review_facts_and_message_include_the_costliest_death(monkeypatch):
    ms = cm.for_match(_export(), OWN)
    text = report_ai.facts({"match": {"map": "Mirage"}, "player": {}}, [], [], None, ms)
    assert cm.facts_line(ms) in text
    assert "TEUERSTE TODE" not in report_ai.facts({"match": {}, "player": {}}, [], [], None, [])
    sent = []
    monkeypatch.setattr(report_ai, "_send_text", lambda cfg, cp, msg, log: sent.append(msg))
    report_ai._notify({}, "x", {"match": {"map": "Mirage"}}, "Text.", None, print, ms)
    assert sent and sent[0].endswith(cm.notify_line(ms))


def test_training_puts_the_latest_match_first_then_the_dearest_deaths():
    sc = [SimpleNamespace(export=e, round=r) for e, r in
          [("new.json", 1), ("new.json", 2), ("old.json", 1), ("old.json", 2), ("old.json", 3)]]
    costs = {("new.json", 2): 20.0, ("new.json", 1): 5.0, ("old.json", 3): 40.0, ("old.json", 1): 0.0}
    assert training_server.duel_order(sc, "new.json", costs) == [1, 0, 4, 2, 3]
    assert training_server.duel_order(sc, "new.json", {}) == [0, 1, 2, 3, 4]   # ohne Preise wie bisher


def test_scenario_costs_map_exports_and_count_last_man_deaths_as_zero(tmp_path, monkeypatch):
    folder = tmp_path / "exports"
    folder.mkdir()
    (folder / "a_coach.json").write_text(json.dumps(_export()), encoding="utf-8")
    monkeypatch.setattr(cm, "priced_own_deaths", lambda *a, **k: OWN)
    assert cm.scenario_costs({}, tmp_path / "config.yaml", folder, "me") == {
        ("a_coach.json", 1): 15.6, ("a_coach.json", 2): 0.0, ("a_coach.json", 3): 24.0, ("a_coach.json", 4): 30.2}


def test_collect_reads_preferred_demos_first(tmp_path, monkeypatch):
    folder = tmp_path / "demos"
    folder.mkdir()
    for name in ("a", "b", "c"):
        (folder / f"{name}.dem").write_bytes(b"x")
    order = []
    monkeypatch.setattr(winprob, "scan_demo", lambda demo, parser_factory=None: order.append(demo.stem) or [])
    stats = {}
    winprob.collect({"demo_folder": str(folder)}, tmp_path / "config.yaml", max_new=1, prefer=["c"], stats=stats)
    assert order == ["c"] and stats == {"new": 1, "waiting": 2}
