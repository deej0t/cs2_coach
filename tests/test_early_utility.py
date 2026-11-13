"""Tests fuer "fruehe Granate im Full Buy" (utility_analysis, training, briefing_view)."""

from __future__ import annotations

from types import SimpleNamespace

from cs2_coach import briefing_view, training
from cs2_coach.utility_analysis import early_utility, early_utility_rounds


def export(rounds, grenades, result="Sieg"):
    """rounds: (buy, death_s, won); grenades: (round, s)."""
    return {
        "match": {"result": result, "total_rounds": len(rounds), "datetime": "2026-11-13 20:00"},
        "round_timeline": [{"round": i + 1, "buy": b, "death_s": d, "won": w}
                           for i, (b, d, w) in enumerate(rounds)],
        "utility_positions": [{"t": "h", "r": r, "s": s} for r, s in grenades],
        "player": {},
    }


def test_zaehlt_nur_full_buy_runden_die_du_ueberlebt_hast():
    e = export([("fullbuy", None, True),    # Granate nach 10 s -> zaehlt, frueh
                ("fullbuy", 50.0, False),   # Granate erst nach 40 s -> zaehlt, nicht frueh
                ("fullbuy", 20.0, False),   # vor 35 s gestorben -> zaehlt nicht
                ("eco", None, False)],      # Eco -> zaehlt nicht
               [(1, 10.0), (2, 40.0), (3, 5.0), (4, 3.0)])
    assert early_utility(e) == (1, 2)


def test_ohne_kaufart_keine_aussage():
    """Exporte vor Schema 6 duerfen nicht als 0 von n zaehlen."""
    e = export([("fullbuy", None, True)], [])
    for r in e["round_timeline"]:
        del r["buy"]
    assert early_utility(e) is None


def test_rundenebene_vergleicht_gewonnene_runden():
    e = export([("fullbuy", None, True)] * 30 + [("fullbuy", None, False)] * 30,
               [(r, 5.0) for r in range(1, 31)])
    c = early_utility_rounds([e])
    assert (c.mean_a, c.mean_b) == (100.0, 0.0)


def test_value_braucht_genug_runden():
    few = export([("fullbuy", None, True)] * (training.MIN_EARLY_ROUNDS - 1), [(1, 5.0)])
    enough = export([("fullbuy", None, True)] * 4, [(1, 5.0), (2, 5.0)])
    assert training._value(few, training.EARLY_KEY) is None
    assert training._value(enough, training.EARLY_KEY) == 50.0


def _matches(k_win, k_loss, n=8):
    wins = [export([("fullbuy", None, True)] * n, [(r, 5.0) for r in range(1, k_win + 1)])
            for _ in range(12)]
    losses = [export([("fullbuy", None, False)] * n, [(r, 5.0) for r in range(1, k_loss + 1)],
                     result="Niederlage") for _ in range(12)]
    # etwas Streuung, sonst ist die Standardabweichung 0
    wins[0]["utility_positions"].pop()
    losses[0]["utility_positions"].append({"t": "h", "r": n, "s": 5.0})
    return wins + losses


def test_ziel_nur_wenn_belastbar_und_dann_statt_der_zaehlziele():
    g = training._early_goal(_matches(6, 2))
    assert g is not None and g.key == training.EARLY_KEY and g.target == 75.0
    goals = training._candidate_goals(_matches(6, 2))
    assert [x.key for x in goals if x.key.startswith("util_") or x.key == "utility"] == []
    assert training._early_goal(_matches(4, 4)) is None


def test_briefing_zeigt_zaehlbare_runden():
    goal = SimpleNamespace(key=training.EARLY_KEY, label="", unit="%", target=60.0,
                           higher_is_better=True)
    e = export([("fullbuy", None, True)] * 5, [(1, 5.0), (2, 9.0), (3, 30.0)])
    text, done = briefing_view._goal_check(e, goal)
    assert text.startswith("⏱️ 3/5") and done
