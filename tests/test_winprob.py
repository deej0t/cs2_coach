"""Siegwahrscheinlichkeit aus dem Rundenzustand: Einlesen, Zustandsfolge, Tabelle, Pruefung."""

import pandas as pd

from cs2_coach import winprob as wp

CT = [f"7656119800000000{i}" for i in range(1, 6)]
T = [f"7656119800000001{i}" for i in range(1, 6)]


class FakeDemo:
    """Runde 1 (Freeze-Ende 100, Ende 2020): T1 stirbt bei 164, Bombe bei 740, CT1 stirbt bei 1380.
    Runde 2 (Freeze-Ende 3000): ein Bot steht auf dem Server -> faellt weg.
    Runde 3 (Freeze-Ende 5000, Ende 5640): nur der Tod nach dem Freeze-Ende zaehlt."""

    def __init__(self, path):
        pass

    def parse_event(self, name):
        if name == "round_freeze_end":
            return pd.DataFrame({"tick": [100, 3000, 5000]})
        if name == "round_end":
            return pd.DataFrame({"tick": [2020, 4000, 5640], "winner": ["T", "CT", "CT"]})
        if name == "player_death":
            return pd.DataFrame({"tick": [164, 1380, 4990, 5064],
                                 "user_steamid": [T[0], CT[0], CT[1], T[1]],
                                 "attacker_steamid": [CT[0], T[1], T[0], CT[2]]})
        if name == "bomb_planted":
            return pd.DataFrame({"tick": [740]})
        raise KeyError(name)

    def parse_ticks(self, props, ticks):
        rows = []
        for tick in ticks:
            for sid in CT + T:
                rows.append({"tick": tick, "steamid": sid, "team_num": 3 if sid in CT else 2,
                             "is_alive": True, "current_equip_value": 4000 if sid in CT else 1000})
            if tick == 3000:
                rows.append({"tick": tick, "steamid": "0", "team_num": 2, "is_alive": True,
                             "current_equip_value": 0})
        return pd.DataFrame(rows)


def _round(events, winner="CT", seconds=60.0, ct_equip=20000, t_equip=20000):
    return {"n": 1, "winner": winner, "seconds": seconds, "ct": list(CT), "t": list(T),
            "ct_equip": ct_equip, "t_equip": t_equip, "events": events}


def test_scan_reads_rounds_sides_and_events():
    first, third = wp.scan_demo("x.dem", parser_factory=FakeDemo)
    assert (first["n"], first["winner"], first["seconds"]) == (1, "T", 30.0)
    assert (first["ct"], first["t"]) == (CT, T)
    assert (first["ct_equip"], first["t_equip"]) == (20000, 5000)
    assert first["events"] == [[1.0, "d", T[0], CT[0]], [10.0, "p", "", ""], [20.0, "d", CT[0], T[1]]]
    assert third["n"] == 3 and third["events"] == [[1.0, "d", T[1], CT[2]]]  # der Tod im Freeze fehlt


def test_timeline_switches_clock_at_the_plant_and_stops_when_decided():
    rnd = _round([[5.0, "d", T[0], CT[0]], [40.0, "p", "", ""], [52.0, "d", CT[0], T[1]]]
                 + [[60.0 + i, "d", sid, T[1]] for i, sid in enumerate(CT[1:])]
                 + [[70.0, "d", T[1], ""]], winner="T", seconds=64.0)
    tl = wp.timeline(rnd)
    assert tl[0]["before"] == (5, 5, False, 5.0) and tl[0]["after"] == (5, 4, False, 5.0)
    assert tl[1]["kind"] == "p" and tl[1]["after"] == (5, 4, True, 0.0)
    assert tl[2]["side"] == "CT" and tl[2]["before"] == (5, 4, True, 12.0)
    assert len(tl) == 7 and tl[-1]["after"][:2] == (0, 4)  # der Tod nach dem Ende zaehlt nicht


def test_visited_cells_cover_every_time_bucket_once():
    rnd = _round([[50.0, "d", T[0], CT[0]], [80.0, "p", "", ""]], winner="T", seconds=120.0)
    assert wp.visited_cells(rnd) == {(5, 5, 0, 0), (5, 5, 0, 1), (5, 4, 0, 1), (5, 4, 0, 2),
                                     (5, 4, 1, 0), (5, 4, 1, 1), (5, 4, 1, 2)}
    wipe = _round([[1.0 + i, "d", sid, CT[0]] for i, sid in enumerate(T)])
    assert all(t > 0 for _, t, _, _ in wp.visited_cells(wipe))  # 5v0 ist entschieden, keine Zelle


def test_table_knows_decided_states_and_shrinks_thin_cells():
    rounds = [(0.0, 1, [(5, 4, 0, 0)])] * 30 + [(0.0, 0, [(5, 4, 0, 0)])] * 10 + [(0.0, 1, [(5, 4, 0, 3)])]
    table = wp.WinTable(rounds, use_equip=False)
    assert table.p_ct(0, 3, True) == 0.0 and table.p_ct(2, 0, False) == 1.0
    assert 0.0 < table.p_ct(2, 0, True) < 1.0  # Bombe liegt: muss noch entschaerft werden
    assert round(table.p_ct(5, 4, False, 10), 2) == 0.75
    assert 0.75 < table.p_ct(5, 4, False, 110) < 0.8  # eine Runde zieht kaum
    assert table.p_side("T", 5, 4, False, 10) == 1 - table.p_ct(5, 4, False, 10)


def test_equipment_shifts_the_table_around_its_neutral_point():
    cell = [(5, 5, 0, 0)]
    rounds = ([(2.0, 1, cell)] * 80 + [(2.0, 0, cell)] * 20 + [(-2.0, 1, cell)] * 20 + [(-2.0, 0, cell)] * 80
              + [(1.0, 1, cell)] * 60 + [(1.0, 0, cell)] * 40)
    table = wp.WinTable(rounds)
    assert table.equip_slope > 0.3
    assert table.p_cell((5, 5, 0, 0), 2.0) > 0.7 > table.p_cell((5, 5, 0, 0)) > 0.3 > table.p_cell((5, 5, 0, 0), -2.0)
    assert abs(table.p_cell((5, 5, 0, 0), table.equip_neutral) - table.p_cell((5, 5, 0, 0))) < 1e-9
    # mit weniger Lebenden wirkt derselbe Vorteil schwaecher
    assert abs(wp._logit(table.p_cell((1, 1, 0, 0), 2.0)) - wp._logit(table.p_cell((1, 1, 0, 0)))) < \
        abs(wp._logit(table.p_cell((5, 5, 0, 0), 2.0)) - wp._logit(table.p_cell((5, 5, 0, 0))))


def _matches(n=40):
    """CT gewinnt die Haelfte; wer zuerst einen Spieler verliert, 20 Punkte weniger, reiche CT 20 mehr."""
    out = []
    for m in range(n):
        rounds = []
        for i in range(20):
            j = m * 20 + i
            ct_dies, rich = j % 2 == 0, (j // 2) % 2 == 0
            chance = 50 + (-20 if ct_dies else 20) + (20 if rich else -20)
            rounds.append(_round([[20.0, "d", CT[0] if ct_dies else T[0], ""]], seconds=90.0,
                                 winner="CT" if (j * 37) % 100 < chance else "T",
                                 ct_equip=30000 if rich else 10000))
        out.append({"demo": f"d{m:02d}", "rounds": rounds})
    return out


def test_calibration_predicts_each_match_from_the_others():
    c = wp.calibration(_matches())
    assert (c["matches"], c["rounds"]) == (40, 800)
    m = c["models"]
    assert m["base"]["n"] == m["equip"]["n"] > 800
    assert m["equip"]["brier"] < m["alive"]["brier"] < m["base"]["brier"]
    assert c["equip_error"] < c["equip_error_without"]
    assert sum(b["n"] for b in c["bins"]) == m["equip"]["n"]
    assert c["equip_slope"] > 0


def test_swings_charge_a_death_to_the_side_of_the_victim():
    matches = _matches()
    table = wp.fit(matches)
    rnd = _round([[20.0, "d", CT[0], T[2]], [30.0, "d", T[2], CT[1]]], winner="T", seconds=90.0)
    first, second = wp.swings(rnd, table)
    assert (first["sid"], first["by"], first["side"], first["own"], first["enemy"]) == (CT[0], T[2], "CT", 5, 5)
    assert first["cost"] > 0 and first["won"] is False
    assert (second["side"], second["own"], second["enemy"], second["won"]) == ("T", 5, 4, True)
    assert second["cost"] > 0


def test_collect_caches_each_demo(tmp_path):
    folder = tmp_path / "demos"
    folder.mkdir()
    (folder / "a.dem").write_bytes(b"x")
    calls = []

    def factory(path):
        calls.append(path)
        return FakeDemo(path)

    cfg = {"demo_folder": str(folder)}
    first = wp.collect(cfg, tmp_path / "config.yaml", parser_factory=factory, log=lambda *_: None)
    again = wp.collect(cfg, tmp_path / "config.yaml", parser_factory=factory, log=lambda *_: None)
    assert len(calls) == 1 and first == again and first[0]["demo"] == "a" and len(first[0]["rounds"]) == 2
    assert (tmp_path / wp.CACHE_DIR / "a.json").exists()
