"""Tode einordnen: getradet, Abstand zum Mitspieler, erster Tod der Runde, Runde gewonnen."""

import pandas as pd

from cs2_coach import death_review as dr

ME, MATE, K1, K2 = "1", "2", "9", "8"


class FakeDemo:
    """Runde 1 (Freeze-Ende 100): K1 toetet dich bei 300, MATE toetet K1 bei 400 -> getradet.
    Runde 2 (Freeze-Ende 1000): erst stirbt MATE (1100), dann du durch K2 bei 1200 - nicht getradet."""

    def __init__(self, path):
        pass

    def parse_event(self, name):
        if name == "player_death":
            return pd.DataFrame({"tick": [300, 400, 1100, 1200],
                                 "user_steamid": [ME, K1, MATE, ME], "attacker_steamid": [K1, MATE, K2, K2]})
        if name == "round_end":
            return pd.DataFrame({"tick": [900, 1800], "winner": ["CT", "T"]})
        return pd.DataFrame({"tick": [100, 1000]})

    def parse_ticks(self, props, ticks):
        rows = []
        for t in ticks:
            for sid, team, x, alive in [(ME, 3, 0, True), (MATE, 3, 400 if t < 1000 else 3000, t < 1100),
                                        (K1, 2, 900, True), (K2, 2, 1500, True)]:
                rows.append({"tick": t, "steamid": sid, "X": float(x), "Y": 0.0, "Z": 0.0,
                             "team_num": team, "is_alive": alive,
                             "inventory": ["AK-47", "Molotov", "Smoke Grenade", "Decoy Grenade"] if t == 299 else []})
        return pd.DataFrame(rows)


def test_classifies_each_death():
    a, b = dr.scan_demo("x.dem", ME, parser_factory=FakeDemo)
    assert (a["traded"], a["mate_dist"], a["opening"], a["won"], a["second"]) == (True, 400, True, True, 3.1)
    assert (b["traded"], b["alone"], b["opening"], b["won"]) == (False, True, False, False)
    assert (a["nades"], b["nades"]) == (2, 0)  # Molotov + Smoke, Decoy zaehlt nicht


def test_summary_compares_wins_and_losses():
    ds = [{"traded": i < 30, "opening": False, "mate_dist": 300, "result": "Sieg", "map": "Mirage"} for i in range(60)]
    ds += [{"traded": i < 10, "opening": True, "mate_dist": 1500, "result": "Niederlage", "map": "Mirage"}
           for i in range(60)]
    s = dr.summary(ds)
    assert s["wins"]["traded"] == 30 and s["losses"]["far"] == 60
    assert s["traded_cmp"].verdict == "solid_positive"
