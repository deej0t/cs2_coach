"""Ziel pro Training: schwaechste Gewohnheit waehlen, im Spiel zaehlen, mit Konfidenzintervall bewerten."""

from cs2_coach import training_goal as tg


def duels(n, **fields):
    return [dict(fields) for _ in range(n)]


def test_picks_weakest_measured_habit():
    sessions = [{"duels": duels(6, first_speed=150, seen_err=4) + duels(4, first_speed=20, seen_err=4)}]
    g = tg.pick_goal(sessions)
    assert g["key"] == "stopped" and (g["before_k"], g["before_n"]) == (4, 10)
    assert tg.pick_goal([{"duels": duels(5, first_speed=150)}]) is None  # zu wenige Duelle


def test_unmeasured_duel_does_not_count():
    assert tg.goal_ok("aim", {}) is None and tg.goal_ok("aim", {"seen_err": 4}) is True


def test_progress_needs_confidence_not_raw_difference():
    goal = {"key": "stopped", "label": "x", "before_k": 4, "before_n": 10}
    few = tg.progress(goal, duels(3, first_speed=10))
    assert few["pct"] == 100 and few["verdict"] == "offen"  # 3 Duelle beweisen nichts
    many = tg.progress({**goal, "before_k": 10, "before_n": 40}, duels(40, first_speed=10))
    assert many["verdict"] == "besser"
    assert "heute 40/40" in tg.progress_text(goal, many)
